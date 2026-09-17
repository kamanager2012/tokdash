"""Grok Bot collector and quota interface.

Parses Grok Bot transcript snapshots (blob files) for session activity,
and handles Grok Bot quota and sand usage inspection.
"""

import os
import sys
import glob
import json
import re
import hashlib
import subprocess
from datetime import datetime, date, timezone

from core.config import (
    GROK_BOT_DIRS,
    GROK_BOT_AUTH_MARKER,
    GROK_BOT_SECRET_PATHS,
    _empty_grok_bot,
    _existing_dirs,
    _expand_path,
    _first_existing_file,
    _load_json,
    classify_date,
)
from core.storage import ledger_touch
from core.collectors.quotas import (
    _PROVIDER_QUOTA_FALLBACK_TTL,
    _PROVIDER_QUOTA_MAX_RESPONSE_BYTES,
    _PROVIDER_QUOTA_TTL,
    _cached_provider_quota,
    _latest_cached_provider_quota,
    _provider_credential_marker,
    _provider_epoch,
    _provider_number,
    _provider_percent,
    _provider_quota_enabled,
    _provider_quota_recent_attempt_result,
    _provider_usage_int,
    _provider_window,
    _save_provider_quota_attempt,
    _save_provider_quota_cache,
)

_GROK_BOT_MAX_BLOB_BYTES = 64 * 1024 * 1024
_GROK_BOT_ACTIVE_GAP_SECONDS = 5 * 60


def _grok_bot_parse_blob(path):
    try:
        size = os.path.getsize(path)
        if size <= 0 or size > _GROK_BOT_MAX_BLOB_BYTES:
            return {"kind": "ignored"}
        with open(path, "r", encoding="utf-8") as handle:
            root = json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError):
        return {"kind": "ignored"}
    value = root.get("value") if isinstance(root, dict) else None
    if not isinstance(value, dict):
        return {"kind": "ignored"}

    rows = value.get("rows")
    if isinstance(rows, list):
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            markers = []
            for candidate in (row.get("lastMessageId"), row.get("newestEntryId")):
                if isinstance(candidate, str) and candidate:
                    markers.append(candidate)
            last_entry = row.get("lastEntry")
            if isinstance(last_entry, dict):
                for candidate in (last_entry.get("id"), last_entry.get("requestId")):
                    if isinstance(candidate, str) and candidate:
                        markers.append(candidate)
            if isinstance(row_id, str) and row_id:
                clean_rows.append({
                    "id": row_id,
                    "markers": sorted(set(markers)),
                })
        return {"kind": "roster", "rows": clean_rows}

    entries = value.get("entries")
    if not isinstance(entries, list):
        return {"kind": "ignored"}
    days = {}
    entry_ids = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_timestamp = _provider_number(entry.get("timestampMs"))
        if raw_timestamp is None or raw_timestamp <= 0:
            continue
        timestamp = raw_timestamp / 1000.0 if raw_timestamp > 100_000_000_000 \
            else raw_timestamp
        try:
            dt = datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
        except (OverflowError, OSError, ValueError):
            continue
        day_key = dt.date().isoformat()
        day = days.setdefault(day_key, {
            "turn_ids": set(), "call_ids": set(), "tool_ids": set(), "timestamps": [],
        })
        entry_id = entry.get("id")
        stable_id = entry_id if isinstance(entry_id, str) and entry_id \
            else f"{path}:{len(entry_ids)}:{int(timestamp * 1000)}"
        if isinstance(entry_id, str) and entry_id:
            entry_ids.append(entry_id)
        day["timestamps"].append(timestamp)
        kind = entry.get("kind")
        if kind == "message" and entry.get("role") == "user":
            day["turn_ids"].add(stable_id)
        if kind == "send-message":
            request_id = entry.get("requestId")
            call_id = request_id if isinstance(request_id, str) and request_id else stable_id
            day["call_ids"].add(call_id)
            message = entry.get("message")
            if isinstance(message, dict) and message.get("type") == "connector":
                day["tool_ids"].add(stable_id)

    clean_days = {}
    for day_key, day in days.items():
        timestamps = sorted(set(day["timestamps"]))
        duration = sum(
            int(current - previous)
            for previous, current in zip(timestamps, timestamps[1:])
            if 0 < current - previous <= _GROK_BOT_ACTIVE_GAP_SECONDS
        )
        clean_days[day_key] = {
            "turns": len(day["turn_ids"]),
            "calls": len(day["call_ids"]),
            "tools": len(day["tool_ids"]),
            "duration": duration,
        }
    marker_ids = entry_ids[:4] + entry_ids[-64:]
    fingerprint_material = "\0".join([
        str(len(entry_ids)), entry_ids[0] if entry_ids else "",
        entry_ids[-1] if entry_ids else "",
    ])
    fingerprint = hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()
    return {
        "kind": "transcript",
        "days": clean_days,
        "markers": sorted(set(marker_ids)),
        "fingerprint": fingerprint,
    }


def scan_grok_bot(bounds, cache):
    ledger_touch("grok_bot")
    file_cache = cache.setdefault("grok_bot", {})
    roots = _existing_dirs(GROK_BOT_DIRS)
    seen = set()
    for root in roots:
        real_root = os.path.realpath(root)
        for path in glob.glob(os.path.join(root, "*.blob")):
            try:
                real_path = os.path.realpath(path)
                if os.path.commonpath((real_root, real_path)) != real_root:
                    continue
                stat = os.stat(real_path)
            except (OSError, ValueError):
                continue
            seen.add(real_path)
            signature = (stat.st_mtime_ns, stat.st_size)
            old = file_cache.get(real_path)
            if isinstance(old, dict) and old.get("sig") == list(signature):
                continue
            parsed = _grok_bot_parse_blob(real_path)
            parsed["sig"] = list(signature)
            file_cache[real_path] = parsed
            cache["_dirty"] = True
    for path in list(file_cache):
        if path not in seen:
            del file_cache[path]
            cache["_dirty"] = True

    roster_rows = []
    transcripts = []
    for path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "roster":
            roster_rows.extend(entry.get("rows") or [])
        elif entry.get("kind") == "transcript":
            transcripts.append((path, entry))

    unique = {}
    for path, entry in transcripts:
        fingerprint = entry.get("fingerprint") or path
        current = unique.get(fingerprint)
        if current is None or (entry.get("sig") or [0])[0] > (current[1].get("sig") or [0])[0]:
            unique[fingerprint] = (path, entry)
    transcripts = list(unique.values())

    unused_rows = set(range(len(roster_rows)))
    assigned = {}
    for path, entry in transcripts:
        markers = set(entry.get("markers") or [])
        match = next((index for index in unused_rows
                      if markers.intersection(roster_rows[index].get("markers") or [])), None)
        if match is not None:
            assigned[path] = roster_rows[match]
            unused_rows.remove(match)
    if len(transcripts) == 1 and len(roster_rows) == 1 and transcripts[0][0] not in assigned:
        assigned[transcripts[0][0]] = roster_rows[0]

    ranges = _empty_grok_bot()["ranges"]
    for path, entry in transcripts:
        row = assigned.get(path) or {}
        sid = row.get("id") or entry.get("fingerprint") or path
        if entry.get("sid") != sid or "project" in entry:
            entry["sid"] = sid
            entry.pop("project", None)
            cache["_dirty"] = True
        for day_key, day in (entry.get("days") or {}).items():
            try:
                local_day = date.fromisoformat(day_key)
            except (TypeError, ValueError):
                continue
            for range_key in classify_date(local_day, bounds):
                bucket = ranges[range_key]
                bucket["sessions"].add(sid)
                bucket["calls"] += int(day.get("calls", 0) or 0)
                bucket["turns"] += int(day.get("turns", 0) or 0)
                bucket["tools"] += int(day.get("tools", 0) or 0)
                bucket["duration"] += int(day.get("duration", 0) or 0)
    return {"ranges": ranges}


# ---------- Grok Bot Quotas & Sand Usage ----------


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
    from core.collectors.cursor import _normalize_cursor_usage_events
    usage = _normalize_cursor_usage_events(unique)
    all_range = (usage.get("ranges") or {}).get("all") if isinstance(usage, dict) else None
    if isinstance(all_range, dict):
        all_range["coverage"] = "本年"
    return usage


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

    from core.collectors.cursor import _cursor_session, fetch_cursor_quota
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


__all__ = [
    "_GROK_BOT_MAX_BLOB_BYTES",
    "_GROK_BOT_ACTIVE_GAP_SECONDS",
    "_grok_bot_parse_blob",
    "scan_grok_bot",
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
]
