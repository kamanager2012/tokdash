import os
import glob
import json
import hashlib
from datetime import datetime, date, timezone

from core.config import (
    GROK_BOT_DIRS,
    _empty_grok_bot,
    _existing_dirs,
    classify_date,
)
from core.collectors.quotas import _provider_number
from core.storage import ledger_touch

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
