"""Cursor collector and quota interface.

Implements Cursor session authentication, usage pagination, token scanning,
and remote quota fetching.
"""

import os
import glob
import json
import re
import sqlite3
from datetime import datetime, date, timezone
from urllib.parse import unquote, quote

from core.config import (
    HOME,
    _empty_token_ranges,
    _first_existing_file,
    _path_candidates,
    _sqlite_ro_uri,
    classify_date,
)
from core.storage import (
    _add_token_usage,
    ledger_touch,
)
from core.collectors.codex import _decode_jwt_claims
from core.collectors.quotas import (
    _PROVIDER_QUOTA_FALLBACK_TTL,
    _PROVIDER_QUOTA_TTL,
    _cached_provider_quota,
    _provider_config_string,
    _provider_credential_marker,
    _provider_integer,
    _provider_json_request,
    _provider_money,
    _provider_number,
    _provider_percent,
    _provider_quota_enabled,
    _provider_usage_from_days,
    _provider_usage_int,
    _provider_window,
    _save_provider_quota_cache,
)

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
        "percent_used": total_pct,
        "end": cycle_end,
        "total_spend": round(plan_used / 100.0, 2),
        "included_spend": round(plan_limit / 100.0, 2),
        "bonus_spend": 0.0,
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

        # Update grok_bot quota cache if sand_usage is returned
        try:
            from core.collectors.grok_bot import _normalize_grok_bot_quota
            grok_bot_quota = _normalize_grok_bot_quota(
                sand_usage, user_info=user_info, identity=session)
            if grok_bot_quota:
                _save_provider_quota_cache("grok_bot", marker, grok_bot_quota)
        except Exception:
            pass

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

    transcript_files = glob.glob(os.path.join(HOME, ".cursor", "projects", "*", "agent-transcripts", "*", "*.jsonl"))
    if not transcript_files:
        return {"ranges": B, "model": "Composer 2.5"}

    ts_pattern = re.compile(r'<timestamp>[A-Za-z]+,\s+([A-Za-z]+)\s+(\d+),\s+(\d{4})')
    month_map = {'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
                 'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'}

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


__all__ = [
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
]
