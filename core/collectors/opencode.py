import os
import glob
import json
import sqlite3
from datetime import datetime, date

from core.config import (
    OPENCODE_DIR,
    OPENCODE_DB,
    OPENCODE_DATA_DIR,
    OPENCODE_DATA_DIRS,
    _empty_token_day,
    _empty_token_ranges,
    _existing_dirs,
    _first_existing_file,
    _path_candidates,
    _sqlite_ro_uri,
    _sqlite_signature,
    classify_date,
    token_total,
)
from core.pricing import (
    _pricing_id,
    _raw_price,
)
from core.storage import (
    _add_model_usage,
    _add_token_usage,
    _merge_live_token_day,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)

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
