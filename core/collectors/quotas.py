import os
import sys
import glob
import json
import math
import re
import hashlib
import sqlite3
import subprocess
import threading
import ssl
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.parse
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from core.config import (
    HOME,
    _USER_DIR,
    ANTIGRAVITY_SCAN_CACHE,
    GROK_BOT_AUTH_MARKER,
    GROK_BOT_SECRET_PATHS,
    PROVIDER_QUOTA_CACHE,
    RANGE_KEYS,
    _empty_token_ranges,
    _expand_path,
    _first_existing_file,
    _load_json,
    _atomic_write_json,
    _path_candidates,
    _sqlite_ro_uri,
    classify_date,
    parse_ts,
    range_bounds,
)
from core.pricing import nice_model
from core.storage import (
    _add_token_usage,
    ledger_touch,
)
from core.collectors.codex import _decode_jwt_claims


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
    """Return the latest aggregate, optionally enforcing a maximum age."""
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


# ----- Cursor -----

_CURSOR_USAGE_PAGE_SIZE = 1000
_CURSOR_USAGE_MAX_PAGES = 200


def _cursor_app_auth_paths():
    return _path_candidates(
        "TOKEI_CURSOR_AUTH_DB",
        os.path.join(HOME, "Library", "Application Support", "Cursor",
                     "User", "globalStorage", "state.vscdb"),
        os.path.join(HOME, ".config", "Cursor", "User",
                     "globalStorage", "state.vscdb"))


def _cursor_app_session(path=None, now_epoch=None):
    db_path = path or _first_existing_file(_cursor_app_auth_paths())
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        connection = sqlite3.connect(_sqlite_ro_uri(db_path), uri=True, timeout=0.25)
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
            ("cursorAuth/accessToken",)).fetchone()
        connection.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    token = row[0]
    if isinstance(token, bytes):
        token = token.decode("utf-8", "ignore")
    if not isinstance(token, str) or not token.strip():
        return None
    token = token.strip()
    claims = _decode_jwt_claims(token) or {}
    subject = claims.get("sub") if isinstance(claims.get("sub"), str) else None
    user_id = subject.rsplit("|", 1)[-1] if subject else None
    if not user_id or not re.fullmatch(r"[A-Za-z0-9._-]+", user_id):
        return None
    expiration = _provider_number(claims.get("exp"))
    now_epoch = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    if expiration is None or expiration <= now_epoch + 60:
        return None
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    return {
        "cookie": f"WorkosCursorSessionToken={user_id}%3A%3A{token}",
        "account": email.strip() if email and email.strip() else subject,
        "subject": subject,
        "user_id": user_id,
        "marker": _provider_credential_marker("cursor", subject, token),
    }


def _cursor_cookie_session(cookie):
    if not isinstance(cookie, str) or not cookie.strip():
        return None
    from urllib.parse import unquote

    account = subject = user_id = None
    for component in cookie.split(";"):
        name, separator, value = component.strip().partition("=")
        if separator and name == "WorkosCursorSessionToken":
            token = unquote(value).split("::")[-1]
            claims = _decode_jwt_claims(token) or {}
            subject = claims.get("sub") if isinstance(claims.get("sub"), str) else None
            user_id = subject.rsplit("|", 1)[-1] if subject else None
            email = claims.get("email")
            account = email.strip() if isinstance(email, str) and email.strip() else subject
            break
    return {
        "cookie": cookie.strip(),
        "account": account,
        "subject": subject,
        "user_id": user_id,
        "marker": _provider_credential_marker("cursor", cookie.strip()),
    }


def _cursor_session():
    manual = _provider_config_string("TOKEI_CURSOR_COOKIE", "cursor_cookie")
    return _cursor_cookie_session(manual) if manual else _cursor_app_session()


def _cursor_plan_name(raw):
    names = {
        "enterprise": "Enterprise", "express": "Start", "free": "Free",
        "free_trial": "Pro Trial", "hobby": "Hobby", "pro": "Pro",
        "pro_student": "Pro", "pro_plus": "Pro+", "team": "Team",
        "ultra": "Ultra",
    }
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = names.get(raw.strip().lower(), raw.strip())
    return f"Cursor {value}"


def _normalize_cursor_usage_events(events, *, bounds=None):
    days = {}
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        timestamp = _provider_number(event.get("timestamp"))
        usage = event.get("tokenUsage")
        if timestamp is None or timestamp <= 0 or not isinstance(usage, dict):
            continue
        timestamp_seconds = timestamp / 1000.0 if timestamp > 100_000_000_000 else timestamp
        try:
            dt = datetime.fromtimestamp(timestamp_seconds, timezone.utc).astimezone()
        except (OverflowError, OSError, ValueError):
            continue
        components = {
            "in": _provider_usage_int(usage.get("inputTokens")),
            "out": _provider_usage_int(usage.get("outputTokens")),
            "cr": _provider_usage_int(usage.get("cacheReadTokens")),
            "cw": _provider_usage_int(usage.get("cacheWriteTokens")),
            "reason": 0,
        }
        total = sum(components.values())
        if total <= 0:
            continue
        cents = _provider_number(usage.get("totalCents"))
        cost = cents / 100.0 if cents is not None and cents >= 0 else 0.0
        model_name = event.get("model")
        model_name = model_name.strip() if isinstance(model_name, str) and model_name.strip() else "unknown"
        day_key = dt.date().isoformat()
        day = days.setdefault(
            day_key,
            {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
             "reason": 0, "cost": 0.0, "requests": 0, "models": {},
             "hours": [0] * 24})
        day["tokens"] += total
        day["cost"] += cost
        day["requests"] += 1
        day["hours"][dt.hour] += total
        for field, value in components.items():
            day[field] += value
        model = day["models"].setdefault(
            model_name,
            {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
             "reason": 0, "cost": 0.0})
        model["tokens"] += total
        model["cost"] += cost
        for field, value in components.items():
            model[field] += value
    return _provider_usage_from_days(days, bounds=bounds)


def _cursor_boundary_overlap(previous, current):
    limit = min(len(previous), len(current))
    for count in range(limit, 0, -1):
        if previous[-count:] == current[:count]:
            return count
    return 0


def _fetch_cursor_usage_events(session, now=None):
    now = now or datetime.now().astimezone()
    start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    headers = {"Cookie": session["cookie"], "Origin": "https://cursor.com"}
    pages = []
    expected_total = None
    completed = False
    for page_number in range(1, _CURSOR_USAGE_MAX_PAGES + 1):
        payload = _provider_json_request(
            "https://cursor.com/api/dashboard/get-filtered-usage-events",
            headers=headers, method="POST", timeout=15, max_bytes=16 * 1024 * 1024,
            body={
                "page": page_number,
                "pageSize": _CURSOR_USAGE_PAGE_SIZE,
                "startDate": str(int(start.timestamp() * 1000)),
                "endDate": str(int(now.timestamp() * 1000)),
            })
        reported = _provider_integer(payload.get("totalUsageEventsCount")) \
            if isinstance(payload, dict) else None
        if reported is not None and reported < 0:
            raise ValueError("cursor usage event count cannot be negative")
        if reported is not None:
            if expected_total is not None and reported != expected_total:
                raise ValueError("cursor usage pagination count changed")
            expected_total = reported
        events = payload.get("usageEventsDisplay") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            raise ValueError("cursor usage events response was invalid")
        if not events:
            completed = True
            break
        pages.append(events)
        if len(events) < _CURSOR_USAGE_PAGE_SIZE:
            completed = True
            break
    if not completed:
        raise ValueError("cursor usage pagination did not complete")
    raw = [event for page in pages for event in page]
    if expected_total is None:
        return raw
    if len(raw) < expected_total:
        raise ValueError("cursor usage pagination was incomplete")
    if len(raw) == expected_total:
        return raw
    removals = len(raw) - expected_total
    reconciled = list(pages[0]) if pages else []
    for index in range(1, len(pages)):
        overlap = min(_cursor_boundary_overlap(pages[index - 1], pages[index]), removals)
        reconciled.extend(pages[index][overlap:])
        removals -= overlap
    if removals or len(reconciled) != expected_total:
        raise ValueError("cursor usage pagination was inconsistent")
    return reconciled


def _normalize_cursor_quota(summary, *, request_usage=None, sand_usage=None, user_info=None,
                            identity=None, updated=None):
    if not isinstance(summary, dict):
        return {}
    individual = summary.get("individualUsage") if isinstance(
        summary.get("individualUsage"), dict) else {}
    team = summary.get("teamUsage") if isinstance(summary.get("teamUsage"), dict) else {}
    plan = individual.get("plan") if isinstance(individual.get("plan"), dict) else {}
    overall = individual.get("overall") if isinstance(individual.get("overall"), dict) else {}
    pooled = team.get("pooled") if isinstance(team.get("pooled"), dict) else {}

    plan_used = _provider_number(plan.get("used")) or 0.0
    plan_limit = _provider_number(plan.get("limit")) or 0.0
    auto_pct = _provider_percent(plan.get("autoPercentUsed"))
    api_pct = _provider_percent(plan.get("apiPercentUsed"))
    total_pct = _provider_percent(plan.get("totalPercentUsed"))
    if total_pct is None and auto_pct is not None and api_pct is not None:
        total_pct = _provider_percent((auto_pct + api_pct) / 2)
    if total_pct is None:
        total_pct = api_pct if api_pct is not None else auto_pct
    if total_pct is None and plan_limit > 0:
        total_pct = _provider_percent(plan_used / plan_limit * 100)
    if total_pct is None:
        for block in (overall, pooled):
            used = _provider_number(block.get("used"))
            limit = _provider_number(block.get("limit"))
            if used is not None and limit is not None and limit > 0:
                total_pct = _provider_percent(used / limit * 100)
                plan_used, plan_limit = used, limit
                break
    total_pct = total_pct if total_pct is not None else 0.0

    request_block = request_usage.get("gpt-4") if isinstance(request_usage, dict) \
        and isinstance(request_usage.get("gpt-4"), dict) else {}
    requests_used = _provider_number(
        request_block.get("numRequestsTotal") if request_block.get("numRequestsTotal") is not None
        else request_block.get("numRequests"))
    requests_limit = _provider_number(request_block.get("maxRequestUsage"))
    legacy = requests_used is not None and requests_limit is not None and requests_limit > 0

    cycle_start = _provider_epoch(summary.get("billingCycleStart"))
    cycle_end = _provider_epoch(summary.get("billingCycleEnd"))
    window_minutes = int((cycle_end - cycle_start) / 60) \
        if cycle_start and cycle_end and cycle_end > cycle_start else None
    if legacy:
        total_pct = _provider_percent(requests_used / requests_limit * 100)
        primary_detail = f"{int(requests_used)} / {int(requests_limit)} requests"
    else:
        primary_detail = (
            f"{_provider_money(plan_used / 100)} / {_provider_money(plan_limit / 100)}"
            if plan_limit > 0 else None)

    windows = [_provider_window(
        "cursor-total", "总额度", total_pct, cycle_end, window_minutes, primary_detail)]
    if not legacy:
        if auto_pct is not None:
            windows.append(_provider_window(
                "cursor-auto", "Cursor 模型", auto_pct, cycle_end, window_minutes))
        if api_pct is not None:
            windows.append(_provider_window(
                "cursor-api", "第三方模型", api_pct, cycle_end, window_minutes))
    details = []
    if plan_limit > 0 and not legacy:
        details.append({
            "label": "套餐用量",
            "value": f"{_provider_money(plan_used / 100)} / {_provider_money(plan_limit / 100)}",
        })
    on_demand = individual.get("onDemand") if isinstance(individual.get("onDemand"), dict) else {}
    team_on_demand = team.get("onDemand") if isinstance(team.get("onDemand"), dict) else {}
    on_used = _provider_number(on_demand.get("used")) or 0.0
    on_limit = _provider_number(on_demand.get("limit"))
    if not on_limit or on_limit <= 0:
        team_limit = _provider_number(team_on_demand.get("limit"))
        if team_limit and team_limit > 0:
            on_used = _provider_number(team_on_demand.get("used")) or 0.0
            on_limit = team_limit
    if on_limit and on_limit > 0:
        details.append({
            "label": "按量预算",
            "value": f"{_provider_money(on_used / 100)} / {_provider_money(on_limit / 100)}",
        })

    user_info = user_info if isinstance(user_info, dict) else {}
    identity = identity if isinstance(identity, dict) else {}
    account = user_info.get("email") or identity.get("account")
    return {
        "available": True,
        "plan": _cursor_plan_name(summary.get("membershipType")),
        "account": account,
        "windows": windows,
        "details": details,
        "source": "cursor-api",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
        # Backward-compatibility aliases for legacy frontend clients
        "percent_used": total_pct,
        "end": cycle_end,
        "total_spend": round(plan_used / 100.0, 2),
        "included_spend": round(plan_limit / 100.0, 2),
        "bonus_spend": 0.0,
    }


def _grok_bot_active_account_id(path=None):
    secrets_path = path or _first_existing_file(GROK_BOT_SECRET_PATHS)
    if not secrets_path or not os.path.isfile(secrets_path):
        return None
    try:
        if os.path.getsize(secrets_path) > _PROVIDER_QUOTA_MAX_RESPONSE_BYTES:
            return None
        outer = _load_json(secrets_path, {})
        encoded = outer.get("cursor-accounts") if isinstance(outer, dict) else None
        if not isinstance(encoded, str) or len(encoded) > _PROVIDER_QUOTA_MAX_RESPONSE_BYTES:
            return None
        container = json.loads(encoded)
    except (OSError, ValueError, TypeError):
        return None
    active = container.get("active") if isinstance(container, dict) else None
    accounts = container.get("accounts") if isinstance(container, dict) else None
    if not isinstance(active, str) or not re.fullmatch(r"[0-9a-f]{64}", active):
        return None
    return active if isinstance(accounts, dict) and isinstance(accounts.get(active), dict) else None


def _grok_bot_authorization_generation(path=None):
    marker = path or GROK_BOT_AUTH_MARKER
    if not marker:
        return None
    try:
        stat = os.stat(marker, follow_symlinks=False)
    except OSError:
        return None
    return stat.st_mtime_ns if os.path.isfile(marker) else None


def _grok_bot_helper_path():
    configured = os.environ.get("TOKEI_GROK_BOT_HELPER")
    candidates = [configured] if isinstance(configured, str) and configured.strip() else []
    if sys.platform == "darwin":
        candidates.append("/Applications/Tokei.app/Contents/MacOS/Tokei")
    for candidate in candidates:
        path = _expand_path(candidate)
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _grok_bot_helper_sand_usage():
    helper = _grok_bot_helper_path()
    if not helper:
        return None
    try:
        result = subprocess.run(
            [helper, "--grok-bot-data-json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=35,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > 16 * 1024 * 1024:
        return None
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _grok_bot_usage_from_bridge(payload):
    if not isinstance(payload, dict) or payload.get("usageFetched") is not True:
        return None
    events = payload.get("usageEventsDisplay")
    if not isinstance(events, list):
        return None
    unique = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            marker = json.dumps(event, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            continue
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(event)
    usage = _normalize_cursor_usage_events(unique)
    all_range = (usage.get("ranges") or {}).get("all") if isinstance(usage, dict) else None
    if isinstance(all_range, dict):
        all_range["coverage"] = "本年"
    return usage


def _grok_bot_provider_data(payload, *, updated=None):
    if not isinstance(payload, dict):
        return {}
    is_bridge_payload = "quotaFetched" in payload or "usageFetched" in payload
    sand_usage = payload.get("sandUsage") if is_bridge_payload else payload
    quota = _normalize_grok_bot_quota(
        sand_usage, updated=updated, source="grok-bot-api")
    usage = _grok_bot_usage_from_bridge(payload) if is_bridge_payload else None
    ranges = usage.get("ranges") if isinstance(usage, dict) else None
    has_usage = isinstance(ranges, dict) and any(
        isinstance(row, dict) and (
            _provider_usage_int(row.get("tokens")) > 0
            or _provider_usage_int(row.get("requests")) > 0
        )
        for row in ranges.values()
    )
    if has_usage:
        if not quota:
            quota = {
                "available": False,
                "plan": None,
                "account": None,
                "windows": [],
                "details": [],
                "source": "grok-bot-api",
                "updated": int(updated if updated is not None else datetime.now().timestamp()),
                "stale": False,
            }
        quota["usage"] = usage
    return quota


def _normalize_grok_bot_quota(sand_usage, *, user_info=None, identity=None, updated=None,
                              source="cursor-sand-api"):
    if not isinstance(sand_usage, dict):
        return {}
    used_pct = _provider_percent(sand_usage.get("usagePercent"))
    if used_pct is None or sand_usage.get("hasNonZeroIncludedLimit") is False:
        return {}
    period_start = _provider_epoch(sand_usage.get("currentPeriodStart"))
    reset = _provider_epoch(sand_usage.get("nextResetTimestampUtc"))
    window_minutes = int((reset - period_start) / 60) \
        if period_start and reset and reset > period_start else None
    plan = sand_usage.get("grokPlanLabel") or sand_usage.get("planLabel") \
        or sand_usage.get("plan")
    plan = plan.strip() if isinstance(plan, str) and plan.strip() else None
    return {
        "available": True,
        "plan": plan,
        "account": None,
        "windows": [_provider_window(
            "grok-bot-period", "本周期额度", used_pct, reset, window_minutes)],
        "details": [],
        "source": source,
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def _grok_bot_quota_from_cursor(cursor_quota):
    if not isinstance(cursor_quota, dict):
        return {}
    source_window = next((window for window in cursor_quota.get("windows", [])
                          if isinstance(window, dict)
                          and window.get("id") == "cursor-grok-bot"), None)
    if source_window is None:
        return {}
    window = dict(source_window)
    window["id"] = "grok-bot-period"
    window["title"] = "本周期额度"
    plan = window.pop("detail", None)
    return {
        "available": True,
        "plan": plan,
        "account": None,
        "windows": [window],
        "details": [],
        "source": "cursor-sand-api",
        "updated": cursor_quota.get("updated"),
        "stale": bool(cursor_quota.get("stale")),
    }


def fetch_cursor_quota(session=None, force=False):
    session = session or _cursor_session()
    if not session:
        return {}
    marker = _provider_credential_marker("cursor-usage-v1", session["marker"])
    if not force:
        cached = _cached_provider_quota("cursor", marker, _PROVIDER_QUOTA_TTL)
        if cached:
            return cached
    headers = {"Cookie": session["cookie"]}
    base = "https://cursor.com"
    try:
        summary = _provider_json_request(base + "/api/usage-summary", headers=headers)
        user_info = None
        try:
            user_info = _provider_json_request(base + "/api/auth/me", headers=headers)
        except Exception:
            pass
        request_usage = None
        user_id = (user_info or {}).get("sub") if isinstance(user_info, dict) else None
        user_id = user_id or session.get("subject") or session.get("user_id")
        if isinstance(user_id, str) and user_id:
            user_id = user_id.rsplit("|", 1)[-1]
            from urllib.parse import quote
            try:
                request_usage = _provider_json_request(
                    base + "/api/usage?user=" + quote(user_id, safe=""), headers=headers)
            except Exception:
                pass
        sand_usage = None
        try:
            sand_usage = _provider_json_request(
                base + "/api/dashboard/get-sand-usage-status",
                headers={**headers, "Origin": base}, method="POST", body={})
        except Exception:
            pass
        usage = _provider_usage_from_days({})
        try:
            usage = _normalize_cursor_usage_events(_fetch_cursor_usage_events(session))
        except Exception:
            pass
        quota = _normalize_cursor_quota(
            summary, request_usage=request_usage, sand_usage=sand_usage,
            user_info=user_info, identity=session)
        quota["usage"] = usage
        _save_provider_quota_cache("cursor", marker, quota)
        grok_bot_quota = _normalize_grok_bot_quota(
            sand_usage, user_info=user_info, identity=session)
        if grok_bot_quota:
            _save_provider_quota_cache("grok_bot", marker, grok_bot_quota)
        return quota
    except Exception:
        fallback = _cached_provider_quota(
            "cursor", marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True)
        if fallback:
            return fallback
        raise


def scan_cursor_quota():
    return fetch_cursor_quota() if _provider_quota_enabled("cursor") else {}


def scan_cursor(bounds, cache):
    ledger_touch("cursor")
    fc = cache.setdefault("cursor", {})
    B = _empty_token_ranges()

    # 扫描 Cursor 真实的 Agent 会话完整转录日志（记录每一轮交互与多轮累加 Context）
    transcript_files = glob.glob(os.path.join(HOME, ".cursor", "projects", "*", "agent-transcripts", "*", "*.jsonl"))
    if not transcript_files:
        return {"ranges": B, "model": "Composer 2.5"}

    import re
    ts_pattern = re.compile(r'<timestamp>[A-Za-z]+,\s+([A-Za-z]+)\s+(\d+),\s+(\d{4})')
    month_map = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
                 'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}

    # 真实 Composer 2.5 计费费率 ($ / 1M tokens)
    p_in = 5.0
    p_cr = 0.50
    p_out = 30.0

    stale = set(fc.keys())
    for f in transcript_files:
        stale.discard(f)
        try:
            st = os.stat(f)
        except OSError:
            continue
        sig = f"{st.st_mtime}:{st.st_size}"
        entry = fc.get(f)
        if isinstance(entry, dict) and entry.get("sig") == sig and "days" in entry and entry.get("version") == 3:
            continue

        sid = os.path.splitext(os.path.basename(f))[0]
        history_chars = []
        prev_ctx = 0
        current_day = None
        days = {}
        turns_count = 0

        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                for line in fh:
                    m = ts_pattern.search(line)
                    if m:
                        mon, day, yr = m.groups()
                        current_day = f"{yr}-{month_map.get(mon, mon)}-{int(day):02d}"

                    line_len = len(line)
                    history_chars.append(line_len)

                    if '"role":"assistant"' in line or '"role": "assistant"' in line:
                        if not current_day:
                            current_day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")

                        # Cursor 官方上下文窗口修剪（上限 150K Tokens）
                        ctx_c = 0
                        for sz in reversed(history_chars):
                            if ctx_c + sz > 150000 * 4:
                                break
                            ctx_c += sz

                        full_prompt = ctx_c // 4
                        cache_read = min(prev_ctx, full_prompt)
                        uncached = max(0, full_prompt - cache_read)
                        out_tok = max(1, line_len // 4)
                        cost = (uncached * p_in + cache_read * p_cr + out_tok * p_out) / 1e6

                        d = days.setdefault(current_day, {"in": 0, "cr": 0, "out": 0, "cost": 0.0, "turns": 0})
                        d["in"] += uncached
                        d["cr"] += cache_read
                        d["out"] += out_tok
                        d["cost"] += cost
                        d["turns"] += 1
                        turns_count += 1

                        prev_ctx = full_prompt
        except Exception:
            continue

        fc[f] = {
            "sig": sig,
            "version": 3,
            "sid": sid,
            "days": days,
            "turns": turns_count,
            "model": "Composer 2.5",
        }
        cache["_dirty"] = True

    for p in stale:
        fc.pop(p, None)
        cache["_dirty"] = True

    for f, entry in fc.items():
        if not isinstance(entry, dict):
            continue
        entry_days = entry.get("days")
        if not isinstance(entry_days, dict):
            continue
        for d_str, day_data in entry_days.items():
            try:
                day_date = date.fromisoformat(d_str)
            except (TypeError, ValueError):
                continue
            for rk in classify_date(day_date, bounds):
                bucket = B[rk]
                bucket["sessions"].add(entry.get("sid", f))
                _add_token_usage(bucket, day_data.get("in", 0), day_data.get("out", 0),
                                 cr=day_data.get("cr", 0),
                                 cost=day_data.get("cost", 0.0), model="Composer 2.5")
    return {
        "ranges": B,
        "model": "Composer 2.5",
        "estimated": True,
        "provenance": "heuristic_char_div_4",
    }


def _grok_bot_usage_only_fallback():
    cached = _latest_cached_provider_quota(
        "grok_bot", max_age=None, stale=True)
    usage = cached.get("usage") if isinstance(cached, dict) else None
    ranges = usage.get("ranges") if isinstance(usage, dict) else None
    if not isinstance(ranges, dict) or not any(
            isinstance(row, dict) and _provider_usage_int(row.get("tokens")) > 0
            for row in ranges.values()):
        return {}
    return {
        "available": False,
        "plan": None,
        "account": None,
        "windows": [],
        "details": [],
        "usage": usage,
        "source": "cache",
        "updated": cached.get("updated"),
        "stale": True,
    }


def fetch_grok_bot_quota(session=None):
    native_fallback = _latest_cached_provider_quota(
        "grok_bot", _PROVIDER_QUOTA_FALLBACK_TTL, stale=True) \
        or _grok_bot_usage_only_fallback()
    if session is None:
        account_id = _grok_bot_active_account_id()
        authorization_generation = _grok_bot_authorization_generation()
        if account_id and authorization_generation is not None:
            native_marker = _provider_credential_marker(
                "grok-bot-account-v1", account_id, authorization_generation)
            cached = _cached_provider_quota(
                "grok_bot", native_marker, _PROVIDER_QUOTA_TTL)
            if cached:
                return cached
            native_fallback = _cached_provider_quota(
                "grok_bot", native_marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True) \
                or native_fallback
            recent_attempt = _provider_quota_recent_attempt_result(
                "grok_bot", native_marker, _PROVIDER_QUOTA_TTL)
            if recent_attempt == "empty":
                return {}
            if recent_attempt is None:
                payload = _grok_bot_helper_sand_usage()
                if payload is not None:
                    quota = _grok_bot_provider_data(
                        payload, updated=payload.get("updated"))
                    if quota:
                        _save_provider_quota_cache("grok_bot", native_marker, quota)
                        return quota
                    if payload.get("quotaFetched") is True or "quotaFetched" not in payload:
                        _save_provider_quota_attempt(
                            "grok_bot", native_marker, result="empty")
                        return {}
                _save_provider_quota_attempt("grok_bot", native_marker)

    session = session or _cursor_session()
    if not session:
        return native_fallback
    marker = _provider_credential_marker("cursor-usage-v1", session["marker"])
    cached = _cached_provider_quota("grok_bot", marker, _PROVIDER_QUOTA_TTL)
    if cached:
        return cached
    cursor_quota = fetch_cursor_quota(session, force=True)
    cached = _cached_provider_quota("grok_bot", marker, _PROVIDER_QUOTA_TTL)
    if cached:
        return cached
    quota = _grok_bot_quota_from_cursor(cursor_quota)
    if quota:
        _save_provider_quota_cache("grok_bot", marker, quota)
        return quota
    return _cached_provider_quota(
        "grok_bot", marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True) or native_fallback


def scan_grok_bot_quota():
    return fetch_grok_bot_quota() if _provider_quota_enabled("grok_bot") else {}


# ----- Zed -----


# ----- sub2api -----


def _local_timezone_name():
    configured = os.environ.get("TZ")
    if configured and configured.strip():
        return configured.strip()
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    zone = datetime.now().astimezone().tzinfo
    return getattr(zone, "key", None) or datetime.now().astimezone().tzname() or "UTC"


# ----- z.ai / GLM -----


def _zai_quota_url(url, scope="personal"):
    if scope != "team":
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key != "type"]
    query.append(("type", "2"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _zai_model_usage_url(base, scope="personal", now=None):
    from urllib.parse import urlencode
    now = now or datetime.now().astimezone()
    start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(minute=59, second=59, microsecond=0)
    query = {
        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if scope == "team":
        query["type"] = "3"
    return base.rstrip("/") + "/api/monitor/usage/model-usage?" + urlencode(query)


def _normalize_zai_model_usage(payload, *, bounds=None):
    if not isinstance(payload, dict) or payload.get("success") is not True \
            or _provider_number(payload.get("code")) != 200:
        return _provider_usage_from_days({}, bounds=bounds, limited_coverage="近30天")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    labels = data.get("x_time") if isinstance(data.get("x_time"), list) else []
    models = data.get("modelDataList") \
        if isinstance(data.get("modelDataList"), list) else []
    parsed_labels = []
    for label in labels:
        if not isinstance(label, str):
            parsed_labels.append(None)
            continue
        try:
            parsed_labels.append(datetime.fromisoformat(label).astimezone())
        except ValueError:
            parsed_labels.append(None)
    days = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        name = model.get("modelName")
        name = name.strip() if isinstance(name, str) and name.strip() else "unknown"
        values = model.get("tokensUsage") if isinstance(model.get("tokensUsage"), list) else []
        for index, dt in enumerate(parsed_labels):
            if dt is None or index >= len(values):
                continue
            tokens = _provider_usage_int(values[index])
            if tokens <= 0:
                continue
            day_key = dt.date().isoformat()
            day = days.setdefault(
                day_key,
                {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                 "reason": 0, "cost": 0.0, "requests": 0, "models": {},
                 "hours": [0] * 24})
            day["tokens"] += tokens
            day["hours"][dt.hour] += tokens
            usage = day["models"].setdefault(name, {"tokens": 0})
            usage["tokens"] += tokens
    return _provider_usage_from_days(days, bounds=bounds, limited_coverage="近30天")


def _zai_limit(raw):
    if not isinstance(raw, dict) or raw.get("type") not in {
            "TOKENS_LIMIT", "CREDIT_LIMIT", "TIME_LIMIT"}:
        return None
    unit = _provider_integer(raw.get("unit"))
    number = _provider_integer(raw.get("number"))
    percent_raw = _provider_integer(raw.get("percentage"))
    percent = _provider_percent(percent_raw)
    if unit is None or number is None or percent is None:
        return None
    usage = _provider_number(raw.get("usage"))
    current = _provider_number(raw.get("currentValue"))
    remaining = _provider_number(raw.get("remaining"))
    if usage is not None and usage > 0:
        used = None
        if remaining is not None:
            used = max(usage - remaining, current if current is not None else usage - remaining)
        elif current is not None:
            used = current
        if used is not None:
            percent = _provider_percent(max(0, min(usage, used)) / usage * 100)
    multipliers = {1: 1440, 3: 60, 5: 1, 6: 10080}
    unit_int, number_int = unit, number
    minutes = number_int * multipliers[unit_int] \
        if number_int > 0 and unit_int in multipliers else None
    if raw.get("type") == "TIME_LIMIT" and unit_int == 5 and number_int == 1:
        minutes = 30 * 24 * 60
    details = raw.get("usageDetails") if isinstance(raw.get("usageDetails"), list) else []
    return {
        "raw": raw, "usage": usage, "current": current, "remaining": remaining,
        "percent": percent, "window_minutes": minutes,
        "reset": _provider_epoch(raw.get("nextResetTime")), "details": details,
    }


def _zai_limit_detail(limit):
    parts = []
    if limit.get("usage") is not None:
        parts.append(f"{limit['usage']:g} limit")
    if limit.get("remaining") is not None:
        parts.append(f"{limit['remaining']:g} remaining")
    return " · ".join(parts) or None


def _normalize_zai_quota(payload, *, region="global", balance=None, updated=None):
    if not isinstance(payload, dict) or payload.get("success") is not True \
            or _provider_number(payload.get("code")) != 200:
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    limits = [_zai_limit(item) for item in data.get("limits", [])]
    limits = [item for item in limits if item]
    token_limits = sorted(
        [item for item in limits if item["raw"].get("type") in {"TOKENS_LIMIT", "CREDIT_LIMIT"}],
        key=lambda item: item.get("window_minutes") or sys.maxsize)
    time_limits = [item for item in limits if item["raw"].get("type") == "TIME_LIMIT"]
    token_limit = token_limits[-1] if token_limits else None
    session_limit = token_limits[0] if len(token_limits) >= 2 else None
    time_limit = time_limits[-1] if time_limits else None
    primary = session_limit or token_limit or time_limit
    windows = []
    if primary:
        windows.append(_provider_window(
            "zai-primary", "会话额度" if session_limit else "额度", primary["percent"],
            primary.get("reset"), primary.get("window_minutes"), _zai_limit_detail(primary)))
    if session_limit and token_limit:
        windows.append(_provider_window(
            "zai-secondary", "周期额度", token_limit["percent"], token_limit.get("reset"),
            token_limit.get("window_minutes"), _zai_limit_detail(token_limit)))
    if time_limit and (token_limit or session_limit):
        windows.append(_provider_window(
            "zai-mcp", "MCP", time_limit["percent"], time_limit.get("reset"),
            time_limit.get("window_minutes"), _zai_limit_detail(time_limit)))

    details = []
    if token_limit:
        label = "Credit quota" if token_limit["raw"].get("type") == "CREDIT_LIMIT" else "Token quota"
        details.append({"label": label, "value": f"{token_limit['percent']:g}% used",
                        "secondary": _zai_limit_detail(token_limit)})
    if session_limit:
        label = "Session credit quota" if session_limit["raw"].get("type") == "CREDIT_LIMIT" \
            else "Session token quota"
        details.append({"label": label, "value": f"{session_limit['percent']:g}% used",
                        "secondary": _zai_limit_detail(session_limit)})
    if time_limit:
        details.append({"label": "MCP quota", "value": f"{time_limit['percent']:g}% used",
                        "secondary": _zai_limit_detail(time_limit)})
        for item in time_limit.get("details", [])[:20]:
            if isinstance(item, dict) and isinstance(item.get("modelCode"), str):
                value = _provider_number(item.get("usage"))
                if value is not None and value >= 0:
                    details.append({"label": item["modelCode"], "value": f"{int(value):,}"})

    if region == "bigmodel-cn" and isinstance(balance, dict) and balance.get("success") is True:
        balance_data = balance.get("data") if isinstance(balance.get("data"), dict) else {}
        available = _provider_number(balance_data.get("availableBalance"))
        current = _provider_number(balance_data.get("balance"))
        amount = available if available is not None else current
        if amount is not None:
            secondary = []
            for key, label in (("rechargeAmount", "recharged"),
                               ("giveAmount", "granted"),
                               ("totalSpendAmount", "spent")):
                value = _provider_number(balance_data.get(key))
                if value is not None and (key != "giveAmount" or value > 0):
                    secondary.append(f"{label} ¥{value:.2f}")
            row = {"label": "Account balance", "value": f"¥{amount:.2f}"}
            if secondary:
                row["secondary"] = " · ".join(secondary)
            details.append(row)

    plan_name = next((data.get(key).strip() for key in (
        "planName", "plan", "plan_type", "packageName", "level")
        if isinstance(data.get(key), str) and data.get(key).strip()), None)
    return {
        "available": bool(windows or details or plan_name),
        "plan": plan_name,
        "account": None,
        "windows": windows,
        "details": details,
        "source": "zai-api",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def fetch_zai_quota():
    region = (_provider_config_string("Z_AI_REGION", "zai_region") or "global").lower()
    if region not in {"global", "bigmodel-cn"}:
        return {}
    scope = (_provider_config_string("Z_AI_USAGE_SCOPE", "zai_usage_scope") or "personal").lower()
    if scope not in {"personal", "team"}:
        return {}
    api_key = _provider_config_string("Z_AI_API_KEY", "zai_api_key")
    if not api_key and region == "bigmodel-cn":
        api_key = _provider_config_string("BIGMODEL_API_KEY", "zai_api_key")
    organization = _provider_config_string("Z_AI_ORGANIZATION", "zai_organization")
    project = _provider_config_string("Z_AI_PROJECT", "zai_project")
    if not api_key or (scope == "team" and (not organization or not project)):
        return {}
    base = "https://open.bigmodel.cn" if region == "bigmodel-cn" else "https://api.z.ai"
    quota_url = _zai_quota_url(base + "/api/monitor/usage/quota/limit", scope)
    marker = _provider_credential_marker(
        "zai-usage-v1", api_key, region, scope, organization, project, quota_url)
    cached = _cached_provider_quota("zai", marker, _PROVIDER_QUOTA_TTL)
    if cached:
        return cached
    headers = {"Authorization": f"Bearer {api_key}"}
    if scope == "team":
        headers["Bigmodel-Organization"] = organization
        headers["Bigmodel-Project"] = project
    try:
        payload = _provider_json_request(quota_url, headers=headers)
        balance = None
        if region == "bigmodel-cn":
            try:
                balance = _provider_json_request(
                    "https://www.bigmodel.cn/api/biz/account/query-customer-account-report",
                    headers=headers, timeout=5)
            except Exception:
                pass
        quota = _normalize_zai_quota(payload, region=region, balance=balance)
        usage = _provider_usage_from_days({}, limited_coverage="近30天")
        try:
            model_payload = _provider_json_request(
                _zai_model_usage_url(base, scope), headers=headers, timeout=15,
                max_bytes=8 * 1024 * 1024)
            usage = _normalize_zai_model_usage(model_payload)
        except Exception:
            pass
        quota["usage"] = usage
        _save_provider_quota_cache("zai", marker, quota)
        return quota
    except Exception:
        fallback = _cached_provider_quota(
            "zai", marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True)
        if fallback:
            return fallback
        raise


def scan_zai_quota():
    return fetch_zai_quota() if _provider_quota_enabled("zai") else {}


# ----- Antigravity loopback quota -----

# 没装/没开 Antigravity 时 ps 全表扫描恒为空,但每 30 秒一个 tick 都要付一次
# spawn(30ms+)。扫空后在这段时间内不重扫;90 秒也把新启动 Antigravity 的
# 发现延迟压在两三个 tick 内。
_ANTIGRAVITY_SCAN_MISS_TTL = 90


def _antigravity_extract_flag(command, flag):
    match = re.search(re.escape(flag) + r"(?:=|\s+)([^\s]+)", command, re.I)
    return match.group(1) if match else None


def _antigravity_process_kind(command):
    lower = command.lower()
    language_server = re.search(
        r"(^|[/\\])language(?:_|-)server(?:[_-][a-z0-9]+)*(?:\.exe)?(?:\s|$)", lower)
    app_match = ("--app_data_dir" in lower and "antigravity" in lower) or any(
        marker in lower for marker in ("antigravity.app/", "/gemini.app/",
                                       "antigravity ide.app/"))
    if language_server and app_match:
        return "ide"
    if re.search(r"(^|[/\\])(antigravity-cli|antigravity_cli|agy)(?:\s|[/\\]|$)", lower):
        return "cli"
    return None


def _antigravity_process_infos(output):
    results = []
    for line in str(output or "").splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid, command = int(match.group(1)), match.group(2)
        kind = _antigravity_process_kind(command)
        if not kind:
            continue
        csrf = _antigravity_extract_flag(command, "--csrf_token")
        if kind != "cli" and not csrf:
            continue
        extension_port = _provider_integer(
            _antigravity_extract_flag(command, "--extension_server_port"))
        if extension_port is not None and not 0 < extension_port <= 65535:
            extension_port = None
        results.append({
            "pid": pid, "kind": kind, "csrf_token": csrf or "",
            "extension_port": extension_port,
            "extension_csrf_token": _antigravity_extract_flag(
                command, "--extension_server_csrf_token"),
        })
    return results


def _antigravity_scan_recently_empty(now_epoch=None):
    """上一轮 ps 扫空后的 TTL 内直接判定没跑,省掉每 tick 一次 ps 进程。"""
    root = _load_json(ANTIGRAVITY_SCAN_CACHE, {})
    empty_at = _provider_number(root.get("empty_at")) if isinstance(root, dict) else None
    if empty_at is None:
        return False
    now = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    return 0 <= now - int(empty_at) < _ANTIGRAVITY_SCAN_MISS_TTL


def _record_antigravity_scan(found, now_epoch=None):
    if found:
        try:
            os.remove(ANTIGRAVITY_SCAN_CACHE)
        except OSError:
            pass
        return
    try:
        _atomic_write_json(ANTIGRAVITY_SCAN_CACHE, {
            "empty_at": int(now_epoch if now_epoch is not None else datetime.now().timestamp()),
        })
    except OSError:
        pass


def _antigravity_running_processes(now_epoch=None):
    if _antigravity_scan_recently_empty(now_epoch):
        return []
    try:
        result = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    processes = _antigravity_process_infos(result.stdout)
    _record_antigravity_scan(bool(processes), now_epoch)
    return processes


def _antigravity_listening_ports(pid):
    lsof = next((path for path in ("/usr/sbin/lsof", "/usr/bin/lsof")
                 if os.path.isfile(path) and os.access(path, os.X_OK)), None)
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({int(value) for value in re.findall(r":(\d+)\s+\(LISTEN\)", result.stdout)})


def _antigravity_endpoints(process):
    endpoints = []
    extension_port = process.get("extension_port")
    if extension_port:
        for token in (process.get("extension_csrf_token"), process.get("csrf_token")):
            if token is not None:
                endpoints.append(("http", extension_port, token, True))
    for port in _antigravity_listening_ports(process["pid"]):
        endpoints.append(("https", port, process.get("csrf_token") or "",
                          process.get("kind") != "cli"))
    unique = []
    for endpoint in endpoints:
        if endpoint not in unique:
            unique.append(endpoint)
    return unique


def _antigravity_request(endpoint, path, body):
    scheme, port, csrf, requires_csrf = endpoint
    headers = {"Connect-Protocol-Version": "1"}
    if requires_csrf:
        headers["X-Codeium-Csrf-Token"] = csrf
    return _provider_json_request(
        f"{scheme}://127.0.0.1:{port}{path}", headers=headers, method="POST", body=body,
        timeout=2, allow_insecure_loopback_tls=True)


def _antigravity_remaining(value):
    if not isinstance(value, dict):
        return None
    raw = value.get("remainingFraction")
    if raw is None and value.get("case") == "remainingFraction":
        raw = value.get("value")
    number = _provider_number(raw)
    return max(0.0, min(1.0, number)) if number is not None else None


def _normalize_antigravity_quota_summary(payload, updated=None):
    root = payload.get("response") if isinstance(payload, dict) \
        and isinstance(payload.get("response"), dict) else payload
    groups = root.get("groups") if isinstance(root, dict) and isinstance(root.get("groups"), list) else []
    windows = []
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        display = str(group.get("displayName") or f"Group {group_index + 1}")
        lower_group = display.lower()
        if "gemini" in lower_group:
            family, family_order = "Gemini", 0
        elif "claude" in lower_group or "gpt" in lower_group or "third" in lower_group:
            family, family_order = "Claude/GPT", 1
        else:
            family, family_order = display, 2 + group_index
        for bucket_index, bucket in enumerate(group.get("buckets") or []):
            if not isinstance(bucket, dict) or bucket.get("disabled") is True:
                continue
            bucket_id = str(bucket.get("bucketId") or f"bucket-{bucket_index}")
            cadence = (bucket_id + " " + str(bucket.get("displayName") or "")).lower()
            normalized = cadence.replace("_", "-")
            if any(marker in normalized for marker in ("5h", "5-hour", "five hour",
                                                        "five-hour", "session")):
                cadence_title, minutes, cadence_order = "5h", 300, 0
            elif any(marker in normalized for marker in ("weekly", "week", "7d")):
                cadence_title, minutes, cadence_order = "周", 10080, 1
            else:
                cadence_title, minutes, cadence_order = str(
                    bucket.get("displayName") or bucket_id), None, 2
            remaining = _antigravity_remaining(bucket.get("remaining"))
            windows.append((family_order, cadence_order, bucket_index, _provider_window(
                "antigravity-" + bucket_id, f"{family} {cadence_title}",
                (1 - remaining) * 100 if remaining is not None else None,
                bucket.get("resetTime"), minutes, bucket.get("description"),
                usage_known=remaining is not None)))
    windows.sort(key=lambda item: item[:3])
    rows = [item[3] for item in windows]
    return {
        "available": bool(rows),
        "plan": None, "account": None, "windows": rows, "details": [],
        "source": "antigravity-local",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def _normalize_antigravity_user_status(payload, updated=None):
    if not isinstance(payload, dict):
        return {}
    status = payload.get("userStatus") if isinstance(payload.get("userStatus"), dict) else payload
    config_data = status.get("cascadeModelConfigData") if isinstance(
        status.get("cascadeModelConfigData"), dict) else {}
    configs = config_data.get("clientModelConfigs")
    if not isinstance(configs, list):
        configs = payload.get("clientModelConfigs") if isinstance(
            payload.get("clientModelConfigs"), list) else []
    windows = []
    for index, config in enumerate(configs):
        if not isinstance(config, dict) or not isinstance(config.get("quotaInfo"), dict):
            continue
        info = config["quotaInfo"]
        remaining = _provider_number(info.get("remainingFraction"))
        if remaining is None:
            continue
        label = config.get("label")
        model = config.get("modelOrAlias") if isinstance(config.get("modelOrAlias"), dict) else {}
        model_id = model.get("model") or f"model-{index}"
        windows.append(_provider_window(
            "antigravity-model-" + str(model_id), str(label or model_id),
            (1 - max(0, min(1, remaining))) * 100, info.get("resetTime")))
    windows.sort(key=lambda row: (-(row.get("used_pct") or 0), row["title"]))
    tier = status.get("userTier") if isinstance(status.get("userTier"), dict) else {}
    plan_status = status.get("planStatus") if isinstance(status.get("planStatus"), dict) else {}
    plan_info = plan_status.get("planInfo") if isinstance(plan_status.get("planInfo"), dict) else {}
    plan = next((value.strip() for value in (
        tier.get("name"), plan_info.get("planName"), plan_info.get("planDisplayName"),
        plan_info.get("displayName"), plan_info.get("productName"),
        plan_info.get("planShortName")) if isinstance(value, str) and value.strip()), None)
    account = status.get("email") if isinstance(status.get("email"), str) else None
    return {
        "available": bool(windows), "plan": plan, "account": account,
        "windows": windows[:12], "details": [], "source": "antigravity-local",
        "updated": int(updated if updated is not None else datetime.now().timestamp()),
        "stale": False,
    }


def fetch_antigravity_quota():
    paths = {
        "summary": "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
        "status": "/exa.language_server_pb.LanguageServerService/GetUserStatus",
        "models": "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs",
    }
    metadata = {"metadata": {
        "ideName": "antigravity", "extensionName": "antigravity",
        "ideVersion": "unknown", "locale": "en",
    }}
    last_error = None
    for process in _antigravity_running_processes():
        endpoints = _antigravity_endpoints(process)
        if not endpoints:
            continue
        marker = _provider_credential_marker(
            "antigravity", process.get("pid"), process.get("csrf_token"), endpoints)
        cached = _cached_provider_quota("antigravity", marker, _PROVIDER_QUOTA_TTL)
        if cached:
            return cached
        for endpoint in endpoints:
            try:
                summary_payload = _antigravity_request(
                    endpoint, paths["summary"], {"forceRefresh": True})
                quota = _normalize_antigravity_quota_summary(summary_payload)
                if quota.get("available"):
                    try:
                        identity_payload = _antigravity_request(endpoint, paths["status"], metadata)
                        identity = _normalize_antigravity_user_status(identity_payload)
                        quota["plan"] = identity.get("plan")
                        quota["account"] = identity.get("account")
                    except Exception:
                        pass
                    _save_provider_quota_cache("antigravity", marker, quota)
                    return quota
            except Exception as error:
                last_error = error
        for path, body in ((paths["status"], metadata), (paths["models"], metadata)):
            for endpoint in endpoints:
                try:
                    quota = _normalize_antigravity_user_status(
                        _antigravity_request(endpoint, path, body))
                    if quota.get("available"):
                        _save_provider_quota_cache("antigravity", marker, quota)
                        return quota
                except Exception as error:
                    last_error = error
        fallback = _cached_provider_quota(
            "antigravity", marker, _PROVIDER_QUOTA_FALLBACK_TTL, stale=True)
        if fallback:
            return fallback
    if last_error:
        raise last_error
    return {}


def scan_antigravity_quota():
    return fetch_antigravity_quota() if _provider_quota_enabled("antigravity") else {}


def scan_provider_quotas(errors=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
            # Native Grok Bot authorization is always tried first. Its own
            # fallback can reuse Cursor when the native login is unavailable.
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

