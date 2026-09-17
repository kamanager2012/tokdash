import os
import glob
import json
import sqlite3
from datetime import datetime, date

from core.config import (
    HOME,
    APPDATA,
    LOCALAPPDATA,
    RANGE_KEYS,
    TOKEN_FIELDS,
    HERMES_DB,
    _first_existing_file,
    _path_candidates,
    _qoder_ide_db_path,
    _sqlite_ro_uri,
    _sqlite_signature,
    classify_date,
    parse_ts,
    token_total,
)
from core.pricing import (
    _model_identity_id,
    price_for,
)
from core.storage import (
    _add_model_usage,
    _add_token_usage,
    ledger_reconcile,
    ledger_touch,
)

# ---------- Qoder ----------
# QoderWork SQLite:~/Library/Application Support/QoderWork/data/agents.db
# messages.metadata 含 durationMs / numTurns, sub_chats.ext 含上下文快照。
_QODER_DB = os.path.join(HOME, "Library", "Application Support", "QoderWork", "data", "agents.db")
QODER_DB_PATHS = _path_candidates(
    "TOKEI_QODER_DB", _QODER_DB,
    os.path.join(APPDATA, "QoderWork", "data", "agents.db"),
    os.path.join(LOCALAPPDATA, "QoderWork", "data", "agents.db"))


def _qoder_db_path():
    return _first_existing_file(_path_candidates("TOKEI_QODER_DB", _QODER_DB, *QODER_DB_PATHS))


def scan_qoder(bounds, cache):
    import sqlite3 as _sqlite3
    ledger_touch("qoderwork")
    fc = cache.setdefault("qoder", {})
    changed = False

    # --- Part 1: DB (all queries cached together by sig) ---
    db_days = {}
    sub_chat_days = {}  # date_str → count
    model = None
    qoder_db = _qoder_db_path()
    if qoder_db:
        sqlite_sig = _sqlite_signature(qoder_db)
        sig = f"{os.path.realpath(qoder_db)}|{sqlite_sig}" if sqlite_sig else None

        entry = fc.get("db")
        if sig and (not entry or entry.get("sig") != sig):
            conn = None
            try:
                conn = _sqlite3.connect(_sqlite_ro_uri(qoder_db), uri=True, timeout=1)
                conn.execute("PRAGMA query_only=ON")
                # messages: calls, sessions, tokens, duration, turns
                # 只统计 assistant 行:user 行也可能带 metadata,会虚增任务数
                for row in conn.execute("""
                    SELECT date(created_at,'unixepoch','localtime') as day,
                           COUNT(*) as calls,
                           COUNT(DISTINCT chat_id) as sessions,
                           COALESCE(SUM(json_extract(metadata,'$.inputTokens')),0),
                           COALESCE(SUM(json_extract(metadata,'$.outputTokens')),0),
                           COALESCE(SUM(json_extract(metadata,'$.durationMs')),0),
                           COALESCE(SUM(json_extract(metadata,'$.numTurns')),0)
                    FROM messages WHERE metadata!='{}' AND role='assistant'
                    GROUP BY day
                """):
                    dk, calls, sessions, ti, to_, dur, turns = row
                    if dk:
                        db_days[dk] = {"calls": calls, "sessions": sessions,
                                       "in": int(ti or 0), "out": int(to_ or 0),
                                       "duration": int(dur or 0), "turns": int(turns or 0),
                                       "ctx_ratio": 0.0, "hours": [0] * 24}
                for row in conn.execute("""
                    SELECT date(created_at,'unixepoch','localtime') as day,
                           CAST(strftime('%H',created_at,'unixepoch','localtime') AS INTEGER),
                           COALESCE(SUM(json_extract(metadata,'$.inputTokens')),0) +
                           COALESCE(SUM(json_extract(metadata,'$.outputTokens')),0)
                    FROM messages WHERE metadata!='{}' AND role='assistant'
                    GROUP BY day, strftime('%H',created_at,'unixepoch','localtime')
                """):
                    dk, hour, tokens = row
                    if dk in db_days and hour is not None:
                        db_days[dk]["hours"][int(hour)] += int(tokens or 0)
                # sub_chats: ctx percentage per day
                for row in conn.execute("""
                    SELECT date(created_at,'unixepoch','localtime') as day,
                           AVG(CASE WHEN json_extract(ext,'$.contextUsageSnapshot.percentage')>0
                                    THEN json_extract(ext,'$.contextUsageSnapshot.percentage') END)
                    FROM sub_chats
                    WHERE ext IS NOT NULL AND ext != '{}'
                    GROUP BY day
                """):
                    dk, ctx_pct = row
                    if dk and ctx_pct and dk in db_days:
                        db_days[dk]["ctx_ratio"] = float(ctx_pct)
                # sub_chats: count per day (for sub_agents metric)
                for row in conn.execute("""
                    SELECT date(created_at,'unixepoch','localtime') as day, COUNT(*)
                    FROM sub_chats WHERE created_at IS NOT NULL
                    GROUP BY day
                """):
                    if row[0]:
                        sub_chat_days[row[0]] = int(row[1])
                # model level
                mrow = conn.execute("SELECT value FROM app_settings WHERE key='modelLevel'").fetchone()
                if mrow:
                    model = mrow[0].strip('"')
            except Exception:
                pass
            finally:
                if conn is not None:
                    conn.close()
            fc["db"] = {"sig": sig, "days": db_days,
                        "sub_chat_days": sub_chat_days, "model": model}
            changed = True
        else:
            db_days = (entry or {}).get("days", {})
            sub_chat_days = (entry or {}).get("sub_chat_days", {})
            model = (entry or {}).get("model")
    elif "db" in fc:
        fc.pop("db", None)
        changed = True

    # --- 汇总 DB 数据 ---
    B = {k: {"in": 0, "out": 0, "sessions": 0, "calls": 0, "sub_agents": 0,
             "duration": 0, "turns": 0, "ctx_sum": 0.0, "ctx_count": 0}
         for k in RANGE_KEYS}

    # 天级整取整用:sub_chats 计数并入 day dict,连同会话计数一起进账本
    live_days = {}
    for dk, db_day in db_days.items():
        day = dict(db_day)
        day["sub_agents"] = int(sub_chat_days.get(dk, 0) or 0)
        live_days[dk] = day

    for dk, day in ledger_reconcile("qoderwork", live_days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        ks = classify_date(d, bounds)
        if not ks:
            continue

        calls = day.get("calls", 0)
        sessions = day.get("sessions", 0)
        duration = day.get("duration", 0)
        turns = day.get("turns", 0)
        ctx_ratio = day.get("ctx_ratio", 0)

        for k in ks:
            b = B[k]
            b["in"] += day.get("in", 0); b["out"] += day.get("out", 0)
            b["sessions"] += sessions; b["calls"] += calls
            b["sub_agents"] += day.get("sub_agents", 0)
            b["duration"] += duration; b["turns"] += turns
            if ctx_ratio > 0:
                b["ctx_sum"] += ctx_ratio * calls
                b["ctx_count"] += calls

    if changed:
        cache["_dirty"] = True
    return {"ranges": B, "model": model}


# ---------- Qoder IDE ----------
# Qoder IDE: SQLite DB ~/Library/Application Support/Qoder/SharedClientCache/cache/db/local.db
# chat_message 表: token_info(JSON明文), model_info(JSON明文), gmt_create(毫秒时间戳)


def _empty_qoder_ide():
    ranges = {k: {"in": 0, "out": 0, "cached": 0, "sessions": 0, "sub_agents": 0,
                  "calls": 0, "messages": 0, "duration": 0} for k in RANGE_KEYS}
    return {"ranges": ranges, "model": None}


def scan_qoder_ide(bounds, cache):
    import sqlite3 as _sq
    fc = cache.setdefault("qoder_ide", {})
    empty = _empty_qoder_ide()

    ledger_touch("qoder_ide")
    qoder_ide_db = _qoder_ide_db_path()
    if not qoder_ide_db or not os.path.exists(qoder_ide_db):
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return empty

    sqlite_sig = _sqlite_signature(qoder_ide_db)
    if not sqlite_sig:
        return empty
    sig = f"{os.path.realpath(qoder_ide_db)}|{sqlite_sig}"

    entry = fc.get("data")
    if not entry or entry.get("sig") != sig:
        days = {}  # date_str → {in, out, cached, session_ids, sub_agent_ids, calls, messages, duration}
        latest_model = None
        conn = None
        try:
            conn = _sq.connect(_sqlite_ro_uri(qoder_ide_db), uri=True, timeout=1)
            conn.execute("PRAGMA query_only=ON")
            # token 用量 & 计数 per day
            for row in conn.execute("""
                SELECT date(gmt_create/1000, 'unixepoch', 'localtime') as day,
                       COALESCE(SUM(json_extract(token_info, '$.prompt_tokens')), 0),
                       COALESCE(SUM(json_extract(token_info, '$.completion_tokens')), 0),
                       COALESCE(SUM(json_extract(token_info, '$.cached_tokens')), 0),
                       COUNT(DISTINCT request_id),
                       COUNT(*)
                FROM chat_message
                WHERE token_info IS NOT NULL AND token_info != ''
                GROUP BY day
            """):
                dk, ti, to_, cached, calls, msgs = row
                if not dk:
                    continue
                days[dk] = {"in": int(ti), "out": int(to_), "cached": int(cached),
                            "session_ids": [], "sub_agent_ids": [],
                            "calls": int(calls), "messages": int(msgs), "duration": 0,
                            "hours": [0] * 24}
            for row in conn.execute("""
                SELECT date(gmt_create/1000, 'unixepoch', 'localtime') as day,
                       CAST(strftime('%H', gmt_create/1000, 'unixepoch', 'localtime') AS INTEGER),
                       COALESCE(SUM(json_extract(token_info, '$.prompt_tokens')), 0) +
                       COALESCE(SUM(json_extract(token_info, '$.completion_tokens')), 0)
                FROM chat_message
                WHERE token_info IS NOT NULL AND token_info != ''
                GROUP BY day, strftime('%H', gmt_create/1000, 'unixepoch', 'localtime')
            """):
                dk, hour, tokens = row
                if dk in days and hour is not None:
                    days[dk]["hours"][int(hour)] += int(tokens or 0)
            # collect session_ids per day, split by type (user vs sub-agent)
            sub_agent_sids = set()
            try:
                for row in conn.execute("""
                    SELECT session_id FROM chat_session
                    WHERE session_type LIKE 'agent_sub_%'
                """):
                    sub_agent_sids.add(row[0])
            except Exception:
                pass
            for row in conn.execute("""
                SELECT date(gmt_create/1000, 'unixepoch', 'localtime') as day,
                       session_id
                FROM chat_message
                WHERE token_info IS NOT NULL AND token_info != ''
                GROUP BY day, session_id
            """):
                dk, sid = row
                if dk and dk in days and sid:
                    if sid in sub_agent_sids:
                        days[dk]["sub_agent_ids"].append(sid)
                    else:
                        days[dk]["session_ids"].append(sid)
            # duration per day (sum of per-request time spans)
            for row in conn.execute("""
                SELECT date(min_ts/1000, 'unixepoch', 'localtime') as day,
                       SUM(max_ts - min_ts) / 1000 as dur_sec
                FROM (SELECT request_id, MIN(gmt_create) as min_ts, MAX(gmt_create) as max_ts
                      FROM chat_message GROUP BY request_id HAVING COUNT(*) > 1) sub
                GROUP BY day
            """):
                dk, dur = row
                if dk and dk in days:
                    days[dk]["duration"] = int(dur)
            # latest model
            row = conn.execute("""
                SELECT json_extract(model_info, '$.model_key') FROM chat_message
                WHERE model_info IS NOT NULL AND model_info != ''
                ORDER BY gmt_create DESC LIMIT 1
            """).fetchone()
            if row and row[0]:
                latest_model = row[0]
        except Exception:
            pass
        finally:
            if conn is not None:
                conn.close()

        fc["data"] = {"sig": sig, "days": days, "model": latest_model}
        cache["_dirty"] = True
        entry = fc["data"]

    # 按时间范围聚合（sessions/sub_agents 用 set 去重，避免跨天会话被多算）
    # 仅 enabled 且正常扫描才会走到这里,disabled 分支在上方早已返回,绝不触碰账本
    B = {k: {"in": 0, "out": 0, "cached": 0, "sessions": 0, "sub_agents": 0,
             "calls": 0, "messages": 0, "duration": 0} for k in RANGE_KEYS}
    session_sets = {k: set() for k in RANGE_KEYS}
    sub_agent_sets = {k: set() for k in RANGE_KEYS}

    for dk, day in ledger_reconcile("qoder_ide", entry.get("days", {})).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in classify_date(d, bounds):
            b = B[k]
            b["in"] += day.get("in", 0)
            b["out"] += day.get("out", 0)
            b["cached"] += day.get("cached", 0)
            b["calls"] += day.get("calls", 0)
            b["messages"] += day.get("messages", 0)
            b["duration"] += day.get("duration", 0)
            for sid in day.get("session_ids") or []:
                session_sets[k].add(sid)
            for sid in day.get("sub_agent_ids") or []:
                sub_agent_sets[k].add(sid)

    for k in RANGE_KEYS:
        B[k]["sessions"] = len(session_sets[k])
        B[k]["sub_agents"] = len(sub_agent_sets[k])

    return {"ranges": B, "model": entry.get("model")}


# ---------- Hermes ----------
# SQLite: ~/.hermes/state.db (旧布局) + ~/.hermes/profiles/*/state.db (profile 布局)
def _hermes_db_paths():
    paths = []
    if os.path.isfile(HERMES_DB):
        paths.append(HERMES_DB)
    profiles = os.path.join(HOME, ".hermes", "profiles")
    if os.path.isdir(profiles):
        for p in os.listdir(profiles):
            db = os.path.join(profiles, p, "state.db")
            if os.path.isfile(db):
                paths.append(db)
    return paths


def _scan_hermes_db(db_path, _sq):
    days = {}
    try:
        conn = _sq.connect(_sqlite_ro_uri(db_path), uri=True)
        conn.row_factory = _sq.Row

        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "sessions" not in tables:
            conn.close()
            return days

        def columns(table):
            return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}

        def expr(alias, available, name, fallback="0"):
            if name in available:
                return f'{alias}."{name}"'
            return fallback

        session_columns = columns("sessions")
        session_query = f"""
            SELECT s.id AS session_id,
                   {expr('s', session_columns, 'started_at')} AS started_at,
                   {expr('s', session_columns, 'model', "''")} AS model,
                   {expr('s', session_columns, 'input_tokens')} AS input_tokens,
                   {expr('s', session_columns, 'output_tokens')} AS output_tokens,
                   {expr('s', session_columns, 'cache_read_tokens')} AS cache_read_tokens,
                   {expr('s', session_columns, 'cache_write_tokens')} AS cache_write_tokens,
                   {expr('s', session_columns, 'reasoning_tokens')} AS reasoning_tokens,
                   {expr('s', session_columns, 'estimated_cost_usd')} AS estimated_cost_usd,
                   {expr('s', session_columns, 'actual_cost_usd', 'NULL')} AS actual_cost_usd
            FROM sessions s
        """
        sessions = {row["session_id"]: dict(row) for row in conn.execute(session_query)}

        # Hermes 0.19 / schema v22 会把旧表改名为 session_model_usage_v21，再创建
        # 带 task 维度的新表。旧库含孤立用量行时迁移会被外键约束中断，两张表会
        # 同时保留；因此两张都读，并按完整主键去重。
        usage_rows = {}
        for table in ("session_model_usage_v21", "session_model_usage"):
            if table not in tables:
                continue
            usage_columns = columns(table)
            if "session_id" not in usage_columns:
                continue
            usage_query = f"""
                SELECT u.session_id AS session_id,
                       {expr('u', usage_columns, 'model', "''")} AS model,
                       {expr('u', usage_columns, 'billing_provider', "''")} AS billing_provider,
                       {expr('u', usage_columns, 'billing_base_url', "''")} AS billing_base_url,
                       {expr('u', usage_columns, 'billing_mode', "''")} AS billing_mode,
                       {expr('u', usage_columns, 'task', "''")} AS task,
                       {expr('u', usage_columns, 'input_tokens')} AS input_tokens,
                       {expr('u', usage_columns, 'output_tokens')} AS output_tokens,
                       {expr('u', usage_columns, 'cache_read_tokens')} AS cache_read_tokens,
                       {expr('u', usage_columns, 'cache_write_tokens')} AS cache_write_tokens,
                       {expr('u', usage_columns, 'reasoning_tokens')} AS reasoning_tokens,
                       {expr('u', usage_columns, 'estimated_cost_usd')} AS estimated_cost_usd,
                       {expr('u', usage_columns, 'actual_cost_usd', 'NULL')} AS actual_cost_usd,
                       {expr('u', usage_columns, 'first_seen', 'NULL')} AS first_seen,
                       {expr('u', usage_columns, 'last_seen', 'NULL')} AS last_seen
                FROM "{table}" u
            """
            for row in conn.execute(usage_query):
                item = dict(row)
                key = tuple(item.get(name) or "" for name in (
                    "session_id", "model", "billing_provider", "billing_base_url",
                    "billing_mode", "task"))
                previous = usage_rows.get(key)
                if previous and token_total({
                    "in": previous.get("input_tokens", 0),
                    "out": previous.get("output_tokens", 0),
                    "cr": previous.get("cache_read_tokens", 0),
                    "cw": previous.get("cache_write_tokens", 0),
                    "reason": previous.get("reasoning_tokens", 0),
                }) > token_total({
                    "in": item.get("input_tokens", 0),
                    "out": item.get("output_tokens", 0),
                    "cr": item.get("cache_read_tokens", 0),
                    "cw": item.get("cache_write_tokens", 0),
                    "reason": item.get("reasoning_tokens", 0),
                }):
                    continue
                usage_rows[key] = item

        records = list(usage_rows.values())
        main_usage_sessions = {
            row.get("session_id") for row in records if not (row.get("task") or "")}

        # 没有用量明细表或主循环明细缺失时，回退到 sessions 汇总。这样既兼容
        # 老版本，也不会把 v22 的主循环行与 sessions 再算一次。
        for session_id, session in sessions.items():
            if session_id in main_usage_sessions:
                continue
            records.append({
                **session,
                "task": "",
                "first_seen": session.get("started_at"),
                "last_seen": session.get("started_at"),
            })

        def row_cost(row):
            actual = row.get("actual_cost_usd")
            return float(actual if actual is not None else row.get("estimated_cost_usd", 0) or 0)

        records_by_session = {}
        for row in records:
            records_by_session.setdefault(row.get("session_id"), []).append(row)
        for session_id, session_records in records_by_session.items():
            session = sessions.get(session_id)
            if not session or any(row_cost(row) for row in session_records):
                continue
            fallback_cost = row_cost(session)
            if not fallback_cost:
                continue
            main_records = [row for row in session_records if not (row.get("task") or "")]
            if not main_records:
                continue
            target = next(
                (row for row in main_records if row.get("model") == session.get("model")),
                main_records[0],
            )
            target["actual_cost_usd"] = session.get("actual_cost_usd")
            target["estimated_cost_usd"] = session.get("estimated_cost_usd")

        session_first_seen = {}
        for row in records:
            session_id = row.get("session_id")
            if not session_id:
                continue
            session = sessions.get(session_id) or {}
            timestamp = session.get("started_at") or row.get("first_seen") or row.get("last_seen")
            try:
                timestamp = float(timestamp)
                if timestamp > 100_000_000_000:
                    timestamp /= 1000.0
            except (TypeError, ValueError):
                continue
            if timestamp <= 0:
                continue
            session_first_seen[session_id] = min(
                timestamp, session_first_seen.get(session_id, timestamp))

        day_sessions = {}
        for row in records:
            session_id = row.get("session_id")
            timestamp = session_first_seen.get(session_id)
            if timestamp is None:
                continue
            local_dt = datetime.fromtimestamp(timestamp).astimezone()
            dk = local_dt.date().isoformat()
            day = days.setdefault(dk, {"in": 0, "out": 0, "cr": 0, "cw": 0,
                                       "reason": 0, "cost": 0.0, "sessions": 0,
                                       "models": {}, "hours": [0] * 24})
            inp = int(row.get("input_tokens") or 0)
            out = int(row.get("output_tokens") or 0)
            cr = int(row.get("cache_read_tokens") or 0)
            cw = int(row.get("cache_write_tokens") or 0)
            reason = int(row.get("reasoning_tokens") or 0)
            _add_token_usage(day, inp, out, cr, cw, reason, row_cost(row),
                             _model_identity_id(row.get("model")))
            day["hours"][local_dt.hour] += inp + out + cr + cw + reason
            if session_id in sessions:
                day_sessions.setdefault(dk, set()).add(session_id)

        for dk, session_ids in day_sessions.items():
            days[dk]["sessions"] = len(session_ids)
        conn.close()
    except Exception:
        pass
    return days


# ---------- Qoder CLI ----------
# qodercli(独立 CLI,数据目录 ~/.qoder,与 Qoder IDE / QoderWork 无关)。
# transcript 中 usage 恒为空(服务端不下发 token),因此只采会话/活跃维度:
# 会话数、用户消息数(turns)、模型调用数(calls)、工具调用(tools)、活跃时长,
# token 为文本 chars/4 估算值(est)。
_QODERCLI_DIR = os.path.join(HOME, ".qoder", "projects")


def _qodercli_dir():
    return os.environ.get("TOKEI_QODERCLI_DIR", _QODERCLI_DIR)


def _empty_qodercli():
    ranges = {k: {"in": 0, "out": 0, "sessions": 0, "calls": 0, "sub_agents": 0,
                  "duration": 0, "turns": 0, "tools": 0, "est": 0,
                  "ctx_sum": 0.0, "ctx_count": 0} for k in RANGE_KEYS}
    return {"ranges": ranges, "model": None}


def _est_tokens(text):
    """CJK 感知估算:汉字/全角 ≈1 token,其余字符 ≈1/4 token(英文 4 字符/token 经验值)。"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef")
    return cjk + (len(text) - cjk) / 4


def _parse_qodercli_file(path):
    """解析单个 qodercli transcript,返回 {"days": {day: {...}}, "model": str|None}。"""
    days = {}
    model = None
    prev_ts = None
    seen_ids = set()
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            typ = row.get("type")
            if typ == "runtime-config":
                m = row.get("model")
                if m:
                    model = m
                continue
            if typ not in ("user", "assistant"):
                continue
            dt = parse_ts(row.get("timestamp") or "")
            if dt is None:
                continue
            dt = dt.astimezone()
            dk = dt.date().isoformat()
            day = days.setdefault(dk, {"calls": 0, "turns": 0, "tools": 0,
                                       "est": 0.0, "active": 0.0})
            ts = dt.timestamp()
            # 活跃时长:相邻事件间隔≤5min 才累计,排除挂机空档
            if prev_ts is not None:
                gap = ts - prev_ts
                if 0 < gap <= 300:
                    day["active"] += gap
            prev_ts = ts
            content = (row.get("message") or {}).get("content")
            if typ == "assistant":
                # 一次模型响应按内容块拆成多行(共享 message.id),去重后才是真实调用数
                mid = (row.get("message") or {}).get("id")
                if not mid or mid not in seen_ids:
                    if mid:
                        seen_ids.add(mid)
                    day["calls"] += 1
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type")
                        if bt == "tool_use":
                            day["tools"] += 1
                            try:
                                day["est"] += _est_tokens(json.dumps(block.get("input") or {}, ensure_ascii=False))
                            except (TypeError, ValueError):
                                pass
                        elif bt == "text":
                            day["est"] += _est_tokens(block.get("text"))
                        elif bt == "thinking":
                            day["est"] += _est_tokens(block.get("thinking"))
            elif not row.get("isMeta") and not row.get("isSidechain"):
                # 只统计真实用户输入,跳过命令回显/系统注入
                if isinstance(content, str) and content and not content.startswith("<"):
                    day["turns"] += 1
                    day["est"] += _est_tokens(content)
    return {"days": days, "model": model}


def scan_qodercli(bounds, cache):
    ledger_touch("qodercli")
    fc = cache.setdefault("qodercli", {})
    root = _qodercli_dir()
    paths = []
    if os.path.isdir(root):
        paths = glob.glob(os.path.join(root, "*", "*.jsonl"))
        paths += glob.glob(os.path.join(root, "*", "transcript", "*.jsonl"))
        paths += glob.glob(os.path.join(root, "*", "*", "subagents", "*.jsonl"))

    stale = set(fc)
    stale.discard("_model")
    latest_model = fc.get("_model")
    latest_mtime = -1
    for path in paths:
        stale.discard(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig = f"{st.st_size}|{st.st_mtime_ns}"
        entry = fc.get(path)
        if isinstance(entry, dict) and entry.get("sig") == sig:
            if entry.get("model") and st.st_mtime_ns > latest_mtime:
                latest_mtime = st.st_mtime_ns
                latest_model = entry["model"]
            continue
        try:
            parsed = _parse_qodercli_file(path)
        except OSError:
            continue
        fc[path] = {"sig": sig, "days": parsed["days"], "model": parsed["model"],
                    "sub": (os.sep + "subagents" + os.sep) in path}
        cache["_dirty"] = True
        if parsed["model"] and st.st_mtime_ns > latest_mtime:
            latest_mtime = st.st_mtime_ns
            latest_model = parsed["model"]
    for path in stale:
        fc.pop(path, None)
        cache["_dirty"] = True
    if latest_model and fc.get("_model") != latest_model:
        fc["_model"] = latest_model
        cache["_dirty"] = True

    B = _empty_qodercli()["ranges"]
    # 会话/消息/子agent 维度只能来自现存 transcript;token 类维度走账本
    live_days = {}
    for path, entry in fc.items():
        if path == "_model" or not isinstance(entry, dict):
            continue
        is_sub = entry.get("sub", False)
        first_day = min(entry.get("days", {}), default=None)
        for dk, day in entry.get("days", {}).items():
            try:
                d = date.fromisoformat(dk)
            except ValueError:
                continue
            agg = live_days.setdefault(dk, {"calls": 0, "tools": 0, "est": 0, "duration": 0})
            agg["calls"] += day.get("calls", 0)
            agg["tools"] += day.get("tools", 0)
            agg["est"] += int(day.get("est", 0))
            agg["duration"] += int(day.get("active", 0.0) * 1000)
            ks = classify_date(d, bounds)
            if not ks:
                continue
            for k in ks:
                b = B[k]
                if is_sub:
                    # 子 agent transcript:不算人类会话/消息,首个活跃日计 1 个子 agent
                    if dk == first_day:
                        b["sub_agents"] += 1
                else:
                    b["sessions"] += 1
                    b["turns"] += day.get("turns", 0)

    for dk, day in ledger_reconcile("qodercli", live_days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in classify_date(d, bounds):
            b = B[k]
            b["calls"] += day.get("calls", 0)
            b["tools"] += day.get("tools", 0)
            b["est"] += int(day.get("est", 0))
            b["duration"] += int(day.get("duration", 0))
    return {"ranges": B, "model": fc.get("_model")}


def scan_hermes(bounds, cache):
    import sqlite3 as _sq
    ledger_touch("hermes")
    fc = cache.setdefault("hermes", {})
    changed = False

    db_paths = _hermes_db_paths()
    if not db_paths:
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": {k: {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0, "cost": 0.0,
                                "sessions": 0, "models": {}} for k in RANGE_KEYS}}

    stale = set(fc.keys())
    for db_path in db_paths:
        stale.discard(db_path)
        sig = _sqlite_signature(db_path)
        if not sig:
            continue
        entry = fc.get(db_path)
        if not entry or entry.get("sig") != sig:
            days = _scan_hermes_db(db_path, _sq)
            fc[db_path] = {"sig": sig, "days": days}
            changed = True
    for p in stale:
        fc.pop(p, None)
        changed = True

    B = {k: {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0, "cost": 0.0,
             "sessions": 0, "models": {}} for k in RANGE_KEYS}
    live_days = {}
    for db_path, entry in fc.items():
        for dk, day in entry.get("days", {}).items():
            try:
                date.fromisoformat(dk)
            except ValueError:
                continue
            agg = live_days.setdefault(
                dk, {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
                     "cost": 0.0, "sessions": 0, "models": {}, "hours": [0] * 24})
            agg["in"] += day.get("in", 0); agg["out"] += day.get("out", 0)
            agg["cr"] += day.get("cr", 0); agg["cw"] += day.get("cw", 0)
            agg["reason"] += day.get("reason", 0); agg["cost"] += day.get("cost", 0)
            agg["sessions"] += day.get("sessions", 0)
            for mn, mv in (day.get("models") or {}).items():
                _add_model_usage(agg["models"], mn, mv.get("in", 0), mv.get("out", 0),
                                 mv.get("cr", 0), mv.get("cw", 0),
                                 mv.get("reason", 0), mv.get("cost", 0))
            for hour, amount in enumerate((day.get("hours") or [])[:24]):
                agg["hours"][hour] += amount

    for dk, day in ledger_reconcile("hermes", live_days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in classify_date(d, bounds):
            b = B[k]
            b["in"] += day.get("in", 0); b["out"] += day.get("out", 0)
            b["cr"] += day.get("cr", 0); b["cw"] += day.get("cw", 0)
            b["reason"] += day.get("reason", 0); b["cost"] += day.get("cost", 0)
            b["sessions"] += day.get("sessions", 0)
            for mn, mv in (day.get("models") or {}).items():
                mm = b["models"].setdefault(
                    mn, {"in": 0, "out": 0, "cr": 0, "cw": 0,
                         "reason": 0, "cost": 0.0})
                for key in TOKEN_FIELDS:
                    mm[key] += mv.get(key, 0)
                mm["cost"] += mv.get("cost", 0)
    if changed:
        cache["_dirty"] = True
    return {"ranges": B}


# ---------- OpenClaw ----------
# SQLite: ~/.openclaw/state/openclaw.sqlite（新版）或 ~/.openclaw/tasks/runs.sqlite（旧版）
# Session JSONL: ~/.openclaw/agents/*/sessions/*.jsonl — token 用量
# ---------- Pi Coding Agent CLI ----------
# JSONL 文件: ~/.pi/agent/sessions/<encoded-cwd>/*.jsonl 或 ~/.omp/agent/sessions/<encoded-cwd>/*.jsonl
# assistant message 里保存 usage{input,output,cacheRead,cacheWrite,reasoningTokens,cost}。
