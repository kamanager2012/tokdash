import os
import glob
import json
import math
import re
import hashlib
import sqlite3
from datetime import datetime, date, timezone

from core.config import (
    HOME,
    CODEBUDDY_DIR,
    DEEPSEEK_HARNESS_DIR,
    GROK_BOT_DIRS,
    KIMI_CODE_DIR,
    OMP_SESSION_DIR,
    OPENCODE_DATA_DIR,
    OPENCODE_DATA_DIRS,
    OPENCODE_DB,
    OPENCODE_DIR,
    PI_AGENT_DIR,
    PI_SESSION_DIR,
    WORKBUDDY_AI_DIR,
    WORKBUDDY_DIR,
    ZCODE_DB,
    _KIMI_CODE_DEFAULT_DIR,
    _KIMI_CODE_LEGACY_DIR,
    _empty_grok_bot,
    _empty_token_day,
    _empty_token_ranges,
    _existing_dirs,
    _first_existing_file,
    _load_json,
    _path_candidates,
    _sqlite_ro_uri,
    _sqlite_signature,
    classify_date,
    parse_ts,
    token_total,
)
from core.pricing import (
    _deepseek_official_price,
    _known_id_or_raw,
    _pricing_id,
    _raw_price,
    price_for,
)
from core.storage import (
    _add_model_usage,
    _add_token_usage,
    _merge_live_token_day,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)
from core.collectors.quotas import _provider_number

def _pi_session_dirs():
    dirs = [
        PI_SESSION_DIR,
        os.path.join(PI_AGENT_DIR, "sessions"),
        os.path.join(HOME, ".pi", "agent", "sessions"),
        OMP_SESSION_DIR,
    ]
    out = []
    for d in dirs:
        d = os.path.realpath(os.path.abspath(os.path.expanduser(d)))
        if d not in out:
            out.append(d)
    return out


def _pi_model_id(msg):
    model = msg.get("model", "") or ""
    provider = msg.get("provider", "") or ""
    if provider and model and "/" not in model:
        return f"{provider}/{model}"
    return model or provider or "unknown"


def _pi_usage_int(usage, *fields):
    for field in fields:
        if field in usage and usage[field] is not None:
            return int(usage[field] or 0)
    return 0


def _pi_usage_cost(u, model):
    cost_obj = u.get("cost") or {}
    total = float(cost_obj.get("total", 0) or 0)
    if total > 0:
        return total
    parts = sum(float(cost_obj.get(k, 0) or 0) for k in ("input", "output", "cacheRead", "cacheWrite"))
    if parts > 0:
        return parts
    p = _raw_price(model)
    inp = _pi_usage_int(u, "input")
    out = _pi_usage_int(u, "output")
    cr = _pi_usage_int(u, "cacheRead", "cache_read")
    cw = _pi_usage_int(u, "cacheWrite", "cache_write")
    return inp / 1e6 * p["in"] + out / 1e6 * p["out"] + cr / 1e6 * p["cache_read"] + cw / 1e6 * p["cache_write"]


def scan_pi(bounds, cache):
    ledger_touch("pi")
    fc = cache.setdefault("pi", {})
    changed = False
    B = _empty_token_ranges()

    roots = [d for d in _pi_session_dirs() if os.path.isdir(d)]
    if not roots:
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": B}

    seen_files = set()
    for root in roots:
        seen_files.update(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    stale = set(fc.keys())

    for f in sorted(seen_files):
        stale.discard(f)
        try:
            st = os.stat(f)
        except OSError:
            continue
        sig = f"{st.st_mtime}:{st.st_size}"
        entry = fc.get(f)
        if not entry or entry.get("sig") != sig:
            days = {}
            proj = None
            sid = os.path.basename(f)
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if '"usage"' not in line and '"type":"session"' not in line and '"type": "session"' not in line:
                            continue
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        if o.get("type") == "session":
                            sid = o.get("id") or sid
                            proj = o.get("cwd") or proj
                            continue
                        if o.get("type") != "message":
                            continue
                        msg = o.get("message") or {}
                        if msg.get("role") != "assistant":
                            continue
                        u = msg.get("usage") or {}
                        if not u:
                            continue
                        dt = parse_ts(o.get("timestamp") or msg.get("timestamp") or "")
                        if dt is None:
                            continue
                        inp = _pi_usage_int(u, "input")
                        out = _pi_usage_int(u, "output")
                        cr = _pi_usage_int(u, "cacheRead", "cache_read")
                        cw = _pi_usage_int(u, "cacheWrite", "cache_write")
                        reason = _pi_usage_int(u, "reasoning", "reason", "reasoningTokens")
                        model = _pi_model_id(msg)
                        cost = _pi_usage_cost(u, model)
                        if inp + out + cr + cw + reason == 0 and cost <= 0:
                            continue
                        dk = dt.astimezone().date().isoformat()
                        day = days.setdefault(dk, _empty_token_day())
                        _add_token_usage(day, inp, out, cr, cw, reason, cost, model)
                        day["hours"][dt.astimezone().hour] += inp + out + cr + cw + reason
            except OSError:
                continue
            fc[f] = {"sig": sig, "days": days, "proj": proj, "sid": sid}
            changed = True

    for p in stale:
        fc.pop(p, None)
        changed = True

    live_days = {}
    for f, entry in fc.items():
        session = entry.get("sid") or f
        for dk, day in entry.get("days", {}).items():
            try:
                d = date.fromisoformat(dk)
            except ValueError:
                continue
            _merge_live_token_day(live_days.setdefault(dk, _empty_token_day()), day)
            # 会话数只能来自现存日志(被清日志无从归属)
            for k in classify_date(d, bounds):
                B[k]["sessions"].add(session)

    for dk, day in ledger_reconcile("pi", live_days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in classify_date(d, bounds):
            _merge_token_day(B[k], day)
    if changed:
        cache["_dirty"] = True
    return {"ranges": B}


# ---------- Prime Agent ----------
# JSONL 文件: ~/.prime/agent/sessions/*.jsonl plus session-artifacts/**/**/*.jsonl.
# Prime Agent uses the Pi Coding Agent Usage shape; child attribution records are bookkeeping only.
# ---------- WorkBuddy ----------
# JSONL 文件: ~/.workbuddy/projects 和 ~/.workbuddy-ai/projects 下的会话文件。
# 两个独立 App 共用解析逻辑,但缓存、账本和展示分别统计。
# 每个带 usage 的 item 代表一次模型调用。providerData 中的同一份 usage 仅作字段补全，
# 不重复累计；reasoning_tokens 已包含在 output_tokens 中。
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
    # 项目名随天入账本(同 scan_claude):日志被清理后仍能回答"那天在干什么"。
    for day_key, names in day_projects.items():
        days[day_key]["projects"] = sorted(names)[:3]

    # 会话数只能来自现存日志(被清日志无从归属)
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


# ---------- Grok Bot ----------
# Grok Bot persists transcript snapshots as JSON blobs. They currently expose
# message/activity metadata, but no model, token, or billing fields. Keep this
# scanner activity-only so text length can never be mistaken for token usage.
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

    # Some releases keep more than one key for the same transcript snapshot.
    # Use its stable entry boundary to avoid counting those copies twice.
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


# ---------- DeepSeek Harness ----------
# Harness 会为同一次调用写 usage chunk 和最终 message。按 session/turn/step
# 只保留最终 message；异常中断时再用 usage chunk 兜底。
_DEEPSEEK_HARNESS_COST_VERSION = 2


def _deepseek_harness_usage_record(item, fallback_model="", fallback_provider="deepseek-official"):
    if not isinstance(item, dict):
        return None
    event_type = item.get("type")
    data = item.get("data") or {}
    if not isinstance(data, dict):
        return None

    priority = 0
    usage = None
    model = fallback_model
    provider = fallback_provider
    if event_type == "assistant/message":
        usage = data.get("usage")
        message = data.get("message") or {}
        source = message.get("source") or {} if isinstance(message, dict) else {}
        if isinstance(source, dict):
            model = source.get("model") or model
            provider = source.get("provider") or provider
        priority = 2
    elif event_type == "assistant/chunk":
        chunk = data.get("chunk") or {}
        if isinstance(chunk, dict) and chunk.get("type") == "usage":
            usage = chunk.get("usage")
            priority = 1
    if not isinstance(usage, dict):
        return None

    timestamp = item.get("time")
    try:
        dt = datetime.fromtimestamp(int(timestamp) / 1000).astimezone()
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    try:
        turn = int(data.get("turn"))
        step = int(data.get("step"))
    except (TypeError, ValueError):
        return None

    inp = max(int(usage.get("inputTokens", 0) or 0), 0)
    raw_out = max(int(usage.get("outputTokens", 0) or 0), 0)
    cr = max(int(usage.get("cacheReadTokens", 0) or 0), 0)
    cw = max(int(usage.get("cacheWriteTokens", 0) or 0), 0)
    reason = min(max(int(usage.get("reasoningTokens", 0) or 0), 0), raw_out)
    out = raw_out - reason
    if inp + raw_out + cr + cw <= 0:
        return None
    model = str(model or "deepseek-v4-pro")
    provider = str(provider or "")
    cost = 0.0
    price = (_deepseek_official_price(model) if provider == "deepseek-official" else None)
    if price is None:
        price_id = _pricing_id(model)
        price = _raw_price(price_id) if price_id else None
    if price:
        cost = (inp / 1e6 * price["in"] + raw_out / 1e6 * price["out"]
                + cr / 1e6 * price["cache_read"] + cw / 1e6 * price["cache_write"])
    return {
        "date": dt.strftime("%Y-%m-%d"), "hour": dt.hour, "ts": int(timestamp),
        "turn": turn, "step": step, "priority": priority, "model": model,
        "provider": provider,
        "in": inp, "out": out, "cr": cr, "cw": cw, "reason": reason,
        "cost": cost,
    }


def _iter_deepseek_harness_records(file_cache):
    records = []
    for path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        session = str(entry.get("sid") or path)
        for record in entry.get("records", []):
            if isinstance(record, dict):
                records.append((record.get("ts", 0), path, entry, session, record))
    records.sort(key=lambda value: (value[0], value[1]))
    seen = set()
    for _, path, entry, session, record in records:
        key = (session, record.get("turn"), record.get("step"))
        if key in seen:
            continue
        seen.add(key)
        yield path, entry, record


def scan_deepseek_harness(bounds, cache):
    ledger_touch("deepseek_harness")
    fc = cache.setdefault("deepseek_harness", {})
    B = _empty_token_ranges()
    if not os.path.isdir(DEEPSEEK_HARNESS_DIR):
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": B}

    files = set(glob.glob(os.path.join(DEEPSEEK_HARNESS_DIR, "**", "*.jsonl"), recursive=True))
    stale = set(fc.keys())
    for path in sorted(files):
        stale.discard(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = f"{st.st_mtime_ns}:{st.st_size}"
        if (isinstance(fc.get(path), dict) and fc[path].get("sig") == sig
                and fc[path].get("cost_version") == _DEEPSEEK_HARNESS_COST_VERSION):
            continue

        session_id = os.path.splitext(os.path.basename(path))[0]
        project = ""
        current_model = "deepseek-v4-pro"
        current_provider = "deepseek-official"
        candidates = {}
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if '"type"' not in line or not any(value in line for value in (
                            '"session"', '"request/header"',
                            '"assistant/chunk"', '"assistant/message"')):
                        continue
                    try:
                        item = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    event_type = item.get("type")
                    if event_type == "session":
                        session_id = str(item.get("id") or session_id)
                        project = item.get("cwd") or project
                        continue
                    if event_type == "request/header":
                        data = item.get("data") or {}
                        header = data.get("header") or {} if isinstance(data, dict) else {}
                        config = header.get("config") or {} if isinstance(header, dict) else {}
                        if isinstance(config, dict):
                            current_model = config.get("model") or current_model
                            current_provider = config.get("provider") or current_provider
                        continue
                    record = _deepseek_harness_usage_record(
                        item, current_model, current_provider)
                    if record is None:
                        continue
                    key = (record["turn"], record["step"])
                    previous = candidates.get(key)
                    if previous is None or record["priority"] >= previous["priority"]:
                        candidates[key] = record
        except OSError:
            continue
        records = sorted(candidates.values(), key=lambda record: record["ts"])
        fc[path] = {"sig": sig, "records": records, "proj": project, "sid": session_id,
                    "cost_version": _DEEPSEEK_HARNESS_COST_VERSION}
        cache["_dirty"] = True

    for path in stale:
        fc.pop(path, None)
        cache["_dirty"] = True

    days = {}
    sessions = {}
    day_projects = {}
    for _, entry, record in _iter_deepseek_harness_records(fc):
        day = days.setdefault(record["date"], _empty_token_day())
        _add_token_usage(day, record["in"], record["out"], record["cr"], record["cw"],
                         record["reason"], record["cost"], record["model"])
        day["hours"][record["hour"]] += token_total(record)
        session = str(entry.get("sid") or "unknown")
        sessions.setdefault(record["date"], set()).add(session)
        project = entry.get("proj") or ""
        project_name = os.path.basename(project.rstrip("/"))
        if project_name:
            day_projects.setdefault(record["date"], set()).add(project_name)
    for day_key, names in day_projects.items():
        days[day_key]["projects"] = sorted(names)[:3]
    for day in days.values():
        day["_cost_version"] = _DEEPSEEK_HARNESS_COST_VERSION

    for day_key, day in days.items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            B[range_key]["sessions"].update(sessions.get(day_key, set()))

    for day_key, day in ledger_reconcile("deepseek_harness", days).items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            _merge_token_day(B[range_key], day)
    return {"ranges": B}


# ---------- OpenCode ----------
# SQLite: ~/.local/share/opencode/opencode.db；旧版 JSON 作为补充来源。
# JSON 文件: ~/.local/share/opencode/storage/message/<session>/msg_*.json
# 每条 assistant 消息有 tokens{input,output,reasoning,cache{read,write}} + cost + modelID。
_OPENCODE_COST_CACHE_VERSION = 1


def _opencode_db_paths():
    data_dirs = _path_candidates(
        "TOKEI_OPENCODE_DATA_DIR", OPENCODE_DATA_DIR, *OPENCODE_DATA_DIRS)
    direct = [OPENCODE_DB] + [os.path.join(root, "opencode.db") for root in data_dirs]
    database = _first_existing_file(direct)
    if database:
        return [os.path.realpath(database)]
    for parent in [os.path.dirname(OPENCODE_DB)] + data_dirs:
        channels = []
        for path in sorted(glob.glob(os.path.join(parent, "opencode-*.db"))):
            name = os.path.basename(path)
            channel = name[len("opencode-"):-len(".db")]
            if channel and all(ch.isalnum() or ch in "._-" for ch in channel):
                channels.append(os.path.realpath(path))
        if channels:
            return [channels[0]]
    return []


def _opencode_json_dirs():
    data_dirs = _path_candidates(
        "TOKEI_OPENCODE_DATA_DIR", OPENCODE_DATA_DIR, *OPENCODE_DATA_DIRS)
    defaults = [OPENCODE_DIR] + [os.path.join(root, "storage", "message") for root in data_dirs]
    return _existing_dirs(_path_candidates("TOKDASH_OPENCODE_DIR", "TOKEI_OPENCODE_DIR", *defaults))


def _opencode_message_day(message, session_id="", created_ms=0, estimate_missing_cost=False):
    if message.get("role") != "assistant":
        return None
    timestamp = (message.get("time") or {}).get("created") or created_ms
    if not timestamp:
        return None
    tokens = message.get("tokens") or {}
    cache = tokens.get("cache") or {}
    model = message.get("modelID", "")
    created = datetime.fromtimestamp(int(timestamp) / 1000).astimezone()
    cost = float(message.get("cost", 0) or 0)
    if estimate_missing_cost and not cost:
        price_id = _pricing_id(model)
        if price_id:
            price = _raw_price(price_id)
            cost = ((int(tokens.get("input", 0) or 0) / 1e6) * price["in"]
                    + ((int(tokens.get("output", 0) or 0) + int(tokens.get("reasoning", 0) or 0)) / 1e6) * price["out"]
                    + (int(cache.get("read", 0) or 0) / 1e6) * price["cache_read"]
                    + (int(cache.get("write", 0) or 0) / 1e6) * price["cache_write"])
    day = {
        "date": created.strftime("%Y-%m-%d"),
        "in": int(tokens.get("input", 0) or 0),
        "out": int(tokens.get("output", 0) or 0),
        "reason": int(tokens.get("reasoning", 0) or 0),
        "cr": int(cache.get("read", 0) or 0),
        "cw": int(cache.get("write", 0) or 0),
        "cost": cost,
        "session": message.get("sessionID") or session_id,
        "models": {},
        "hours": [0] * 24,
    }
    day["hours"][created.hour] = token_total(day)
    _add_model_usage(day["models"], model, day["in"], day["out"], day["cr"],
                     day["cw"], day["reason"], day["cost"])
    return day


def _scan_opencode_database(path, estimate_missing_cost=False):
    import sqlite3

    days = {}
    message_ids = set()
    sessions = {}
    connection = sqlite3.connect(_sqlite_ro_uri(path), uri=True, timeout=1)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("SELECT id, session_id, time_created, data FROM message")
        for message_id, session_id, created_ms, raw in rows:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            day = _opencode_message_day(message, session_id or "", created_ms or 0,
                                        estimate_missing_cost=estimate_missing_cost)
            if not day:
                continue
            if message_id:
                message_ids.add(str(message_id))
            day_key = day.pop("date")
            target = days.setdefault(day_key, _empty_token_day())
            _add_token_usage(target, day["in"], day["out"], day["cr"], day["cw"],
                             day["reason"], day["cost"])
            for model, usage in day["models"].items():
                _add_model_usage(target["models"], model, usage["in"], usage["out"],
                                 usage["cr"], usage["cw"], usage["reason"], usage["cost"])
            for hour, amount in enumerate(day["hours"]):
                target["hours"][hour] += amount
            if day.get("session"):
                sessions.setdefault(day_key, set()).add(day["session"])
    finally:
        connection.close()
    for day_key, ids in sessions.items():
        days[day_key]["sessions"] = sorted(ids)
    return days, sorted(message_ids)


def scan_opencode(bounds, cache):
    ledger_touch("opencode")
    fc = cache.setdefault("opencode", {})
    changed = False
    B = _empty_token_ranges()
    db_paths = _opencode_db_paths()
    json_dirs = _opencode_json_dirs()
    if not db_paths and not json_dirs:
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": B}

    stale = set(fc.keys())
    db_message_ids = set()
    live_days = {}
    live_sessions = {}

    for db_path in db_paths:
        cache_key = "db:" + db_path
        stale.discard(cache_key)
        signature = _sqlite_signature(db_path)
        entry = fc.get(cache_key)
        if (not entry or entry.get("sig") != signature
                or entry.get("cost_version") != _OPENCODE_COST_CACHE_VERSION):
            try:
                days, message_ids = _scan_opencode_database(
                    db_path, estimate_missing_cost=True)
            except Exception:
                continue
            entry = {
                "sig": signature,
                "days": days,
                "message_ids": message_ids,
                "source": "sqlite",
                "cost_version": _OPENCODE_COST_CACHE_VERSION,
            }
            fc[cache_key] = entry
            changed = True
        db_message_ids.update(entry.get("message_ids", []))
        for day_key, day in entry.get("days", {}).items():
            try:
                date.fromisoformat(day_key)
            except ValueError:
                continue
            _merge_live_token_day(live_days.setdefault(day_key, _empty_token_day()), day)
            live_sessions.setdefault(day_key, set()).update(day.get("sessions", []))

    seen_message_ids = set(db_message_ids)
    for json_dir in json_dirs:
        for sess_dir in glob.glob(os.path.join(json_dir, "ses_*")):
            for f in glob.glob(os.path.join(sess_dir, "msg_*.json")):
                file_id = os.path.splitext(os.path.basename(f))[0]
                if file_id in seen_message_ids:
                    continue
                try:
                    st = os.stat(f)
                except OSError:
                    continue
                stale.discard(f)
                sig = f"{st.st_mtime}:{st.st_size}"
                entry = fc.get(f)
                if (entry and entry.get("sig") == sig
                        and entry.get("cost_version") == _OPENCODE_COST_CACHE_VERSION):
                    day_data = entry.get("day")
                    message_id = entry.get("message_id") or file_id
                else:
                    try:
                        with open(f, encoding="utf-8") as handle:
                            d = json.load(handle)
                    except Exception:
                        continue
                    message_id = str(d.get("id") or file_id)
                    if message_id in seen_message_ids:
                        continue
                    day_data = _opencode_message_day(d, estimate_missing_cost=True)
                    fc[f] = {
                        "sig": sig,
                        "day": day_data,
                        "message_id": message_id,
                        "cost_version": _OPENCODE_COST_CACHE_VERSION,
                    }
                    changed = True
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)

                if not day_data:
                    continue
                dk = day_data["date"]
                try:
                    date.fromisoformat(dk)
                except (TypeError, ValueError):
                    continue
                _merge_live_token_day(live_days.setdefault(dk, _empty_token_day()), day_data)
                session = day_data.get("session")
                if session is not None:
                    live_sessions.setdefault(dk, set()).add(session)

    for p in stale:
        fc.pop(p, None)
        changed = True

    for day_key, day in live_days.items():
        day["sessions"] = sorted(live_sessions.get(day_key, set()))

    for day_key, day in ledger_reconcile("opencode", live_days).items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            _merge_token_day(B[range_key], day)
            B[range_key]["sessions"].update(day.get("sessions", []))

    if changed:
        cache["_dirty"] = True
    return {"ranges": B}


# ---------- ZCode ----------
# SQLite: ~/.zcode/cli/db/db.sqlite, model_usage rows use epoch milliseconds.
def _scan_zcode_database(path):
    import sqlite3

    def number(value):
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    days = {}
    sessions = {}
    connection = sqlite3.connect(_sqlite_ro_uri(path), uri=True, timeout=1)
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("""
            SELECT id, session_id, model_id, input_tokens, output_tokens,
                   reasoning_tokens, cache_creation_input_tokens,
                   cache_read_input_tokens, started_at, completed_at
            FROM model_usage
            ORDER BY started_at ASC
        """)
        for row_id, session_id, model, input_total, output_total, reasoning, cache_write, cache_read, started_at, completed_at in rows:
            timestamp_ms = number(completed_at) or number(started_at)
            if not timestamp_ms:
                continue
            try:
                created = datetime.fromtimestamp(timestamp_ms / 1000).astimezone()
            except (OSError, OverflowError, ValueError):
                continue
            input_total = number(input_total)
            output_total = number(output_total)
            reasoning = number(reasoning)
            cache_write = number(cache_write)
            cache_read = number(cache_read)
            fresh_input = max(input_total - cache_read - cache_write, 0)
            visible_output = max(output_total - reasoning, 0)
            if fresh_input + output_total + cache_read + cache_write <= 0:
                continue
            display_model = _known_id_or_raw(model) or str(model or "unknown")
            price_id = _pricing_id(model)
            cost = 0.0
            if price_id:
                price = _raw_price(price_id)
                cost = (fresh_input / 1e6 * price["in"]
                        + output_total / 1e6 * price["out"]
                        + cache_read / 1e6 * price["cache_read"]
                        + cache_write / 1e6 * price["cache_write"])
            day_key = created.date().isoformat()
            day = days.setdefault(day_key, _empty_token_day())
            _add_token_usage(day, fresh_input, visible_output, cache_read, cache_write,
                             reasoning, cost, display_model)
            day["hours"][created.hour] += fresh_input + output_total + cache_read + cache_write
            sessions.setdefault(day_key, set()).add(str(session_id or row_id or "unknown"))
    finally:
        connection.close()
    for day_key, session_ids in sessions.items():
        days[day_key]["sessions"] = sorted(session_ids)
    return days


def scan_zcode(bounds, cache):
    ledger_touch("zcode")
    fc = cache.setdefault("zcode", {})
    B = _empty_token_ranges()
    if not os.path.isfile(ZCODE_DB):
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": B}

    cache_key = "db:" + os.path.realpath(ZCODE_DB)
    signature = _sqlite_signature(ZCODE_DB)
    entry = fc.get(cache_key)
    if not entry or entry.get("sig") != signature or entry.get("version") != 1:
        entry = {"sig": signature, "days": _scan_zcode_database(ZCODE_DB), "version": 1}
        fc.clear()
        fc[cache_key] = entry
        cache["_dirty"] = True

    for day_key, day in ledger_reconcile("zcode", entry.get("days", {})).items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            _merge_token_day(B[range_key], day)
            B[range_key]["sessions"].update(day.get("sessions", []))
    return {"ranges": B}


# ---------- MiMoCode ----------
# MiMoCode uses the OpenCode message schema and XDG data-directory rules.
# ---------- Qwen Code ----------
# 新版逐请求日志提供实时、按小时数据；旧版会话汇总用于补齐历史。
# 两种来源按 sessionId 去重，逐请求日志覆盖同一会话的汇总快照。
# ---------- Kimi Code CLI ----------
# protocol 1 使用主 wire 中的 StatusUpdate/SubagentEvent；protocol 1.5 把每个
# Agent 的 usage.record 独立写入 agents/*/wire.jsonl。两者都只读 session 目录，
# 不扫描 server/events 镜像，避免重复累计。
_KIMI_PARSER_VERSION = 3


def _kimi_roots():
    configured = (os.environ.get("TOKEI_KIMI_DIR") or os.environ.get("KIMI_CODE_HOME")
                  or os.environ.get("KIMI_SHARE_DIR"))
    if configured:
        candidates = [configured]
    elif os.path.normcase(KIMI_CODE_DIR) != os.path.normcase(_KIMI_CODE_DEFAULT_DIR):
        # Tests and embedders may replace KIMI_CODE_DIR after importing this module.
        candidates = [KIMI_CODE_DIR]
    else:
        candidates = [_KIMI_CODE_DEFAULT_DIR, _KIMI_CODE_LEGACY_DIR]
    roots = []
    seen = set()
    for candidate in candidates:
        root = os.path.abspath(os.path.expanduser(candidate))
        key = os.path.normcase(os.path.realpath(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def _kimi_wire_groups():
    """按会话产出 (agent_wires, root_wire);定深有界遍历,不碰 server/events 等镜像目录。

    protocol 1 只写会话根 wire.jsonl,protocol 1.5 写 agents/<agent>/wire.jsonl。
    过渡版本可能两者并存,所以两类都要交给上层,由记录级去重决定谁算谁不算。"""
    groups = []
    for root in _kimi_roots():
        sessions_dir = os.path.join(root, "sessions")
        for session_dir in glob.glob(os.path.join(sessions_dir, "*", "*")):
            if not os.path.isdir(session_dir):
                continue
            agent_wires = sorted(os.path.abspath(path) for path in glob.glob(
                os.path.join(session_dir, "agents", "*", "wire.jsonl")))
            root_wire = os.path.join(session_dir, "wire.jsonl")
            root_wire = os.path.abspath(root_wire) if os.path.isfile(root_wire) else None
            if agent_wires or root_wire:
                groups.append((agent_wires, root_wire))
    return groups


def _kimi_wire_files(groups=None):
    files = set()
    for agent_wires, root_wire in _kimi_wire_groups() if groups is None else groups:
        files.update(agent_wires)
        if root_wire:
            files.add(root_wire)
    return sorted(files)


def _kimi_mirror_sources(groups):
    """→ {根 wire: (同会话的 agent wire...)},只含两者并存的会话。"""
    return {root_wire: tuple(agent_wires)
            for agent_wires, root_wire in groups
            if root_wire and agent_wires}


def _kimi_group_signature(paths):
    parts = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            parts.append(f"{path}:-")
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _kimi_record_counts(paths):
    """把这些 wire 里每条记录的去重键计成多重集,用作根 wire 的排除表。"""
    counts = {}
    for path in paths:
        _scan_kimi_wire(path, seen=counts)
    return counts


def _kimi_project_map():
    result = {}
    for root in _kimi_roots():
        metadata = _load_json(os.path.join(root, "kimi.json"), {})
        for item in metadata.get("work_dirs", []) if isinstance(metadata, dict) else []:
            if not isinstance(item, dict):
                continue
            project = item.get("path")
            if not isinstance(project, str) or not project:
                continue
            digest = hashlib.md5(project.encode("utf-8")).hexdigest()
            result[digest] = project
            kaos = item.get("kaos")
            if isinstance(kaos, str) and kaos:
                result[f"{kaos}_{digest}"] = project
    return result


def _kimi_wire_context(path, legacy_projects):
    agent_dir = os.path.dirname(path)
    agents_dir = os.path.dirname(agent_dir)
    if os.path.basename(agents_dir) == "agents":
        session_dir = os.path.dirname(agents_dir)
        state = _load_json(os.path.join(session_dir, "state.json"), {})
        if not isinstance(state, dict):
            state = {}
        session_id = state.get("id") or os.path.basename(session_dir)
        project = state.get("cwd")
        return {
            "sid": str(session_id),
            "proj": project if isinstance(project, str) and project else None,
            "agent": os.path.basename(agent_dir),
        }
    session_dir = agent_dir
    work_dir_hash = os.path.basename(os.path.dirname(session_dir))
    return {
        "sid": os.path.basename(session_dir),
        "proj": legacy_projects.get(work_dir_hash),
        "agent": "main",
    }


def _kimi_events(message, scope="main"):
    if not isinstance(message, dict):
        return
    msg_type = message.get("type")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return
    if msg_type == "SubagentEvent":
        agent = payload.get("agent_id") or payload.get("parent_tool_call_id") \
            or payload.get("task_tool_call_id")
        child_scope = f"{scope}/{agent}" if isinstance(agent, str) and agent else scope
        yield from _kimi_events(payload.get("event"), child_scope)
    elif msg_type == "StatusUpdate":
        yield scope, payload


def _kimi_token(value):
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _kimi_datetime(record, key):
    value = record.get(key) if isinstance(record, dict) else None
    try:
        epoch = float(value)
        if not math.isfinite(epoch):
            return None
        if epoch > 100_000_000_000:
            epoch /= 1000
        return datetime.fromtimestamp(epoch).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        parsed = parse_ts(value) if isinstance(value, str) else None
        return parsed.astimezone() if parsed is not None else None


def _scan_kimi_wire(path, exclude=None, seen=None):
    """exclude:记录键多重集,命中就跳过并抵扣(根 wire 去掉 agent wire 的镜像)。
    seen:传进来就把本文件的记录键计进去,供上层构造 exclude。

    键只在同一种记录形态内可比:protocol 1.5 的 usage.record 没有 id,只能按
    时刻+模型+四个 token 值定身份;protocol 1 有 message_id 就用它。跨形态的
    镜像(根 wire 是 protocol 1、agent wire 是 1.5)认不出来,不在此列。"""
    days = {}
    seen_messages = set()
    # 绝大多数会话没有根 wire 镜像,这时一条记录键都不用建。
    track = exclude is not None or seen is not None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if ('"usage.record"' not in line and '"StatusUpdate"' not in line
                        and '"SubagentEvent"' not in line):
                    continue
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "usage.record":
                    usage = record.get("usage")
                    dt = _kimi_datetime(record, "time")
                    if not isinstance(usage, dict) or dt is None:
                        continue
                    inp = _kimi_token(usage.get("inputOther"))
                    out = _kimi_token(usage.get("output"))
                    cr = _kimi_token(usage.get("inputCacheRead"))
                    cw = _kimi_token(usage.get("inputCacheCreation"))
                    if inp + out + cr + cw == 0:
                        continue
                    model = record.get("model")
                    if not isinstance(model, str) or not model.strip():
                        model = None
                    if track:
                        key = ("u", round(dt.timestamp() * 1000), model, inp, out, cr, cw)
                        if exclude:
                            left = exclude.get(key, 0)
                            if left > 0:
                                exclude[key] = left - 1
                                continue
                        if seen is not None:
                            seen[key] = seen.get(key, 0) + 1
                    day = days.setdefault(dt.date().isoformat(), _empty_token_day())
                    _add_token_usage(day, inp, out, cr, cw, model=model)
                    day["hours"][dt.hour] += inp + out + cr + cw
                    continue

                dt = _kimi_datetime(record, "timestamp")
                if dt is None:
                    continue
                message = record.get("message")
                for scope, payload in _kimi_events(message):
                    usage = payload.get("token_usage")
                    if not isinstance(usage, dict):
                        continue
                    message_id = payload.get("message_id")
                    if isinstance(message_id, str) and message_id:
                        dedup_key = f"{scope}:{message_id}"
                        if dedup_key in seen_messages:
                            continue
                        seen_messages.add(dedup_key)
                    else:
                        message_id = None
                    inp = _kimi_token(usage.get("input_other"))
                    out = _kimi_token(usage.get("output"))
                    cr = _kimi_token(usage.get("input_cache_read"))
                    cw = _kimi_token(usage.get("input_cache_creation"))
                    if inp + out + cr + cw == 0:
                        continue
                    if track:
                        key = (("m", scope, message_id) if message_id else
                               ("t", round(dt.timestamp() * 1000), scope, inp, out, cr, cw))
                        if exclude:
                            left = exclude.get(key, 0)
                            if left > 0:
                                exclude[key] = left - 1
                                continue
                        if seen is not None:
                            seen[key] = seen.get(key, 0) + 1
                    day = days.setdefault(dt.date().isoformat(), _empty_token_day())
                    _add_token_usage(day, inp, out, cr, cw)
                    day["hours"][dt.hour] += inp + out + cr + cw
    except OSError:
        return {}
    return days


def scan_kimicode(bounds, cache):
    ledger_touch("kimicode")
    fc = cache.setdefault("kimicode", {})
    B = _empty_token_ranges()
    groups = _kimi_wire_groups()
    files = _kimi_wire_files(groups)
    if not files:
        if fc:
            fc.clear()
            cache["_dirty"] = True

    projects = _kimi_project_map()
    mirrors = _kimi_mirror_sources(groups)
    stale = set(fc)
    changed = False
    for path in files:
        stale.discard(path)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
        mirror_of = mirrors.get(path)
        if mirror_of:
            # 排除表来自 agent wire,它们一变根 wire 就得重算,否则镜像抵扣会错位。
            signature = f"{signature}:{_kimi_group_signature(mirror_of)}"
        entry = fc.get(path)
        context = _kimi_wire_context(path, projects)
        if (not isinstance(entry, dict) or entry.get("sig") != signature
                or entry.get("parser_version") != _KIMI_PARSER_VERSION):
            fc[path] = {
                "sig": signature,
                "days": _scan_kimi_wire(
                    path, exclude=_kimi_record_counts(mirror_of) if mirror_of else None),
                "sid": context["sid"],
                "proj": context["proj"],
                "agent": context["agent"],
                "parser_version": _KIMI_PARSER_VERSION,
            }
            changed = True
        elif any(entry.get(key) != context[key] for key in ("sid", "proj", "agent")):
            entry.update(context)
            changed = True

    for path in stale:
        fc.pop(path, None)
        changed = True

    live_days = {}
    live_sessions = {}
    live_projects = {}
    for path, entry in fc.items():
        if not isinstance(entry, dict):
            continue
        for day_key, day in entry.get("days", {}).items():
            try:
                date.fromisoformat(day_key)
            except (TypeError, ValueError):
                continue
            _merge_live_token_day(live_days.setdefault(day_key, _empty_token_day()), day)
            session = entry.get("sid") or path
            live_sessions.setdefault(day_key, set()).add(session)
            project = entry.get("proj")
            if isinstance(project, str) and project:
                live_projects.setdefault(day_key, set()).add(project)

    for day_key, day in live_days.items():
        day["sessions"] = sorted(live_sessions.get(day_key, set()))
        day["projects"] = sorted(live_projects.get(day_key, set()))

    for day_key, day in ledger_reconcile("kimicode", live_days).items():
        try:
            local_day = date.fromisoformat(day_key)
        except (TypeError, ValueError):
            continue
        for range_key in classify_date(local_day, bounds):
            _merge_token_day(B[range_key], day)
            B[range_key]["sessions"].update(day.get("sessions", []))
    if changed:
        cache["_dirty"] = True
    return {"ranges": B}


