import os
import glob
import json
import math
import re
import hashlib
import base64
import subprocess
import threading
import tempfile as _tempfile
import time as _time
import urllib.request
import urllib.parse
from datetime import datetime, date, timezone

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from core.config import (
    _atomic_write_json,
    CODEX_DIR,
    CODEX_ARCHIVED_DIR,
    CODEX_AUTH,
    CODEX_CONFIG,
    CODEX_QUOTA_CACHE,
    CODEX_RESET_CARDS_CACHE,
    _SCAN_CACHE_FILE,
    RANGE_KEYS,
    TOKEN_FIELDS,
    classify_date,
    parse_ts,
    _load_json,
    _path_candidates,
    _existing_dirs,
)
from core.pricing import (
    _raw_price,
    _known_id_or_raw,
    _has_known_price,
    price_for,
)
from core.storage import (
    _CODEX_EVENT_CACHE_SUFFIX,
    _CODEX_PARSER_VERSION,
    _CODEX_SCAN_CHECKPOINT_INTERVAL,
    _codex_event_cache_dir,
    _remove_codex_event_cache_dir,
    _save_scan_cache,
    ledger_reconcile,
    ledger_touch,
    _add_model_usage,
)
from core.collectors.claude import _iso_to_epoch

# Re-export live limits and reset cards from codex_limits
from core.collectors.codex_limits import (
    _CODEX_QUOTA_TTL,
    _CODEX_QUOTA_FALLBACK_TTL,
    _CODEX_USAGE_URL,
    _CODEX_USAGE_MAX_RESPONSE_BYTES,
    _CODEX_RESET_CARDS_URL,
    _CODEX_RESET_CARDS_REFRESH_INTERVAL,
    _CODEX_RESET_CARDS_RETRY_INTERVAL,
    _CODEX_RESET_CARDS_MAX_RESPONSE_BYTES,
    _window_from_codex_live,
    _codex_live_to_limits,
    _codex_limits_have_active_window,
    _cached_codex_live_limits,
    _codex_live_snapshot_is_current,
    _decode_jwt_claims,
    _codex_config,
    _codex_is_custom_provider,
    _codex_auth_context,
    fetch_codex_live_limits,
    _normalize_codex_reset_cards,
    _cached_codex_reset_cards,
    _codex_reset_cards_next_attempt,
    _save_codex_reset_cards_state,
    fetch_codex_reset_cards,
)

# Re-export event disk cache, deduplication, and replay from codex_cache
from core.collectors.codex_cache import (
    _codex_event_key,
    _codex_event_cache_path,
    _codex_event_cache_ready,
    _codex_write_event_cache,
    _codex_append_event_cache,
    _codex_remove_event_cache,
    _codex_clear_event_cache,
    _iter_codex_cached_events,
    _codex_event_metadata,
    _codex_days_from_cached_events,
    _codex_entry_prefix_key,
    _codex_cached_prefix_match_count,
    _codex_cached_burst_count,
    _codex_cached_drop_count,
    _codex_canonical_file_cache,
    _codex_add_event,
    _codex_prefix_match_count,
    _codex_replayed_event_indexes,
    _codex_deduped_days,
    _codex_migrate_event_cache,
)


_CODEX_MODEL_RECORD_TYPES = {"turn_context", "session_meta"}
_CODEX_USAGE_RECORD_MARKERS = (
    b'"token_count"', b'"turn_context"', b'"session_meta"',
)


def _codex_decode_json_string(raw):
    try:
        if b"\\" not in raw:
            return raw.decode("utf-8")
        return json.loads(b'"' + raw + b'"')
    except Exception:
        return raw.decode("utf-8", errors="ignore")


def _codex_probe_record_header(data):
    """Read selected JSON fields from a bounded record prefix.

    Codex adds top-level metadata fields over time. This structural probe tracks
    object depth instead of depending on serialized key order, while leaving
    large unrelated JSONL records bounded by the caller's prefix limits.
    """
    timestamp = None
    root_type = None
    payload_type = None
    model = None
    pending_keys = {}
    containers = []
    payload_depth = None
    depth = 0
    i = 0
    size = len(data)

    while i < size:
        ch = data[i]
        if ch in b" \t\r\n":
            i += 1
            continue

        if ch == 0x22:  # JSON string
            start = i + 1
            i = start
            while i < size:
                if data[i] == 0x5C:  # escape
                    i += 2
                    continue
                if data[i] == 0x22:
                    break
                i += 1
            if i >= size:
                break

            value = _codex_decode_json_string(bytes(data[start:i]))
            i += 1
            lookahead = i
            while lookahead < size and data[lookahead] in b" \t\r\n":
                lookahead += 1
            if lookahead < size and data[lookahead] == 0x3A:  # colon
                pending_keys[depth] = value
                i = lookahead + 1
                continue

            key = pending_keys.pop(depth, None)
            if depth == 1:
                if key == "timestamp":
                    timestamp = value
                elif key == "type":
                    root_type = value
            elif payload_depth is not None and depth == payload_depth:
                if key == "type":
                    payload_type = value
                elif key == "model":
                    model = value
            i = lookahead
            continue

        if ch in (0x7B, 0x5B):  # object or array open
            parent_depth = depth
            key = pending_keys.pop(parent_depth, None)
            containers.append(ch)
            depth += 1
            if ch == 0x7B and parent_depth == 1 and key == "payload":
                payload_depth = depth
            i += 1
            continue

        if ch in (0x7D, 0x5D):  # object or array close
            pending_keys.pop(depth, None)
            if payload_depth == depth:
                payload_depth = None
            if containers:
                containers.pop()
            depth = max(0, depth - 1)
            i += 1
            continue

        if ch == 0x2C:  # comma
            pending_keys.pop(depth, None)
        i += 1

    return timestamp, root_type, payload_type, model


def _iter_codex_usage_records(path, chunk_size=64 * 1024, header_limit=1024,
                              model_limit=4 * 1024, start_offset=0, end_offset=None):
    """Yield model changes and token records without buffering unrelated large JSONL lines."""
    prefix = bytearray()
    candidate = None
    kind = None

    with open(path, "rb", buffering=0) as fh:
        if start_offset:
            fh.seek(start_offset)
        while True:
            if end_offset is not None:
                remaining = end_offset - fh.tell()
                if remaining <= 0:
                    break
                chunk = fh.read(min(chunk_size, remaining))
            else:
                chunk = fh.read(chunk_size)
            if not chunk:
                break

            start = 0
            while start < len(chunk):
                newline = chunk.find(b"\n", start)
                end = len(chunk) if newline < 0 else newline
                piece = memoryview(chunk)[start:end]

                if kind == "token":
                    candidate.extend(piece)
                elif kind == "model":
                    take = min(len(piece), model_limit - len(prefix))
                    prefix.extend(piece[:take])
                    _, _, _, model = _codex_probe_record_header(prefix)
                    if model:
                        yield "model", model
                        prefix = bytearray()
                        kind = "ignore"
                    elif len(prefix) >= model_limit:
                        prefix = bytearray()
                        kind = "ignore"
                elif kind is None and len(prefix) < header_limit:
                    take = min(len(piece), header_limit - len(prefix))
                    prefix.extend(piece[:take])
                    if any(marker in prefix for marker in _CODEX_USAGE_RECORD_MARKERS):
                        timestamp, root_type, payload_type, model = (
                            _codex_probe_record_header(prefix)
                        )
                    else:
                        timestamp = root_type = payload_type = model = None
                    if (timestamp and root_type == "event_msg"
                            and payload_type == "token_count"):
                        candidate = prefix
                        prefix = bytearray()
                        kind = "token"
                        if take < len(piece):
                            candidate.extend(piece[take:])
                    elif timestamp and root_type in _CODEX_MODEL_RECORD_TYPES:
                        kind = "model"
                        if take < len(piece):
                            extra = min(len(piece) - take, model_limit - len(prefix))
                            prefix.extend(piece[take:take + extra])
                        _, _, _, model = _codex_probe_record_header(prefix)
                        if model:
                            yield "model", model
                            prefix = bytearray()
                            kind = "ignore"
                    elif (root_type is not None
                          and root_type not in _CODEX_MODEL_RECORD_TYPES
                          and (root_type != "event_msg"
                               or (payload_type is not None
                                   and payload_type != "token_count"))):
                        prefix = bytearray()
                        kind = "ignore"
                    elif len(prefix) >= header_limit:
                        prefix = bytearray()
                        kind = "ignore"

                if newline < 0:
                    break

                if candidate is not None:
                    yield "token", bytes(candidate)
                prefix = bytearray()
                candidate = None
                kind = None
                start = newline + 1

    if candidate is not None:
        raw_cand = bytes(candidate)
        try:
            json.loads(raw_cand.decode("utf-8", errors="ignore"))
            yield "token", raw_cand
        except Exception:
            pass


def _codex_complete_offset(path, size, chunk_size=64 * 1024):
    """Return the byte offset after the last complete JSONL record."""
    if size <= 0:
        return 0
    try:
        with open(path, "rb", buffering=0) as fh:
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return size
            position = size
            while position > 0:
                start = max(0, position - chunk_size)
                fh.seek(start)
                data = fh.read(position - start)
                newline = data.rfind(b"\n")
                if newline >= 0:
                    return start + newline + 1
                position = start
    except OSError:
        return 0
    return 0


def _codex_offset_guard(path, offset, guard_size=4096):
    if offset <= 0:
        return ""
    try:
        import hashlib
        with open(path, "rb", buffering=0) as fh:
            start = max(0, offset - guard_size)
            fh.seek(start)
            data = fh.read(offset - start)
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return None


def _iter_codex_token_lines(path, chunk_size=64 * 1024, header_limit=4 * 1024):
    """Compatibility iterator for callers that only need token_count records."""
    for kind, value in _iter_codex_usage_records(path, chunk_size, header_limit):
        if kind == "token":
            yield value


def _codex_session_meta(path, max_lines=20, max_line_bytes=2 * 1024 * 1024):
    try:
        with open(path, "rb", buffering=0) as fh:
            for _ in range(max_lines):
                line = fh.readline(max_line_bytes)
                if not line:
                    break
                if b'"session_meta"' not in line:
                    continue
                try:
                    o = json.loads(line.decode("utf-8", errors="ignore"))
                except Exception:
                    continue
                if o.get("type") != "session_meta":
                    continue
                meta = o.get("payload") or {}
                parent_id = meta.get("forked_from_id") or meta.get("parent_thread_id")
                if not parent_id:
                    source = meta.get("source") or {}
                    subagent = source.get("subagent") if isinstance(source, dict) else None
                    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                    if isinstance(spawn, dict):
                        parent_id = spawn.get("parent_thread_id")
                return meta.get("id") or meta.get("session_id"), parent_id
    except OSError:
        pass
    return None, None


def _codex_rollout_files():
    roots = _existing_dirs(
        _path_candidates("TOKEI_CODEX_DIR", CODEX_DIR) +
        _path_candidates("TOKEI_CODEX_ARCHIVED_DIR", CODEX_ARCHIVED_DIR)
    )
    files = []
    seen = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)):
            real = os.path.realpath(path)
            key = os.path.normcase(real)
            if key not in seen and os.path.isfile(real):
                seen.add(key)
                files.append(real)
    return files


def scan_codex(bounds, cache, rollout_files=None):
    ledger_touch("codex")
    fc = cache.setdefault("codex", {})
    if _codex_migrate_event_cache(fc):
        cache["_dirty"] = True
    B = {k: {"in": 0, "cached": 0, "out": 0, "reason": 0, "cost": 0.0,
             "sessions": set(), "models": {}}
         for k in RANGE_KEYS}
    if rollout_files is None:
        rollout_files = _codex_rollout_files()
    if not rollout_files:
        if fc:
            _codex_clear_event_cache(fc)
            cache["_dirty"] = True
        return {"ranges": B, "cur_total": None, "limits": None, "plan": None,
                "limits_updated": None, "limits_consumed": None}

    today_d = bounds["today"].date()
    yest_d = bounds["yesterday"].date()
    week_d = bounds["week"].date()
    lw_start_d = bounds["last_week"].date()
    lw_end_d = bounds["last_week_end"].date()
    month_d = bounds["month"].date()
    year_d = bounds["year"].date()

    cur_file, cur_mtime = None, -1.0
    stale = set(fc.keys())
    dedupe_paths = set()
    active_root = os.path.realpath(CODEX_DIR) if os.path.isdir(CODEX_DIR) else None
    next_checkpoint = _time.monotonic() + _CODEX_SCAN_CHECKPOINT_INTERVAL

    for f in rollout_files:
        stale.discard(f)
        try:
            st = os.stat(f)
        except OSError:
            continue
        mtime, size = st.st_mtime, st.st_size
        try:
            is_active = active_root is not None and os.path.commonpath((f, active_root)) == active_root
        except ValueError:
            is_active = False
        if is_active and mtime > cur_mtime:
            cur_mtime = mtime
            cur_file = f
        sig = f"{st.st_mtime_ns}:{size}"
        entry = fc.get(f)
        event_cache_ready = _codex_event_cache_ready(f, entry)
        legacy_parser_cache = (
            isinstance(entry, dict)
            and entry.get("parser_version") is None
            and entry.get("model_version") == 2
            and event_cache_ready
        )
        legacy_cache_usable = legacy_parser_cache and (
            int(entry.get("event_count", 0) or 0) > 0 or size == 0)
        if legacy_cache_usable and entry.get("sig") == sig:
            entry["parser_version"] = _CODEX_PARSER_VERSION
            entry.pop("model_version", None)
            cache["_dirty"] = True
            continue
        if (not entry or entry.get("sig") != sig
                or entry.get("parser_version") != _CODEX_PARSER_VERSION
                or not event_cache_ready):
            complete_offset = _codex_complete_offset(f, size)
            file_id = f"{st.st_dev}:{st.st_ino}"
            append_from = None
            if (isinstance(entry, dict)
                    and (entry.get("parser_version") == _CODEX_PARSER_VERSION
                         or legacy_cache_usable)):
                old_offset = int(entry.get("parsed_size", 0) or 0)
                if (entry.get("file_id") == file_id and old_offset <= complete_offset
                        and entry.get("parsed_guard") == _codex_offset_guard(f, old_offset)
                        and event_cache_ready):
                    append_from = old_offset

            if append_from is None:
                events = []
                session_id, forked_from_id = _codex_session_meta(f)
                file_limits = None; file_limits_ts = None; file_plan = None
                file_g_limits = None; file_g_ts = None; file_g_plan = None
                file_last_total = None
                prev_total_key = None
                file_model = None
                parse_start = 0
            else:
                events = []
                session_id = entry.get("session_id")
                forked_from_id = entry.get("forked_from_id")
                file_limits = entry.get("limits"); file_limits_ts = entry.get("limits_ts")
                file_plan = entry.get("plan")
                file_g_limits = entry.get("g_limits"); file_g_ts = entry.get("g_ts")
                file_g_plan = entry.get("g_plan")
                file_last_total = entry.get("last_total")
                previous = entry.get("prev_total_key")
                prev_total_key = tuple(previous) if isinstance(previous, (list, tuple)) else None
                file_model = entry.get("active_model")
                parse_start = append_from

            try:
                for record_kind, record in _iter_codex_usage_records(
                        f, start_offset=parse_start, end_offset=complete_offset):
                    if record_kind == "model":
                        file_model = record
                        continue
                    try:
                        o = json.loads(record.decode("utf-8", errors="ignore"))
                    except Exception:
                        continue
                    ts = parse_ts(o.get("timestamp", ""))
                    if not ts:
                        continue
                    info = (o.get("payload") or {}).get("info") or {}
                    last = info.get("last_token_usage") or {}
                    total = info.get("total_token_usage") or {}
                    total_key = None
                    duplicate_total = False
                    if total:
                        total_key = (total.get("input_tokens", 0) or 0,
                                     total.get("cached_input_tokens", 0) or 0,
                                     total.get("output_tokens", 0) or 0,
                                     total.get("reasoning_output_tokens", 0) or 0)
                        duplicate_total = total_key == prev_total_key
                        prev_total_key = total_key
                        file_last_total = total
                    rl = (o.get("payload") or {}).get("rate_limits")
                    if ts and rl:
                        ts_iso = ts.isoformat()
                        if file_g_ts is None or ts_iso > file_g_ts:
                            file_g_ts = ts_iso
                            file_g_limits = rl
                            file_g_plan = rl.get("plan_type")
                        if rl.get("limit_id") == "codex" and (file_limits_ts is None or ts_iso > file_limits_ts):
                            file_limits_ts = ts_iso
                            file_limits = rl
                            file_plan = rl.get("plan_type")
                    # Codex may emit the same cumulative snapshot twice; in that case
                    # last_token_usage is repeated too, so counting it again overstates usage.
                    if ts and last and not duplicate_total:
                        dk = ts.astimezone().date().isoformat()
                        li = last.get("input_tokens", 0) or 0
                        lc = last.get("cached_input_tokens", 0) or 0
                        lo = last.get("output_tokens", 0) or 0
                        lr = last.get("reasoning_output_tokens", 0) or 0
                        # 无模型字段(老版本 CLI 日志/截断会话)标为 unknown,不冒充 gpt-5.5;
                        # 计费仍按 gpt-5.5 保守估算(下行 price_model 兜底)
                        model = _known_id_or_raw(file_model) or "unknown"
                        price_model = model if _has_known_price(model) else "openai/gpt-5.5"
                        cx_base = _raw_price(price_model)
                        hi = li > 272_000
                        p_in = cx_base["in"] * (2 if hi else 1)
                        p_out = cx_base["out"] * (1.5 if hi else 1)
                        p_cr = cx_base["cache_read"] * (2 if hi else 1)
                        cost = (li - lc) / 1e6 * p_in + lc / 1e6 * p_cr + lo / 1e6 * p_out
                        totals = total_key if total_key is not None else (None, None, None, None)
                        # timestamp, local day, cumulative usage, incremental usage, cost
                        events.append([ts.isoformat(), dk, *totals, li, lc, lo, lr, cost, model])
            except OSError:
                continue

            if append_from is None:
                event_cache_size = _codex_write_event_cache(f, events)
                metadata = _codex_event_metadata(events)
                deduped_days = {}
                drop_count = 0
                dedupe_open = True
                was_canonical = False
                dedupe_paths.add(f)
            else:
                event_cache_size = _codex_append_event_cache(
                    f, events, int(entry.get("event_cache_size", 0) or 0))
                metadata = {
                    "event_count": int(entry.get("event_count", 0) or 0) + len(events),
                    "first_keys": entry.get("first_keys") or [],
                    "first_event_ts": entry.get("first_event_ts"),
                    "last_event_ts": (
                        str(events[-1][0]) if events else entry.get("last_event_ts")
                    ),
                }
                if len(metadata["first_keys"]) < 2 and metadata["event_count"]:
                    prefix_events = list(_iter_codex_cached_events(f, limit=2))
                    prefix_metadata = _codex_event_metadata(prefix_events)
                    metadata["first_keys"] = prefix_metadata["first_keys"]
                    metadata["first_event_ts"] = prefix_metadata["first_event_ts"]
                deduped_days = entry.get("deduped_days")
                if not isinstance(deduped_days, dict):
                    deduped_days = dict(entry.get("days") or {})
                drop_count = int(entry.get("drop_count", 0) or 0)
                dedupe_open = bool(entry.get("dedupe_open"))
                was_canonical = bool(entry.get("canonical"))
                if dedupe_open:
                    dedupe_paths.add(f)
                else:
                    for event in events:
                        _codex_add_event(deduped_days, event)

            fc[f] = {
                "sig": sig, "days": entry.get("days", {}) if isinstance(entry, dict) else {},
                "deduped_days": deduped_days,
                "session_id": session_id, "forked_from_id": forked_from_id,
                "limits": file_limits, "limits_ts": file_limits_ts, "plan": file_plan,
                "g_limits": file_g_limits, "g_ts": file_g_ts, "g_plan": file_g_plan,
                "last_total": file_last_total, "prev_total_key": prev_total_key,
                "active_model": file_model, "parser_version": _CODEX_PARSER_VERSION,
                "file_id": file_id, "parsed_size": complete_offset,
                "parsed_guard": _codex_offset_guard(f, complete_offset),
                "event_cache_size": event_cache_size,
                "event_count": metadata["event_count"],
                "first_keys": metadata["first_keys"],
                "first_event_ts": metadata["first_event_ts"],
                "last_event_ts": metadata["last_event_ts"],
                "drop_count": drop_count, "dedupe_open": dedupe_open,
                "canonical": was_canonical,
            }
            cache["_dirty"] = True
            if _time.monotonic() >= next_checkpoint:
                _save_scan_cache(cache)
                next_checkpoint = _time.monotonic() + _CODEX_SCAN_CHECKPOINT_INTERVAL

    for p in stale:
        fc.pop(p, None)
        _codex_remove_event_cache(p)
        cache["_dirty"] = True

    # A session can briefly exist in active and archived directories together.
    # Select the more complete copy before applying fork/replay deduplication.
    canonical_fc = _codex_canonical_file_cache(fc)
    for f, entry in fc.items():
        is_canonical = f in canonical_fc
        if is_canonical and not entry.get("canonical"):
            dedupe_paths.add(f)
        if entry.get("canonical") != is_canonical:
            entry["canonical"] = is_canonical
            cache["_dirty"] = True

    for f in dedupe_paths:
        entry = canonical_fc.get(f)
        if entry is None:
            continue
        try:
            drop_count, dedupe_open = _codex_cached_drop_count(f, entry, canonical_fc)
            deduped_days = _codex_days_from_cached_events(
                f, start_index=drop_count, event_count=entry.get("event_count"))
        except OSError:
            _codex_clear_event_cache(fc)
            cache["_dirty"] = True
            raise
        if (entry.get("drop_count") != drop_count or
                entry.get("dedupe_open") != dedupe_open or
                entry.get("deduped_days") != deduped_days):
            entry["drop_count"] = drop_count
            entry["dedupe_open"] = dedupe_open
            entry["deduped_days"] = deduped_days
            cache["_dirty"] = True

    for f, entry in fc.items():
        days = entry.get("deduped_days", {}) if f in canonical_fc else {}
        if entry.get("days") != days:
            entry["days"] = days
            cache["_dirty"] = True

    # Assembly: per-day → range buckets
    def _codex_range_keys(d):
        return classify_date(d, bounds)

    live_days = {}
    for f, entry in canonical_fc.items():
        for dk, day in entry.get("days", {}).items():
            d = date.fromisoformat(dk)
            agg = live_days.setdefault(
                dk, {"in": 0, "cached": 0, "out": 0, "reason": 0,
                     "cost": 0.0, "models": {}, "hours": [0] * 24})
            agg["in"] += day["in"]; agg["cached"] += day["cached"]
            agg["out"] += day["out"]; agg["reason"] += day["reason"]
            agg["cost"] += day["cost"]
            for model, usage in day.get("models", {}).items():
                _add_model_usage(agg["models"], model, usage.get("in", 0), usage.get("out", 0),
                                 usage.get("cr", 0), usage.get("cw", 0),
                                 usage.get("reason", 0), usage.get("cost", 0))
            for hour, amount in enumerate((day.get("hours") or [])[:24]):
                agg["hours"][hour] += amount
            # 会话数只能来自现存日志(被清日志无从归属)
            for k in _codex_range_keys(d):
                B[k]["sessions"].add(f)

    merged_days = ledger_reconcile("codex", live_days)
    for dk, day in merged_days.items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in _codex_range_keys(d):
            b = B[k]
            b["in"] += day.get("in", 0); b["cached"] += day.get("cached", 0)
            b["out"] += day.get("out", 0); b["reason"] += day.get("reason", 0)
            b["cost"] += day.get("cost", 0.0)
            for model, usage in (day.get("models") or {}).items():
                _add_model_usage(b["models"], model, usage.get("in", 0), usage.get("out", 0),
                                 usage.get("cr", 0), usage.get("cw", 0),
                                 usage.get("reason", 0), usage.get("cost", 0))

    # Find latest limits across all cached files
    latest_limits = None; latest_ts = None; plan_type = None
    g_limits = None; g_ts = None
    for entry in fc.values():
        if entry.get("limits_ts"):
            if latest_ts is None or entry["limits_ts"] > latest_ts:
                latest_ts = entry["limits_ts"]
                latest_limits = entry["limits"]
                plan_type = entry["plan"]
        if entry.get("g_ts"):
            if g_ts is None or entry["g_ts"] > g_ts:
                g_ts = entry["g_ts"]
                g_limits = entry["g_limits"]

    selected_limits_ts = latest_ts
    if latest_limits is None and g_limits is not None:
        latest_limits = g_limits
        plan_type = (g_limits or {}).get("plan_type")
        selected_limits_ts = g_ts

    # 读数时间:live 真正胜出时用抓取时刻,否则用日志里那条记录的时间。
    # live_updated 只在 if live 分支内有定义,先在外面兜底。
    limits_updated = _iso_to_epoch(selected_limits_ts)
    live = fetch_codex_live_limits()
    if live:
        live_limits, live_plan, live_updated = live
        if _codex_live_snapshot_is_current(live_updated, selected_limits_ts):
            latest_limits = live_limits
            plan_type = live_plan or (live_limits or {}).get("plan_type") or plan_type
            limits_updated = int(live_updated)

    # 窗口翻篇后本机又消耗了多少 —— 用来区分「确实回满了」和「读数已经失真」。
    # now_epoch=0 让映射函数只做槽位归类,不触发过期处理。
    slots = _codex_quota_values(latest_limits, now_epoch=0)
    limits_consumed = {
        "p5": _codex_used_since(merged_days, slots["r5"]),
        "pw": _codex_used_since(merged_days, slots["rw"]),
    }

    # For third-party providers the official OpenAI quota is not meaningful,
    # and stale limits from older sessions must not be shown.
    if _codex_is_custom_provider():
        latest_limits = None
        plan_type = None

    cur_total = None
    if cur_file:
        entry = fc.get(cur_file)
        if entry:
            cur_total = entry.get("last_total")

    return {
        "ranges": B,
        "cur_total": cur_total,
        "limits": latest_limits,
        "plan": plan_type,
        "limits_updated": limits_updated,
        "limits_consumed": limits_consumed,
    }


def _codex_used_since(days, since_epoch):
    """since_epoch 之后本机消耗的 codex token;拿不到就返回 None。

    账本里 in 已含 cached,所以口径是 in+out(与 hours 一致)。起始那天按 hours[24]
    从重置小时切起;宁可把重置那个整点全算进来,也不要漏报消耗——漏报会让一份
    已经失真的额度读数被当成"还满着"。
    """
    if not isinstance(days, dict) or not since_epoch:
        return None
    try:
        start = datetime.fromtimestamp(float(since_epoch))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    start_day = start.date().isoformat()
    total = 0
    for dk, day in days.items():
        if not isinstance(day, dict) or dk < start_day:
            continue
        whole_day = int(day.get("in", 0) or 0) + int(day.get("out", 0) or 0)
        if dk > start_day:
            total += whole_day
            continue
        hours = day.get("hours") or []
        # 没有小时分布(老账本条目)就整天算,保守方向是宁多勿少
        total += sum(int(h or 0) for h in hours[start.hour:24]) if hours else whole_day
    return total


def _codex_quota_values(limits, now_epoch=None, consumed=None):
    """Map Codex rate-limit slots by duration; primary/secondary roles can change.

    consumed = {"p5": n, "pw": n}:该窗口 resets_at 之后本机又消耗了多少 token。
    """
    values = {"p5": None, "pw": None, "r5": None, "rw": None,
              "p5_stale": False, "pw_stale": False}
    for slot_name in ("primary", "secondary"):
        slot = (limits or {}).get(slot_name) or {}
        if not slot:
            continue
        minutes = slot.get("window_minutes")
        # Older logs use primary=5h and secondary=7d. Newer plans may expose
        # the 7d window as primary with no secondary, so duration is canonical.
        is_week = minutes == 7 * 24 * 60 or (minutes is None and slot_name == "secondary")
        pct_key, reset_key = ("pw", "rw") if is_week else ("p5", "r5")
        values[pct_key] = slot.get("used_percent")
        values[reset_key] = slot.get("resets_at")

    now_epoch = now_epoch if now_epoch is not None else int(datetime.now().timestamp())
    for pct_key, reset_key in (("p5", "r5"), ("pw", "rw")):
        reset = values[reset_key]
        if not reset or now_epoch <= reset:
            continue
        # 窗口已经翻篇。此后一个 token 都没用 = 确实回满了;用过 = 这份读数已经
        # 失真,标出来让界面说"已过期"。谎报满额比承认不知道危险得多(issue #63)。
        if (consumed or {}).get(pct_key) == 0:
            values[pct_key] = 0.0
            values[reset_key] = None
        elif values[pct_key] is not None:
            values[f"{pct_key}_stale"] = True
    return values


__all__ = [
    # Live limits & reset cards (re-exported from codex_limits)
    "_CODEX_QUOTA_TTL",
    "_CODEX_QUOTA_FALLBACK_TTL",
    "_CODEX_USAGE_URL",
    "_CODEX_USAGE_MAX_RESPONSE_BYTES",
    "_CODEX_RESET_CARDS_URL",
    "_CODEX_RESET_CARDS_REFRESH_INTERVAL",
    "_CODEX_RESET_CARDS_RETRY_INTERVAL",
    "_CODEX_RESET_CARDS_MAX_RESPONSE_BYTES",
    "_window_from_codex_live",
    "_codex_live_to_limits",
    "_codex_limits_have_active_window",
    "_cached_codex_live_limits",
    "_codex_live_snapshot_is_current",
    "_decode_jwt_claims",
    "_codex_config",
    "_codex_is_custom_provider",
    "_codex_auth_context",
    "fetch_codex_live_limits",
    "_normalize_codex_reset_cards",
    "_cached_codex_reset_cards",
    "_codex_reset_cards_next_attempt",
    "_save_codex_reset_cards_state",
    "fetch_codex_reset_cards",
    # Event cache & deduplication (re-exported from codex_cache)
    "_codex_event_key",
    "_codex_event_cache_dir",
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
    # Scanner & session parsing
    "_CODEX_MODEL_RECORD_TYPES",
    "_CODEX_USAGE_RECORD_MARKERS",
    "_codex_decode_json_string",
    "_codex_probe_record_header",
    "_iter_codex_usage_records",
    "_codex_complete_offset",
    "_codex_offset_guard",
    "_iter_codex_token_lines",
    "_codex_session_meta",
    "_codex_rollout_files",
    "scan_codex",
    "_codex_used_since",
    "_codex_quota_values",
]
