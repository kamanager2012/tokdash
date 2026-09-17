"""Codex event disk cache, deduplication, and prefix replay engine.

Handles persistent on-disk event streams, fork detection, and rollout prefix deduplication
to prevent duplicate accounting across branched and archived rollout sessions.
"""

import os
import json
import hashlib
import tempfile as _tempfile
from datetime import datetime

from core.storage import (
    _codex_event_cache_dir,
    _add_model_usage,
)


def _codex_event_key(event):
    if not isinstance(event, list) or len(event) < 11:
        return None
    total_values = event[2:6]
    if not all(value is not None for value in total_values):
        return None
    return tuple(event[2:10])


def _codex_event_cache_path(file_path):
    normalized = os.path.normcase(os.path.realpath(file_path))
    digest = hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
    return os.path.join(_codex_event_cache_dir(), f"{digest}.jsonl")


def _codex_event_cache_ready(file_path, entry):
    if not isinstance(entry, dict) or entry.get("event_count") is None:
        return False
    try:
        expected_size = int(entry.get("event_cache_size", -1))
        return expected_size >= 0 and os.path.getsize(
            _codex_event_cache_path(file_path)) >= expected_size
    except (OSError, TypeError, ValueError):
        return False


def _codex_write_event_cache(file_path, events):
    directory = _codex_event_cache_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    destination = _codex_event_cache_path(file_path)
    fd, tmp = _tempfile.mkstemp(prefix=".codex-events-", suffix=".jsonl", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, destination)
        return os.path.getsize(destination)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _codex_append_event_cache(file_path, events, expected_size):
    destination = _codex_event_cache_path(file_path)
    with open(destination, "r+b") as handle:
        current_size = os.fstat(handle.fileno()).st_size
        if current_size < expected_size:
            raise OSError("Codex event cache is shorter than its committed size")
        handle.truncate(expected_size)
        handle.seek(expected_size)
        for event in events:
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            handle.write(payload.encode("utf-8"))
            handle.write(b"\n")
        return handle.tell()


def _codex_remove_event_cache(file_path):
    try:
        os.remove(_codex_event_cache_path(file_path))
    except OSError:
        pass


def _codex_clear_event_cache(file_cache):
    for file_path in list(file_cache):
        _codex_remove_event_cache(file_path)
    file_cache.clear()


def _iter_codex_cached_events(file_path, start_index=0, limit=None):
    emitted = 0
    with open(_codex_event_cache_path(file_path), "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < start_index:
                continue
            if limit is not None and emitted >= limit:
                break
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                raise OSError("Codex event cache contains invalid JSON")
            if not isinstance(event, list):
                raise OSError("Codex event cache contains an invalid event")
            emitted += 1
            yield event


def _codex_event_metadata(events):
    keys = []
    first_ts = None
    last_ts = None
    for event in events:
        if first_ts is None and event:
            first_ts = str(event[0])
        if event:
            last_ts = str(event[0])
        if len(keys) < 2:
            key = _codex_event_key(event)
            if key is not None:
                keys.append(list(key))
    return {
        "event_count": len(events),
        "first_keys": keys,
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
    }


def _codex_days_from_cached_events(file_path, start_index=0, event_count=None):
    days = {}
    limit = None if event_count is None else max(int(event_count) - start_index, 0)
    for event in _iter_codex_cached_events(
            file_path, start_index=start_index, limit=limit):
        _codex_add_event(days, event)
    return days


def _codex_entry_prefix_key(entry):
    values = entry.get("first_keys") or []
    if len(values) < 2:
        return None
    try:
        return tuple(values[0]), tuple(values[1])
    except TypeError:
        return None


def _codex_cached_prefix_match_count(
        child_path, parent_path, child_count=None, parent_count=None):
    count = 0
    child_events = _iter_codex_cached_events(child_path, limit=child_count)
    parent_events = _iter_codex_cached_events(parent_path, limit=parent_count)
    for child, parent in zip(child_events, parent_events):
        child_key = _codex_event_key(child)
        parent_key = _codex_event_key(parent)
        if child_key is None or child_key != parent_key:
            break
        count += 1
    return count


def _codex_cached_burst_count(file_path, start_index, event_count):
    burst_second = None
    count = 0
    for event in _iter_codex_cached_events(
            file_path, start_index=start_index,
            limit=max(int(event_count) - start_index, 0)):
        if not event:
            break
        event_second = str(event[0])[:19]
        if burst_second is None:
            burst_second = event_second
        elif event_second != burst_second:
            break
        count += 1
    return count if count >= 5 else 0


def _codex_cached_drop_count(file_path, entry, file_cache):
    by_sid = {
        candidate.get("session_id"): (path, candidate)
        for path, candidate in file_cache.items()
        if candidate.get("session_id")
    }
    event_count = int(entry.get("event_count", 0) or 0)
    drop_count = 0
    prefix_open = False

    parent = by_sid.get(entry.get("forked_from_id"))
    if parent and parent[0] != file_path:
        drop_count = _codex_cached_prefix_match_count(
            file_path, parent[0], event_count, parent[1].get("event_count"))
        prefix_open = drop_count > 0 and drop_count == event_count

    prefix_key = _codex_entry_prefix_key(entry)
    if drop_count == 0 and prefix_key is not None and event_count >= 2:
        child_first_ts = str(entry.get("first_event_ts") or "")
        best = 0
        for parent_path, parent_entry in file_cache.items():
            if parent_path == file_path or _codex_entry_prefix_key(parent_entry) != prefix_key:
                continue
            parent_first_ts = str(parent_entry.get("first_event_ts") or "")
            if not parent_first_ts or parent_first_ts >= child_first_ts:
                continue
            best = max(best, _codex_cached_prefix_match_count(
                file_path, parent_path, event_count, parent_entry.get("event_count")))
        if best >= 2:
            drop_count = best
            prefix_open = drop_count == event_count

    burst_count = _codex_cached_burst_count(file_path, drop_count, event_count)
    if burst_count:
        drop_count += burst_count

    if event_count < 2 and not entry.get("forked_from_id"):
        prefix_open = True
    elif entry.get("forked_from_id") and parent is None:
        prefix_open = True
    return min(drop_count, event_count), prefix_open


def _codex_canonical_file_cache(file_cache):
    """Choose one complete physical copy for each logical Codex session."""
    canonical = {}
    selected = {}
    for file_path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        logical_id = ("session", str(session_id)) if session_id else (
            "rollout", os.path.basename(file_path))
        events = entry.get("events") or []
        events = events if isinstance(events, list) else []
        event_count = int(entry.get("event_count", len(events)) or 0)
        event_timestamps = [str(event[0]) for event in events
                            if isinstance(event, list) and event]
        last_event_ts = str(entry.get("last_event_ts") or
                            max(event_timestamps, default=""))
        try:
            parsed_size = int(entry.get("parsed_size", 0) or 0)
        except (TypeError, ValueError):
            parsed_size = 0
        score = (event_count, last_event_ts, parsed_size)
        previous = selected.get(logical_id)
        if previous is not None and score <= previous[0]:
            continue
        if previous is not None:
            canonical.pop(previous[1], None)
        selected[logical_id] = (score, file_path)
        canonical[file_path] = entry
    return canonical


def _codex_add_event(days, event):
    dk = event[1]
    li, lc, lo, lr, cost = event[6:11]
    day = days.setdefault(dk, {"in": 0, "cached": 0, "out": 0,
                               "reason": 0, "cost": 0.0, "models": {}, "hours": [0] * 24})
    day["in"] += li
    day["cached"] += lc
    day["out"] += lo
    day["reason"] += lr
    day["cost"] += cost
    model = event[11] if len(event) > 11 else None
    _add_model_usage(day["models"], model, max(li - lc, 0), lo, lc, 0, lr, cost)
    try:
        hour = datetime.fromisoformat(event[0]).astimezone().hour
        day["hours"][hour] += li + lo
    except (TypeError, ValueError):
        pass


def _codex_prefix_match_count(child_events, parent_events):
    n = 0
    while n < len(child_events) and n < len(parent_events):
        child_key = _codex_event_key(child_events[n])
        parent_key = _codex_event_key(parent_events[n])
        if child_key is None or child_key != parent_key:
            break
        n += 1
    return n


def _codex_replayed_event_indexes(file_cache):
    by_sid = {}
    ordered = []
    for file_path, entry in file_cache.items():
        events = entry.get("events") or []
        if events:
            ordered.append((file_path, entry))
        sid = entry.get("session_id")
        if sid:
            by_sid[sid] = (file_path, entry)

    drops = {}
    for file_path, entry in ordered:
        parent = by_sid.get(entry.get("forked_from_id"))
        if not parent or parent[0] == file_path:
            continue
        n = _codex_prefix_match_count(entry.get("events") or [], parent[1].get("events") or [])
        if n:
            drops.setdefault(file_path, set()).update(range(n))

    prefix_candidates = {}
    for file_path, entry in ordered:
        events = entry.get("events") or []
        if len(events) < 2:
            continue
        first = _codex_event_key(events[0])
        second = _codex_event_key(events[1])
        if first is not None and second is not None:
            prefix_candidates.setdefault((first, second), []).append((file_path, entry))

    for file_path, entry in ordered:
        if drops.get(file_path):
            continue
        child_events = entry.get("events") or []
        if len(child_events) < 2:
            continue
        first = _codex_event_key(child_events[0])
        second = _codex_event_key(child_events[1])
        if first is None or second is None:
            continue
        child_first_ts = child_events[0][0]
        best = 0
        for parent_path, parent_entry in prefix_candidates.get((first, second), []):
            if parent_path == file_path:
                continue
            parent_events = parent_entry.get("events") or []
            if not parent_events or parent_events[0][0] >= child_first_ts:
                continue
            best = max(best, _codex_prefix_match_count(child_events, parent_events))
        if best >= 2:
            drops.setdefault(file_path, set()).update(range(best))

    for file_path, entry in ordered:
        events = entry.get("events") or []
        if len(events) < 5:
            continue
        already = drops.get(file_path, set())
        start = 0
        while start in already:
            start += 1
        if start + 4 >= len(events):
            continue
        first_ev = events[start]
        if not isinstance(first_ev, list) or not first_ev:
            continue
        burst_sec = str(first_ev[0])[:19]
        n = start
        while n < len(events):
            ev = events[n]
            if not isinstance(ev, list) or not ev or str(ev[0])[:19] != burst_sec:
                break
            n += 1
        if n - start >= 5:
            drops.setdefault(file_path, set()).update(range(start, n))
    return drops


def _codex_deduped_days(file_cache):
    """Return per-file daily usage after removing copied rollout prefixes."""
    drops = _codex_replayed_event_indexes(file_cache)
    days_by_file = {}
    for file_path, entry in file_cache.items():
        skip = drops.get(file_path, set())
        for event_index, event in enumerate(entry.get("events", [])):
            if event_index in skip or _codex_event_key(event) is None:
                if event_index in skip:
                    continue
                if not isinstance(event, list) or len(event) < 11:
                    continue
            _codex_add_event(days_by_file.setdefault(file_path, {}), event)
    return days_by_file


def _codex_migrate_event_cache(file_cache):
    if not any(isinstance(entry, dict) and "events" in entry for entry in file_cache.values()):
        return False

    canonical = _codex_canonical_file_cache(file_cache)
    drops = _codex_replayed_event_indexes(canonical)
    days_by_file = _codex_deduped_days(canonical)
    prepared = {}
    for file_path, entry in file_cache.items():
        events = entry.get("events") or []
        cache_size = _codex_write_event_cache(file_path, events)
        metadata = _codex_event_metadata(events)
        skipped = drops.get(file_path, set())
        drop_count = 0
        while drop_count in skipped:
            drop_count += 1
        prepared[file_path] = {
            **metadata,
            "event_cache_size": cache_size,
            "drop_count": drop_count,
            "dedupe_open": bool(drop_count and drop_count == len(events)),
            "deduped_days": days_by_file.get(file_path, {}),
            "canonical": file_path in canonical,
        }

    for file_path, entry in file_cache.items():
        entry.update(prepared[file_path])
        entry["days"] = entry["deduped_days"] if entry["canonical"] else {}
        entry.pop("events", None)
    return True


__all__ = [
    "_codex_event_key",
    "_codex_event_cache_path",
    "_codex_event_cache_ready",
    "_codex_write_event_cache",
    "_codex_append_event_cache",
    "_codex_remove_event_cache",
    "_codex_clear_event_cache",
    "_iter_codex_cached_events",
    "_codex_event_metadata",
    "_codex_days_from_cached_events",
    "_codex_entry_prefix_key",
    "_codex_cached_prefix_match_count",
    "_codex_cached_burst_count",
    "_codex_cached_drop_count",
    "_codex_canonical_file_cache",
    "_codex_add_event",
    "_codex_prefix_match_count",
    "_codex_replayed_event_indexes",
    "_codex_deduped_days",
    "_codex_migrate_event_cache",
]
