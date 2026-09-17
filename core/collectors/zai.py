"""Z.AI / GLM remote quota and usage collector.

Supports both global (api.z.ai) and BigModel China (open.bigmodel.cn) regions,
personal and team scopes, multi-window token/credit limits, and balance queries.
"""

import os
import sys
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.collectors.quotas import (
    _PROVIDER_QUOTA_FALLBACK_TTL,
    _PROVIDER_QUOTA_TTL,
    _cached_provider_quota,
    _provider_config_string,
    _provider_credential_marker,
    _provider_epoch,
    _provider_integer,
    _provider_json_request,
    _provider_number,
    _provider_percent,
    _provider_quota_enabled,
    _provider_usage_from_days,
    _provider_usage_int,
    _provider_window,
    _save_provider_quota_cache,
)


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


def _zai_quota_url(url, scope="personal"):
    if scope != "team":
        return url
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key != "type"]
    query.append(("type", "2"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _zai_model_usage_url(base, scope="personal", now=None):
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


__all__ = [
    "_local_timezone_name",
    "_zai_quota_url",
    "_zai_model_usage_url",
    "_normalize_zai_model_usage",
    "_zai_limit",
    "_zai_limit_detail",
    "_normalize_zai_quota",
    "fetch_zai_quota",
    "scan_zai_quota",
]
