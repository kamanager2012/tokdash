import os
import glob
import json
from datetime import datetime, date

from core.config import (
    HOME,
    WORKBUDDY_DIR,
    WORKBUDDY_AI_DIR,
    CODEBUDDY_DIR,
    _empty_token_day,
    _empty_token_ranges,
    classify_date,
    parse_ts,
)
from core.pricing import _raw_price
from core.storage import (
    _add_token_usage,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)


def _workbuddy_number(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key not in obj:
            continue
        value = obj.get(key)
        if isinstance(value, bool):
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return None


def _workbuddy_detail_total(value, *keys):
    if isinstance(value, dict):
        return _workbuddy_number(value, *keys) or 0
    if isinstance(value, list):
        return sum(_workbuddy_number(item, *keys) or 0 for item in value if isinstance(item, dict))
    return 0


def _workbuddy_timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        dt = parse_ts(value)
        return dt.astimezone() if dt else None
    return None


def _workbuddy_usage_record(item):
    message = item.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    provider = item.get("providerData") or message.get("providerData") or {}
    if not isinstance(provider, dict):
        provider = {}

    message_usage = message.get("usage") or {}
    normalized = provider.get("usage") or {}
    raw = provider.get("rawUsage") or {}
    sources = [x for x in (message_usage, normalized, raw) if isinstance(x, dict) and x]

    selected = None
    input_total = output = 0
    for source in sources:
        inp = _workbuddy_number(source, "input_tokens", "inputTokens", "input", "prompt_tokens")
        out = _workbuddy_number(source, "output_tokens", "outputTokens", "output", "completion_tokens")
        if (inp or 0) + (out or 0) > 0:
            selected = source
            input_total = inp or 0
            output = out or 0
            break
    if selected is None:
        return None

    cache_read_candidates = []
    cache_write_candidates = []
    total_candidates = []
    for source in sources:
        cache_read_candidates.extend([
            _workbuddy_number(source, "cache_read_input_tokens", "cacheReadInputTokens",
                              "cache_read", "cacheRead", "cached_tokens", "cachedTokens") or 0,
            _workbuddy_number(source, "prompt_cache_hit_tokens") or 0,
            _workbuddy_detail_total(source.get("inputTokensDetails"), "cached_tokens", "cachedTokens"),
            _workbuddy_detail_total(source.get("input_tokens_details"), "cached_tokens", "cachedTokens"),
            _workbuddy_detail_total(source.get("prompt_tokens_details"), "cached_tokens", "cachedTokens"),
        ])
        cache_write_candidates.extend([
            _workbuddy_number(source, "cache_creation_input_tokens", "cacheCreationInputTokens",
                              "cache_write_input_tokens", "cacheWriteInputTokens",
                              "prompt_cache_write_tokens", "cache_write", "cacheWrite") or 0,
        ])
        total = _workbuddy_number(source, "total_tokens", "totalTokens", "total")
        if total is not None:
            total_candidates.append(total)

    cache_read = max(cache_read_candidates, default=0)
    cache_write = max(cache_write_candidates, default=0)
    inclusive_input = any(total == input_total + output for total in total_candidates)
    if inclusive_input:
        cache_read = min(cache_read, input_total)
        cache_write = min(cache_write, max(input_total - cache_read, 0))
        input_tokens = max(input_total - cache_read - cache_write, 0)
    else:
        input_tokens = input_total

    timestamp_value = item.get("timestamp") or message.get("timestamp")
    dt = _workbuddy_timestamp(timestamp_value)
    if dt is None:
        return None

    model = (provider.get("requestModelName") or provider.get("requestModelId")
             or provider.get("model") or message.get("model") or item.get("model") or "unknown")
    price = _raw_price(str(model))
    cost = (input_tokens / 1e6 * price["in"] + output / 1e6 * price["out"]
            + cache_read / 1e6 * price["cache_read"]
            + cache_write / 1e6 * price["cache_write"])
    item_id = item.get("id") or provider.get("messageId") or ""
    return {
        "date": dt.date().isoformat(),
        "hour": dt.hour,
        "ts": dt.timestamp(),
        "ts_key": str(timestamp_value),
        "item_id": str(item_id),
        "in": input_tokens,
        "out": output,
        "cr": cache_read,
        "cw": cache_write,
        "reason": 0,
        "cost": cost,
        "model": str(model),
    }


def _iter_workbuddy_records(file_cache):
    items = []
    for path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        for record in entry.get("records", []):
            if isinstance(record, dict):
                items.append((record.get("ts", 0), path, entry, record))
    items.sort(key=lambda x: (x[0], x[1]))

    seen = set()
    for _, path, entry, record in items:
        key = record.get("dedup") or f"{path}:{record.get('line', 0)}:{record.get('ts_key', '')}"
        if key in seen:
            continue
        seen.add(key)
        yield path, entry, record


def _scan_workbuddy_root(bounds, cache, root, tool_key):
    ledger_touch(tool_key)
    fc = cache.setdefault(tool_key, {})
    B = _empty_token_ranges()
    if not os.path.isdir(root):
        return {"ranges": B}

    files = set(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    stale = set(fc.keys())
    for path in sorted(files):
        stale.discard(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = f"{st.st_mtime}:{st.st_size}"
        if isinstance(fc.get(path), dict) and fc[path].get("sig") == sig:
            continue

        records = []
        project = None
        session_id = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line_no, line in enumerate(fh, 1):
                    if '"usage"' not in line and '"cwd"' not in line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    project = item.get("cwd") or project
                    session_id = item.get("sessionId") or session_id
                    record = _workbuddy_usage_record(item)
                    if record is None:
                        continue
                    record_session = str(item.get("sessionId") or session_id)
                    if record["item_id"]:
                        record["dedup"] = json.dumps(
                            [record_session, record["item_id"], record["ts_key"]], separators=(",", ":"))
                    else:
                        record["dedup"] = f"{path}:{line_no}:{record['ts_key']}"
                    record["session"] = record_session
                    record["line"] = line_no
                    records.append(record)
        except OSError:
            continue
        fc[path] = {"sig": sig, "records": records, "proj": project, "sid": str(session_id)}

    for path in stale:
        fc.pop(path, None)

    days = {}
    sessions = {}
    day_projects = {}
    for _, entry, record in _iter_workbuddy_records(fc):
        day = days.setdefault(record["date"], _empty_token_day())
        _add_token_usage(day, record["in"], record["out"], record["cr"], record["cw"],
                         0, record["cost"], record["model"])
        sessions.setdefault(record["date"], set()).add(record.get("session") or "unknown")
        proj_name = os.path.basename((entry.get("proj") or "").rstrip("/"))
        if proj_name:
            day_projects.setdefault(record["date"], set()).add(proj_name)
    for day_key, names in day_projects.items():
        days[day_key]["projects"] = sorted(names)[:3]

    for day_key, day in days.items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            B[range_key]["sessions"].update(sessions.get(day_key, set()))

    for day_key, day in ledger_reconcile(tool_key, days).items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            _merge_token_day(B[range_key], day)
    return {"ranges": B}


def scan_workbuddy(bounds, cache):
    return _scan_workbuddy_root(bounds, cache, WORKBUDDY_DIR, "workbuddy")


def scan_workbuddy_ai(bounds, cache):
    return _scan_workbuddy_root(bounds, cache, WORKBUDDY_AI_DIR, "workbuddy_ai")


def scan_codebuddy(bounds, cache):
    return _scan_workbuddy_root(bounds, cache, CODEBUDDY_DIR, "codebuddy")
