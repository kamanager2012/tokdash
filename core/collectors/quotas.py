"""Core provider quota engine, common helpers, and multi-provider aggregator.

Specialized provider implementations have been extracted to dedicated modules:
- cursor.py: Cursor Composer session, usage, and quota
- grok_bot.py: Grok Bot activity and quota
- zai.py: Z.AI / GLM quota and model usage
- antigravity.py: Antigravity IDE and CLI loopback quota

All symbols are re-exported here for complete backward compatibility.
"""

import os
import json
import math
import hashlib
import threading
from datetime import datetime, date

from core.config import (
    _USER_DIR,
    PROVIDER_QUOTA_CACHE,
    RANGE_KEYS,
    _atomic_write_json,
    _load_json,
    classify_date,
    parse_ts,
    range_bounds,
)
from core.pricing import nice_model


def _tokei_config():
    cfg = _load_json(os.path.join(_USER_DIR, "config.json"), {})
    return cfg if isinstance(cfg, dict) else {}


# ---------- CodexBar-compatible provider quotas ----------
# Remote providers are opt-in. Antigravity is the exception: it only probes an
# already-running loopback language server and never starts the app/CLI.
_PROVIDER_QUOTA_TTL = 300
_PROVIDER_QUOTA_FALLBACK_TTL = 3600
_PROVIDER_QUOTA_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_PROVIDER_QUOTA_CACHE_LOCK = threading.Lock()
_PROVIDER_QUOTA_ENV = {
    "cursor": "TOKEI_CURSOR_QUOTA",
    "grok_bot": "TOKEI_GROK_BOT_QUOTA",
    "zed": "TOKEI_ZED_QUOTA",
    "sub2api": "TOKEI_SUB2API_QUOTA",
    "zai": "TOKEI_ZAI_QUOTA",
    "antigravity": "TOKEI_ANTIGRAVITY_QUOTA",
}


def _provider_quota_enabled(provider):
    env_key = _PROVIDER_QUOTA_ENV.get(provider)
    env = os.environ.get(env_key) if env_key else None
    if env == "0":
        return False
    if env == "1":
        return True
    default = provider == "antigravity"
    return bool(_tokei_config().get(f"{provider}_quota_enabled", default))


def _provider_config_string(env_key, config_key):
    value = os.environ.get(env_key)
    if not isinstance(value, str) or not value.strip():
        value = _tokei_config().get(config_key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _provider_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _provider_integer(value):
    number = _provider_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _provider_percent(value):
    number = _provider_number(value)
    return round(max(0.0, min(100.0, number)), 6) if number is not None else None


def _provider_epoch(value):
    number = _provider_number(value)
    if number is not None:
        if number > 100_000_000_000:
            number /= 1000.0
        return int(number) if number > 0 else None
    parsed = parse_ts(value) if isinstance(value, str) else None
    return int(parsed.timestamp()) if parsed else None


def _provider_money(value, unit="USD"):
    number = _provider_number(value)
    if number is None:
        return None
    if str(unit or "USD").upper() == "USD":
        return f"${number:,.2f}"
    return f"{number:,.2f} {unit}"


_PROVIDER_USAGE_FIELDS = ("in", "out", "cr", "cw", "reason")


def _provider_usage_int(value):
    number = _provider_integer(value)
    return number if number is not None and number >= 0 else 0


def _provider_usage_from_days(days, *, bounds=None, limited_coverage=None):
    bounds = bounds or range_bounds()
    buckets = {
        key: {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
              "reason": 0, "cost": 0.0, "requests": 0, "models": {}}
        for key in RANGE_KEYS
    }
    clean_days = days if isinstance(days, dict) else {}
    for day_key, day in clean_days.items():
        if not isinstance(day, dict):
            continue
        try:
            local_day = date.fromisoformat(str(day_key)[:10])
        except ValueError:
            continue
        components = {field: _provider_usage_int(day.get(field))
                      for field in _PROVIDER_USAGE_FIELDS}
        total = _provider_usage_int(day.get("tokens")) or sum(components.values())
        cost = _provider_number(day.get("cost")) or 0.0
        cost = cost if cost >= 0 else 0.0
        requests = _provider_usage_int(day.get("requests"))
        models = day.get("models") if isinstance(day.get("models"), dict) else {}
        for range_key in classify_date(local_day, bounds):
            bucket = buckets[range_key]
            bucket["tokens"] += total
            bucket["cost"] += cost
            bucket["requests"] += requests
            for field, value in components.items():
                bucket[field] += value
            for raw_name, raw_usage in models.items():
                if not isinstance(raw_usage, dict):
                    continue
                model = bucket["models"].setdefault(
                    str(raw_name or "unknown"),
                    {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                     "reason": 0, "cost": 0.0})
                model_components = {field: _provider_usage_int(raw_usage.get(field))
                                    for field in _PROVIDER_USAGE_FIELDS}
                model["tokens"] += _provider_usage_int(raw_usage.get("tokens")) \
                    or sum(model_components.values())
                for field, value in model_components.items():
                    model[field] += value
                model_cost = _provider_number(raw_usage.get("cost")) or 0.0
                if model_cost >= 0:
                    model["cost"] += model_cost

    ranges = {}
    for range_key, bucket in buckets.items():
        formatted = {}
        for raw_name, usage in bucket.pop("models").items():
            display_name = nice_model(raw_name)
            model = formatted.setdefault(
                display_name,
                {"name": display_name, "tokens": 0, "in": 0, "out": 0,
                 "cr": 0, "cw": 0, "reason": 0, "cost": 0.0})
            for field in ("tokens",) + _PROVIDER_USAGE_FIELDS:
                model[field] += usage[field]
            model["cost"] += usage["cost"]
        row = dict(bucket)
        input_total = row["in"] + row["cr"] + row["cw"]
        row["hit"] = row["cr"] / input_total * 100 if input_total else 0.0
        row["cost"] = round(row["cost"], 6)
        row["models"] = sorted(
            formatted.values(), key=lambda item: (-item["tokens"], item["name"]))
        for model in row["models"]:
            model["cost"] = round(model["cost"], 6)
        if limited_coverage and range_key in {"year", "all"}:
            row["coverage"] = limited_coverage
        ranges[range_key] = row
    return {"ranges": ranges, "days": clean_days}


def _provider_window(window_id, title, used_pct=None, reset=None, window_minutes=None,
                     detail=None, usage_known=True):
    return {
        "id": str(window_id),
        "title": str(title),
        "used_pct": _provider_percent(used_pct),
        "reset": _provider_epoch(reset),
        "window_minutes": int(window_minutes) if isinstance(window_minutes, (int, float))
        and window_minutes > 0 else None,
        "detail": str(detail) if detail else None,
        "usage_known": bool(usage_known),
    }


def _provider_credential_marker(provider, *parts):
    material = "\0".join(str(part or "") for part in (provider,) + parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cached_provider_quota(provider, marker, max_age, now_epoch=None, stale=False):
    with _PROVIDER_QUOTA_CACHE_LOCK:
        root = _load_json(PROVIDER_QUOTA_CACHE, {})
    entry = (root.get("providers") or {}).get(provider) if isinstance(root, dict) else None
    if not isinstance(entry, dict) or entry.get("marker") != marker:
        return None
    fetched_at = _provider_number(entry.get("fetched_at"))
    quota = entry.get("quota")
    now_epoch = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    if fetched_at is None or not isinstance(quota, dict):
        return None
    age = now_epoch - int(fetched_at)
    if age < -300 or age > max_age:
        return None
    out = json.loads(json.dumps(quota))
    if stale:
        out["stale"] = True
        out["source"] = "cache"
    return out


def _latest_cached_provider_quota(provider, max_age=None, now_epoch=None, stale=False):
    with _PROVIDER_QUOTA_CACHE_LOCK:
        root = _load_json(PROVIDER_QUOTA_CACHE, {})
    entry = (root.get("providers") or {}).get(provider) if isinstance(root, dict) else None
    if not isinstance(entry, dict):
        return None
    fetched_at = _provider_number(entry.get("fetched_at"))
    quota = entry.get("quota")
    now_epoch = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    if fetched_at is None or not isinstance(quota, dict):
        return None
    age = now_epoch - int(fetched_at)
    if age < -300 or (max_age is not None and age > max_age):
        return None
    out = json.loads(json.dumps(quota))
    if stale:
        out["stale"] = True
        out["source"] = "cache"
    return out


def _save_provider_quota_cache(provider, marker, quota, fetched_at=None):
    usage = quota.get("usage") if isinstance(quota, dict) else None
    usage_ranges = usage.get("ranges") if isinstance(usage, dict) else None
    has_usage = isinstance(usage_ranges, dict) and any(
        isinstance(row, dict) and (
            _provider_usage_int(row.get("tokens")) > 0
            or _provider_usage_int(row.get("requests")) > 0
        )
        for row in usage_ranges.values()
    )
    if not isinstance(quota, dict) or (not quota.get("available") and not has_usage):
        return
    with _PROVIDER_QUOTA_CACHE_LOCK:
        root = _load_json(PROVIDER_QUOTA_CACHE, {})
        if not isinstance(root, dict):
            root = {}
        providers = root.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        providers[provider] = {
            "marker": marker,
            "fetched_at": int(fetched_at if fetched_at is not None else datetime.now().timestamp()),
            "quota": quota,
        }
        root = {"version": 1, "providers": providers}
        try:
            _atomic_write_json(PROVIDER_QUOTA_CACHE, root)
            os.chmod(PROVIDER_QUOTA_CACHE, 0o600)
        except OSError:
            pass


def _provider_quota_recent_attempt_result(provider, marker, max_age, now_epoch=None):
    with _PROVIDER_QUOTA_CACHE_LOCK:
        root = _load_json(PROVIDER_QUOTA_CACHE, {})
    entry = (root.get("providers") or {}).get(provider) if isinstance(root, dict) else None
    if not isinstance(entry, dict) or entry.get("marker") != marker:
        return None
    attempted_at = _provider_number(entry.get("attempted_at"))
    if attempted_at is None:
        return None
    now_epoch = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    age = now_epoch - int(attempted_at)
    if not -300 <= age <= max_age:
        return None
    result = entry.get("attempt_result")
    return result if result in {"empty", "failed"} else "failed"


def _save_provider_quota_attempt(provider, marker, attempted_at=None, result="failed"):
    with _PROVIDER_QUOTA_CACHE_LOCK:
        root = _load_json(PROVIDER_QUOTA_CACHE, {})
        if not isinstance(root, dict):
            root = {}
        providers = root.get("providers")
        if not isinstance(providers, dict):
            providers = {}
        previous = providers.get(provider)
        entry = dict(previous) if isinstance(previous, dict) \
            and previous.get("marker") == marker else {"marker": marker}
        entry["attempted_at"] = int(
            attempted_at if attempted_at is not None else datetime.now().timestamp())
        entry["attempt_result"] = result if result in {"empty", "failed"} else "failed"
        providers[provider] = entry
        root = {"version": 1, "providers": providers}
        try:
            _atomic_write_json(PROVIDER_QUOTA_CACHE, root)
            os.chmod(PROVIDER_QUOTA_CACHE, 0o600)
        except OSError:
            pass


def _provider_json_request(url, *, headers=None, method="GET", body=None, timeout=5,
                           max_bytes=_PROVIDER_QUOTA_MAX_RESPONSE_BYTES,
                           allow_insecure_loopback_tls=False):
    import ssl
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    raw_body = None
    request_headers = dict(headers or {})
    if body is not None:
        raw_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request_headers.setdefault("Accept", "application/json")
    request = urllib.request.Request(
        url, data=raw_body, headers=request_headers, method=method)
    context = None
    parsed = urlsplit(url)
    if allow_insecure_loopback_tls and parsed.scheme == "https" \
            and parsed.hostname in {"127.0.0.1", "::1", "localhost"}:
        context = ssl._create_unverified_context()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, response_headers, new_url):
            return None

    handlers = [NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(f"HTTP {status}") from error
    if len(data) > max_bytes:
        raise ValueError("provider response too large")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("provider response was not valid JSON") from error


# ---------- Provider Quotas Multi-threaded Aggregator ----------


def scan_provider_quotas(errors=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.collectors.zai import scan_zai_quota
    from core.collectors.antigravity import scan_antigravity_quota
    from core.collectors.cursor import _cursor_session, fetch_cursor_quota
    from core.collectors.grok_bot import fetch_grok_bot_quota

    all_scans = {
        "zai": scan_zai_quota,
        "antigravity": scan_antigravity_quota,
    }
    result = {name: {} for name in (*all_scans, "cursor", "grok_bot")}
    scans = {name: scan for name, scan in all_scans.items()
             if _provider_quota_enabled(name)}
    cursor_enabled = _provider_quota_enabled("cursor")
    grok_bot_enabled = _provider_quota_enabled("grok_bot")
    if cursor_enabled or grok_bot_enabled:
        def cursor_bundle():
            session = _cursor_session()
            cursor_quota = fetch_cursor_quota(session) \
                if cursor_enabled and session else {}
            grok_bot_quota = fetch_grok_bot_quota() if grok_bot_enabled else {}
            return cursor_quota, grok_bot_quota
        scans["cursor_bundle"] = cursor_bundle
    if not scans:
        return result
    with ThreadPoolExecutor(max_workers=len(scans), thread_name_prefix="tokei-quota") as pool:
        futures = {pool.submit(scan): name for name, scan in scans.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                value = future.result()
                if name == "cursor_bundle":
                    cursor_quota, grok_bot_quota = value
                    if cursor_enabled:
                        result["cursor"] = cursor_quota
                    if grok_bot_enabled:
                        result["grok_bot"] = grok_bot_quota
                else:
                    result[name] = value if isinstance(value, dict) else {}
            except Exception as error:
                if errors is not None:
                    error_name = "cursor" if name == "cursor_bundle" else name
                    errors[f"{error_name}_quota"] = f"{type(error).__name__}: {error}"
    return result


# ---------- Backward-Compatibility Re-Exports ----------

from core.collectors.cursor import (
    _CURSOR_USAGE_PAGE_SIZE,
    _CURSOR_USAGE_MAX_PAGES,
    _cursor_app_auth_paths,
    _cursor_app_session,
    _cursor_cookie_session,
    _cursor_session,
    _cursor_plan_name,
    _normalize_cursor_usage_events,
    _cursor_boundary_overlap,
    _fetch_cursor_usage_events,
    _normalize_cursor_quota,
    fetch_cursor_quota,
    scan_cursor_quota,
    scan_cursor,
)

from core.collectors.grok_bot import (
    _grok_bot_active_account_id,
    _grok_bot_authorization_generation,
    _grok_bot_helper_path,
    _grok_bot_helper_sand_usage,
    _grok_bot_usage_from_bridge,
    _grok_bot_provider_data,
    _normalize_grok_bot_quota,
    _grok_bot_quota_from_cursor,
    _grok_bot_usage_only_fallback,
    fetch_grok_bot_quota,
    scan_grok_bot_quota,
)

from core.collectors.zai import (
    _local_timezone_name,
    _zai_quota_url,
    _zai_model_usage_url,
    _normalize_zai_model_usage,
    _zai_limit,
    _zai_limit_detail,
    _normalize_zai_quota,
    fetch_zai_quota,
    scan_zai_quota,
)

from core.collectors.antigravity import (
    _ANTIGRAVITY_SCAN_MISS_TTL,
    _antigravity_extract_flag,
    _antigravity_process_kind,
    _antigravity_process_infos,
    _antigravity_scan_recently_empty,
    _record_antigravity_scan,
    _antigravity_running_processes,
    _antigravity_listening_ports,
    _antigravity_endpoints,
    _antigravity_request,
    _antigravity_remaining,
    _normalize_antigravity_quota_summary,
    _normalize_antigravity_user_status,
    fetch_antigravity_quota,
    scan_antigravity_quota,
)

__all__ = [
    "_tokei_config",
    "_PROVIDER_QUOTA_TTL",
    "_PROVIDER_QUOTA_FALLBACK_TTL",
    "_PROVIDER_QUOTA_MAX_RESPONSE_BYTES",
    "_PROVIDER_QUOTA_CACHE_LOCK",
    "_PROVIDER_QUOTA_ENV",
    "_provider_quota_enabled",
    "_provider_config_string",
    "_provider_number",
    "_provider_integer",
    "_provider_percent",
    "_provider_epoch",
    "_provider_money",
    "_provider_usage_int",
    "_provider_usage_from_days",
    "_provider_window",
    "_provider_credential_marker",
    "_cached_provider_quota",
    "_latest_cached_provider_quota",
    "_save_provider_quota_cache",
    "_provider_quota_recent_attempt_result",
    "_save_provider_quota_attempt",
    "_provider_json_request",
    "scan_provider_quotas",
    "_CURSOR_USAGE_PAGE_SIZE",
    "_CURSOR_USAGE_MAX_PAGES",
    "_cursor_app_auth_paths",
    "_cursor_app_session",
    "_cursor_cookie_session",
    "_cursor_session",
    "_cursor_plan_name",
    "_normalize_cursor_usage_events",
    "_cursor_boundary_overlap",
    "_fetch_cursor_usage_events",
    "_normalize_cursor_quota",
    "fetch_cursor_quota",
    "scan_cursor_quota",
    "scan_cursor",
    "_grok_bot_active_account_id",
    "_grok_bot_authorization_generation",
    "_grok_bot_helper_path",
    "_grok_bot_helper_sand_usage",
    "_grok_bot_usage_from_bridge",
    "_grok_bot_provider_data",
    "_normalize_grok_bot_quota",
    "_grok_bot_quota_from_cursor",
    "_grok_bot_usage_only_fallback",
    "fetch_grok_bot_quota",
    "scan_grok_bot_quota",
    "_local_timezone_name",
    "_zai_quota_url",
    "_zai_model_usage_url",
    "_normalize_zai_model_usage",
    "_zai_limit",
    "_zai_limit_detail",
    "_normalize_zai_quota",
    "fetch_zai_quota",
    "scan_zai_quota",
    "_ANTIGRAVITY_SCAN_MISS_TTL",
    "_antigravity_extract_flag",
    "_antigravity_process_kind",
    "_antigravity_process_infos",
    "_antigravity_scan_recently_empty",
    "_record_antigravity_scan",
    "_antigravity_running_processes",
    "_antigravity_listening_ports",
    "_antigravity_endpoints",
    "_antigravity_request",
    "_antigravity_remaining",
    "_normalize_antigravity_quota_summary",
    "_normalize_antigravity_user_status",
    "fetch_antigravity_quota",
    "scan_antigravity_quota",
]
