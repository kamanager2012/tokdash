import os
import sqlite3 as _sq
from datetime import datetime, date

from core.config import (
    HOME,
    HERMES_DB,
    RANGE_KEYS,
    TOKEN_FIELDS,
    _sqlite_ro_uri,
    _sqlite_signature,
    classify_date,
    token_total,
)
from core.pricing import _model_identity_id
from core.storage import (
    _add_model_usage,
    _add_token_usage,
    ledger_reconcile,
    ledger_touch,
)


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


def _scan_hermes_db(db_path, _sq_module=_sq):
    days = {}
    try:
        conn = _sq_module.connect(_sqlite_ro_uri(db_path), uri=True)
        conn.row_factory = _sq_module.Row

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


def scan_hermes(bounds, cache):
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
