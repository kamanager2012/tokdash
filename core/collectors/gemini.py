import os
import glob
import json
import sqlite3
from datetime import datetime, date, timezone

from core.config import (
    GEMINI_DIR,
    GEMINI_DIRS,
    RANGE_KEYS,
    _empty_gemini,
    _path_candidates,
    classify_date,
    parse_ts,
    _sqlite_ro_uri,
    _sqlite_signature,
)
from core.pricing import gemini_price
from core.storage import (
    ledger_reconcile,
    ledger_touch,
)

# 日志:
# - Gemini CLI: ~/.gemini/tmp/<projectHash>/chats/{session-*.json,session-*.jsonl,<parent>/*.jsonl}
#   assistant 行 type=="gemini",tokens={input,output,cached,thoughts,total}
# - Antigravity CLI: ~/.gemini/antigravity-cli/conversations/<uuid>.db
#   gen_metadata 表存储 protobuf 逐步生成指标(包含 input, output, cached, thoughts, model, start_time)
def _gemini_session_files():
    files = []
    roots = _path_candidates("TOKEI_GEMINI_DIR", GEMINI_DIR, *GEMINI_DIRS)
    patterns = []
    for root in roots:
        patterns.extend((
            os.path.join(root, "*.db"),
            os.path.join(root, "**", "*.db"),
            os.path.join(root, "*", "chats", "session-*.json"),
            os.path.join(root, "*", "chats", "**", "*.jsonl"),
            os.path.join(root, "**", "session-*.json"),
            os.path.join(root, "**", "session-*.jsonl"),
        ))
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))
    return sorted(set(os.path.realpath(path) for path in files
                      if os.path.isfile(path) and not path.endswith("conversation_summaries.db")))


_PROTO_VARINT_MAX_BYTES = 10  # protobuf 规范:64 位整数最多 10 个字节


def _decode_proto_varint(data, offset):
    """坏数据不封顶会让 val 长成百万位大整数,每轮 |= 都是 O(n),整体退化成 O(n²)。"""
    val = 0
    shift = 0
    for _ in range(_PROTO_VARINT_MAX_BYTES):
        if offset >= len(data):
            break
        b = data[offset]
        offset += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, offset
        shift += 7
    raise ValueError("varint 超过 64 位,当坏数据处理")


def _parse_proto_fields(data):
    i = 0
    fields = []
    while i < len(data):
        try:
            key, i = _decode_proto_varint(data, i)
            field_num = key >> 3
            wire_type = key & 0x7
            if wire_type == 0:
                val, i = _decode_proto_varint(data, i)
            elif wire_type == 2:
                length, i = _decode_proto_varint(data, i)
                # 越界不能靠切片静默截短:截出来的碎片会被当成合法子消息继续解析。
                if length < 0 or i + length > len(data):
                    break
                val = data[i:i + length]
                i += length
            elif wire_type in (1, 5):
                width = 8 if wire_type == 1 else 4
                if i + width > len(data):
                    break
                val = data[i:i + width]
                i += width
            else:
                break
        except Exception:
            break
        fields.append((field_num, wire_type, val))
    return fields


# gen_metadata.data 的字段号是逆向出来的,没有官方 schema:
# 1 = 单次生成记录,其中 19=模型名, 4={2:输入(不含缓存), 3:输出, 5:缓存读, 9:思考},
# 9→4→1 = 生成开始时间(秒)。Google 一改编号这里就会静默解出错数,所以下面做了上界校验。
_ANTIGRAVITY_MAX_TOKENS = 100_000_000  # 单次生成的 token 上界,超了就是解析错位
_ANTIGRAVITY_MIN_TS = 1_577_836_800    # 2020-01-01,更早的时间戳必然是错位


def _antigravity_gen_step(record, fallback_ts=None):
    """解一条生成记录 → (model, input, output, cached, thoughts, ts_sec);解不出返回 None。"""
    model = "unknown"
    inp = out = cached = thoughts = 0
    ts_sec = None
    for sfn, swt, sval in _parse_proto_fields(record):
        if sfn == 19 and swt == 2:
            try:
                model = sval.decode("utf-8")
            except UnicodeDecodeError:
                pass
        elif sfn == 4 and swt == 2:
            for tfn, twt, tval in _parse_proto_fields(sval):
                if twt != 0:
                    continue
                if tfn == 2:
                    inp = tval
                elif tfn == 3:
                    out = tval
                elif tfn == 5:
                    cached = tval
                elif tfn == 9:
                    thoughts = tval
        elif sfn == 9 and swt == 2:
            for tfn, twt, tval in _parse_proto_fields(sval):
                if tfn == 4 and twt == 2:
                    for stfn, stwt, stval in _parse_proto_fields(tval):
                        if stfn == 1 and stwt == 0:
                            ts_sec = stval
    # 账本是逐日高水位,虚高数字一旦写进去就永久留着且无法纠正 —— 宁可丢也不能记错。
    if not isinstance(ts_sec, int) or not (_ANTIGRAVITY_MIN_TS <= ts_sec <= 1 << 34):
        ts_sec = fallback_ts
    if not isinstance(ts_sec, int) or not (_ANTIGRAVITY_MIN_TS <= ts_sec <= 1 << 34):
        return None
    if max(inp, out, cached, thoughts) > _ANTIGRAVITY_MAX_TOKENS:
        return None
    if not model or len(model) > 120 or not model.isprintable():
        model = "unknown"
    return model, inp, out, cached, thoughts, ts_sec


def _decode_packed_varints(data):
    values = []
    offset = 0
    while offset < len(data):
        value, offset = _decode_proto_varint(data, offset)
        values.append(value)
    return values


def _antigravity_step_timestamp(metadata):
    """Current Antigravity stores a google.protobuf.Timestamp in steps.metadata field 1."""
    for field_num, wire_type, value in _parse_proto_fields(metadata or b""):
        if field_num != 1 or wire_type != 2:
            continue
        for sub_num, sub_wire, sub_value in _parse_proto_fields(value):
            if sub_num == 1 and sub_wire == 0 \
                    and _ANTIGRAVITY_MIN_TS <= sub_value <= 1 << 34:
                return sub_value
    return None


def _load_antigravity_db(path):
    """从 Antigravity conversations/*.db 的 gen_metadata 表解析逐步 token 用量"""
    events = []
    max_ts = ""
    conn = None
    try:
        conn = sqlite3.connect(_sqlite_ro_uri(path), uri=True, timeout=1)
        rows = conn.execute("SELECT idx, data FROM gen_metadata ORDER BY idx ASC").fetchall()
        try:
            step_rows = conn.execute("SELECT idx, metadata FROM steps").fetchall()
        except sqlite3.Error:
            step_rows = []
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()

    step_timestamps = {
        int(step_idx): timestamp
        for step_idx, metadata in step_rows
        if (timestamp := _antigravity_step_timestamp(metadata)) is not None
    }

    for idx, data in rows:
        if not data:
            continue
        fields = _parse_proto_fields(data)
        referenced_steps = []
        for fn, wt, val in fields:
            if fn == 2 and wt == 2:
                try:
                    referenced_steps.extend(_decode_packed_varints(val))
                except ValueError:
                    pass
        fallback_ts = next((step_timestamps.get(step_idx) for step_idx in referenced_steps
                            if step_timestamps.get(step_idx) is not None), None)
        record_index = 0
        for fn, wt, val in fields:
            if fn != 1 or wt != 2:
                continue
            # 逐条兜异常:一行坏数据不该把同一个库里的好数据一起带走。
            try:
                step = _antigravity_gen_step(val, fallback_ts=fallback_ts)
            except Exception:
                step = None
            if step is None:
                continue
            model, inp, out, cached, thoughts, ts_sec = step
            iso_ts = datetime.fromtimestamp(ts_sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if iso_ts > max_ts:
                max_ts = iso_ts
            events.append({
                "id": f"{os.path.basename(path)}:{idx}:{record_index}",
                "timestamp": iso_ts,
                "model": model,
                "tokens": {
                    "input": inp + cached,
                    "output": out,
                    "cached": cached,
                    "thoughts": thoughts,
                },
            })
            record_index += 1

    if not events:
        return None
    sid = os.path.basename(path)
    if sid.endswith(".db"):
        sid = sid[:-3]
    return {
        "sid": sid,
        "updated": max_ts,
        "rank": 3,
        "events": events,
    }


def _gemini_apply_messages(message_map, messages, replace=False):
    if replace:
        message_map.clear()
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if message_id:
            message_map[str(message_id)] = message


def _load_gemini_usage_file(path):
    if path.endswith(".db"):
        return _load_antigravity_db(path)
    metadata = {}
    messages = {}
    rank = 2 if path.endswith(".jsonl") else 1
    try:
        if rank == 1:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                record = json.load(handle)
            if not isinstance(record, dict):
                return None
            metadata.update(record)
            _gemini_apply_messages(messages, record.get("messages"))
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(record, dict):
                        continue
                    rewind_id = record.get("$rewindTo")
                    if isinstance(rewind_id, str):
                        keys = list(messages)
                        if rewind_id in messages:
                            for message_id in keys[keys.index(rewind_id):]:
                                messages.pop(message_id, None)
                        else:
                            messages.clear()
                        continue
                    if isinstance(record.get("id"), str):
                        messages[record["id"]] = record
                        continue
                    updates = record.get("$set")
                    if isinstance(updates, dict):
                        if isinstance(updates.get("messages"), list):
                            _gemini_apply_messages(messages, updates["messages"], replace=True)
                        metadata.update(updates)
                        continue
                    pushed = record.get("$push")
                    if isinstance(pushed, dict):
                        _gemini_apply_messages(messages, pushed.get("messages"))
                        continue
                    if isinstance(record.get("sessionId"), str):
                        metadata.update(record)
                        _gemini_apply_messages(messages, record.get("messages"))
    except OSError:
        return None

    events = []
    for message_id, message in messages.items():
        tokens = message.get("tokens")
        if message.get("type") != "gemini" or not isinstance(tokens, dict):
            continue
        timestamp = message.get("timestamp")
        if not timestamp:
            continue
        events.append({
            "id": message_id,
            "timestamp": timestamp,
            "model": message.get("model") or "unknown",
            "tokens": {
                "input": int(tokens.get("input", 0) or 0),
                "output": int(tokens.get("output", 0) or 0),
                "cached": int(tokens.get("cached", 0) or 0),
                "thoughts": int(tokens.get("thoughts", 0) or 0),
            },
        })
    return {
        "sid": metadata.get("sessionId") or os.path.basename(path),
        "updated": metadata.get("lastUpdated") or "",
        "rank": rank,
        "events": events,
    }


def scan_gemini(bounds, cache):
    ledger_touch("gemini")
    fc = cache.setdefault("gemini", {})
    files = _gemini_session_files()
    if not files:
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return _empty_gemini()

    stale = set(fc)
    for path in files:
        stale.discard(path)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        # SQLite 的新数据可能全在 -wal 里,主库 mtime/size 一动不动 —— 只看主库会永不刷新。
        signature = (_sqlite_signature(path) if path.endswith(".db")
                     else f"{stat.st_mtime_ns}:{stat.st_size}")
        entry = fc.get(path)
        if entry and entry.get("sig") == signature:
            continue
        parsed = _load_gemini_usage_file(path)
        if parsed is None:
            continue
        parsed["sig"] = signature
        parsed["mtime"] = stat.st_mtime_ns
        fc[path] = parsed
        cache["_dirty"] = True

    for path in stale:
        fc.pop(path, None)
        cache["_dirty"] = True

    sessions = {}
    for path, entry in fc.items():
        sid = entry.get("sid") or path
        score = (int(entry.get("rank", 0)), entry.get("updated") or "", int(entry.get("mtime", 0)))
        current = sessions.get(sid)
        if current is None or score > current[0]:
            sessions[sid] = (score, entry)

    days = {}
    _price_cache = {}  # model → gemini_price() result; 8 unique models vs 21K events
    _date_cache = {}   # timestamp str → (dt.date().isoformat(), dt.hour)
    for sid, (_, entry) in sessions.items():
        for event in entry.get("events", []):
            ts = event.get("timestamp", "")
            date_info = _date_cache.get(ts)
            if date_info is None:
                dt = parse_ts(ts)
                if dt is None:
                    continue
                dt = dt.astimezone()
                date_info = (dt.date().isoformat(), dt.hour)
                _date_cache[ts] = date_info
            day_key, hour = date_info
            tokens = event.get("tokens") or {}
            model = event.get("model") or "unknown"
            inp = int(tokens.get("input", 0) or 0)
            out = int(tokens.get("output", 0) or 0)
            cached = int(tokens.get("cached", 0) or 0)
            thoughts = int(tokens.get("thoughts", 0) or 0)
            price = _price_cache.get(model)
            if price is None:
                price = gemini_price(model)
                _price_cache[model] = price
            cost = (max(inp - cached, 0) / 1e6 * price["in"]
                    + cached / 1e6 * price["cache_read"]
                    + (out + thoughts) / 1e6 * price["out"])
            day = days.setdefault(
                day_key, {"in": 0, "out": 0, "cached": 0, "thoughts": 0,
                          "cost": 0.0, "models": {}, "sessions": set(), "hours": [0] * 24})
            day["in"] += inp; day["out"] += out; day["cached"] += cached
            day["thoughts"] += thoughts; day["cost"] += cost; day["sessions"].add(sid)
            day["hours"][hour] += inp + out + thoughts
            model_usage = day["models"].setdefault(
                model, {"in": 0, "out": 0, "cached": 0, "thoughts": 0, "cost": 0.0})
            model_usage["in"] += inp; model_usage["out"] += out
            model_usage["cached"] += cached; model_usage["thoughts"] += thoughts
            model_usage["cost"] += cost

    B = {k: {"in": 0, "out": 0, "cached": 0, "thoughts": 0, "cost": 0.0,
             "models": {}, "sessions": set()}
         for k in RANGE_KEYS}
    # 会话数只能来自现存日志(被清日志无从归属)
    for dk, day in days.items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for key in classify_date(d, bounds):
            B[key]["sessions"].update(day.get("sessions", set()))

    for dk, day in ledger_reconcile("gemini", days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for key in classify_date(d, bounds):
            bucket = B[key]
            bucket["in"] += day.get("in", 0); bucket["out"] += day.get("out", 0)
            bucket["cached"] += day.get("cached", 0)
            bucket["thoughts"] += day.get("thoughts", 0); bucket["cost"] += day.get("cost", 0)
            for model, usage in (day.get("models") or {}).items():
                model_usage = bucket["models"].setdefault(
                    model, {"in": 0, "out": 0, "cached": 0,
                            "thoughts": 0, "cost": 0.0})
                for field in ("in", "out", "cached", "thoughts"):
                    model_usage[field] += usage.get(field, 0)
                model_usage["cost"] += usage.get("cost", 0)
    return {"ranges": B, "days": days}

