import os
import glob
import json
import time
from datetime import datetime, date

from core.config import (
    HOME,
    APPDATA,
    LOCALAPPDATA,
    CLAUDE_DIR,
    CLAUDE_QUOTA_CACHE,
    RANGE_KEYS,
    classify_date,
    parse_ts,
    _path_candidates,
    _existing_dirs,
    _load_json,
    _atomic_write_json,
)
from core.pricing import price_for
from core.storage import ledger_reconcile


def _claude_event_total(event):
    return sum(int(event.get(key, 0) or 0) for key in ("in", "out", "cr", "cw"))


def _prefer_claude_event(candidate, existing):
    candidate_sidechain = bool(candidate.get("sidechain"))
    existing_sidechain = bool(existing.get("sidechain"))
    if candidate_sidechain != existing_sidechain:
        return existing_sidechain
    candidate_total = _claude_event_total(candidate)
    existing_total = _claude_event_total(existing)
    if candidate_total != existing_total:
        return candidate_total > existing_total
    return float(candidate.get("cost", 0) or 0) > float(existing.get("cost", 0) or 0)


def _dedupe_claude_events(file_events):
    selected = []
    exact = {}
    by_message = {}

    for source, event in file_events:
        message_id = event.get("mid")
        request_id = event.get("request_id")
        index = None
        exact_key = None
        if message_id:
            exact_key = ("message", message_id, request_id)
            index = exact.get(exact_key)
            if index is None:
                for candidate_index in by_message.get(message_id, []):
                    existing = selected[candidate_index][1]
                    if event.get("sidechain") or existing.get("sidechain"):
                        index = candidate_index
                        break
        elif event.get("event_id"):
            exact_key = ("event", event["event_id"])
            index = exact.get(exact_key)

        if index is not None:
            if _prefer_claude_event(event, selected[index][1]):
                selected[index] = (source, event)
                if exact_key is not None:
                    exact[exact_key] = index
            continue

        index = len(selected)
        selected.append((source, event))
        if exact_key is not None:
            exact[exact_key] = index
        if message_id:
            by_message.setdefault(message_id, []).append(index)
    return selected


def scan_claude(bounds, cache):
    fc = cache.setdefault("claude", {})
    changed = False
    B = {k: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "models": {}, "sessions": set()}
         for k in RANGE_KEYS}
    cur_file, cur_mtime = None, -1.0
    if not os.path.isdir(CLAUDE_DIR):
        if fc:
            fc.clear()
            cache["_dirty"] = True
        return {"ranges": B, "cur": {"in": 0, "out": 0, "cr": 0, "cw": 0, "name": "-"}}

    today_d = bounds["today"].date()
    yest_d = bounds["yesterday"].date()
    week_d = bounds["week"].date()
    lw_start_d = bounds["last_week"].date()
    lw_end_d = bounds["last_week_end"].date()
    month_d = bounds["month"].date()
    year_d = bounds["year"].date()

    stale = set(fc.keys())

    for f in glob.glob(os.path.join(CLAUDE_DIR, "**", "*.jsonl"), recursive=True):
        stale.discard(f)
        try:
            st = os.stat(f)
        except OSError:
            continue
        mtime, size = st.st_mtime, st.st_size
        if mtime > cur_mtime:
            cur_mtime = mtime
            cur_file = f
        sig = f"{mtime}:{size}"
        entry = fc.get(f)
        if not entry or entry.get("sig") != sig:
            events = []
            proj = None
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line_number, line in enumerate(fh, 1):
                        if '"usage"' not in line:
                            continue
                        u = _claude_usage(line, want_dt=True)
                        if not u:
                            continue
                        events.append({
                            "in": u["in"], "out": u["out"], "cr": u["cr"], "cw": u["cw"],
                            "cost": u["cost"], "model": u.get("model") or "unknown",
                            "cwd": u.get("cwd"), "mid": u.get("mid"),
                            "request_id": u.get("request_id"), "event_id": u.get("event_id"),
                            "sidechain": bool(u.get("sidechain")), "timestamp": u["dt"].isoformat(),
                            "line": line_number,
                        })
                        if proj is None and u.get("cwd"):
                            proj = u["cwd"]
            except OSError:
                continue
            events = [event for _, event in _dedupe_claude_events((f, item) for item in events)]
            fc[f] = {"sig": sig, "events": events, "proj": proj}
            changed = True

    for p in stale:
        fc.pop(p, None)
        changed = True

    all_events = []
    for path, entry in fc.items():
        for event in entry.get("events", []):
            all_events.append((path, event))
    selected_events = _dedupe_claude_events(all_events)

    aggregates = {
        path: {"days": {}, "hours": [0] * 24, "day_hours": {}, "dh": set(),
               "proj": entry.get("proj")}
        for path, entry in fc.items()
    }
    for path, event in selected_events:
        dt = parse_ts(event.get("timestamp", ""))
        if dt is None:
            continue
        dt = dt.astimezone()
        day_key = dt.date().isoformat()
        aggregate = aggregates[path]
        if not aggregate["proj"] and event.get("cwd"):
            aggregate["proj"] = event["cwd"]
        day = aggregate["days"].setdefault(
            day_key, {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "models": {}})
        day["in"] += event["in"]; day["out"] += event["out"]
        day["cr"] += event["cr"]; day["cw"] += event["cw"]
        day["cost"] += event["cost"]
        model = event.get("model") or "unknown"
        model_usage = day["models"].setdefault(
            model, {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0})
        model_usage["in"] += event["in"]; model_usage["out"] += event["out"]
        model_usage["cr"] += event["cr"]; model_usage["cw"] += event["cw"]
        model_usage["cost"] += event["cost"]
        amount = _claude_event_total(event)
        aggregate["hours"][dt.hour] += amount
        aggregate["day_hours"].setdefault(day_key, [0] * 24)[dt.hour] += amount
        aggregate["dh"].add(f"{day_key}:{dt.hour}")

    for path, aggregate in aggregates.items():
        entry = fc[path]
        values = {
            "days": aggregate["days"], "hours": aggregate["hours"],
            "day_hours": aggregate["day_hours"], "dh": sorted(aggregate["dh"]),
            "proj": aggregate["proj"],
        }
        for key, value in values.items():
            if entry.get(key) != value:
                entry[key] = value
                changed = True

    if changed:
        cache["_dirty"] = True

    # Assembly: per-day → range buckets
    def classify(d):
        return classify_date(d, bounds)

    live_days = {}
    day_projects = {}
    for f, entry in fc.items():
        proj_name = os.path.basename((entry.get("proj") or "").rstrip("/"))
        for dk, day in entry.get("days", {}).items():
            agg = live_days.setdefault(
                dk, {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "models": {}})
            agg["in"] += day["in"]; agg["out"] += day["out"]
            agg["cr"] += day["cr"]; agg["cw"] += day["cw"]; agg["cost"] += day["cost"]
            if proj_name:
                day_projects.setdefault(dk, set()).add(proj_name)
            for mn, mv in day["models"].items():
                mm = agg["models"].setdefault(mn, {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0})
                mm["in"] += mv["in"]; mm["out"] += mv["out"]
                mm["cr"] += mv["cr"]; mm["cw"] += mv["cw"]; mm["cost"] += mv["cost"]
            try:
                d = date.fromisoformat(dk)
            except ValueError:
                continue
            for k in classify(d):
                B[k]["sessions"].add(f)
    for dk, names in day_projects.items():
        live_days[dk]["projects"] = sorted(names)[:3]

    for dk, day in ledger_reconcile("claude", live_days).items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in classify(d):
            b = B[k]
            b["in"] += day.get("in", 0); b["out"] += day.get("out", 0)
            b["cr"] += day.get("cr", 0); b["cw"] += day.get("cw", 0)
            b["cost"] += day.get("cost", 0.0)
            for mn, mv in (day.get("models") or {}).items():
                mm = b["models"].setdefault(mn, {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0})
                mm["in"] += mv.get("in", 0); mm["out"] += mv.get("out", 0)
                mm["cr"] += mv.get("cr", 0); mm["cw"] += mv.get("cw", 0)
                mm["cost"] += mv.get("cost", 0.0)

    # Current session: sum all days of the most recently modified file
    cur_in = cur_out = cur_cr = cur_cw = 0
    if cur_file:
        entry = fc.get(cur_file)
        if entry:
            for day in entry.get("days", {}).values():
                cur_in += day["in"]; cur_out += day["out"]
                cur_cr += day["cr"]; cur_cw += day["cw"]

    return {
        "ranges": B,
        "cur": {"in": cur_in, "out": cur_out, "cr": cur_cr, "cw": cur_cw,
                "name": os.path.basename(cur_file)[:8] if cur_file else "-"},
    }


def _claude_usage(line, want_dt=False):
    try:
        o = json.loads(line)
    except Exception:
        return None
    if o.get("type") != "assistant":
        return None
    dt = None
    if want_dt:
        dt = parse_ts(o.get("timestamp", ""))
        if dt is None:
            return None
        dt = dt.astimezone()
    msg = o.get("message", {})
    u = msg.get("usage")
    if not u:
        return None
    inp = u.get("input_tokens", 0) or 0
    out = u.get("output_tokens", 0) or 0
    cr = u.get("cache_read_input_tokens", 0) or 0
    cw = u.get("cache_creation_input_tokens", 0) or 0
    p = price_for(msg.get("model"))
    cc = u.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens")
    w1 = cc.get("ephemeral_1h_input_tokens")
    if w5 is None and w1 is None:
        write_cost = cw / 1e6 * p["write5m"]
    else:
        write_cost = (w5 or 0) / 1e6 * p["write5m"] + (w1 or 0) / 1e6 * p["write1h"]
    cost = inp / 1e6 * p["in"] + out / 1e6 * p["out"] + cr / 1e6 * p["cache_read"] + write_cost
    res = {"in": inp, "out": out, "cr": cr, "cw": cw, "cost": cost,
           "model": msg.get("model"), "cwd": o.get("cwd"), "mid": msg.get("id"),
           "request_id": o.get("requestId") or o.get("request_id"),
           "event_id": o.get("uuid"), "sidechain": o.get("isSidechain") is True}
    if want_dt:
        res["dt"] = dt
    return res


def fmt_reset(epoch):
    try:
        return datetime.fromtimestamp(int(epoch)).astimezone().strftime("%m-%d %H:%M")
    except Exception:
        return "?"


CLAUDE_CACHE = os.path.join(
    HOME, "Library", "Application Support", "Claude", "Cache", "Cache_Data"
)
CLAUDE_CACHE_DIRS = _path_candidates(
    "TOKEI_CLAUDE_CACHE_DIR", CLAUDE_CACHE,
    os.path.join(APPDATA, "Claude", "Cache", "Cache_Data"),
    os.path.join(LOCALAPPDATA, "Claude", "Cache", "Cache_Data"))


def _claude_cache_records():
    cache_dirs = _existing_dirs(
        _path_candidates("TOKEI_CLAUDE_CACHE_DIR", CLAUDE_CACHE, *CLAUDE_CACHE_DIRS))
    records = {}
    for cache_dir in cache_dirs:
        real_dir = os.path.realpath(cache_dir)
        for path in glob.glob(os.path.join(cache_dir, "*_0")):
            try:
                if os.path.islink(path):
                    real = os.path.realpath(path)
                    st = os.stat(real)
                else:
                    real = os.path.join(real_dir, os.path.basename(path))
                    st = os.stat(path)
                records[real] = {
                    "path": real,
                    "mtime_ns": st.st_mtime_ns,
                    "size": st.st_size,
                }
            except OSError:
                continue
    return sorted(records.values(), key=lambda r: (r["mtime_ns"], r["path"]), reverse=True)


def _claude_cache_files():
    return [record["path"] for record in _claude_cache_records()]


def _iso_to_epoch(s):
    dt = parse_ts(s) if s else None
    return int(dt.timestamp()) if dt else None


def _zstd_decompress(data):
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=len(data) * 20)
    except ImportError:
        pass
    except Exception:
        pass
    return None


_CLAUDE_QUOTA_STATE_VERSION = 2
_CLAUDE_QUOTA_STALE_TTL = 1800
_CLAUDE_QUOTA_FULL_SCAN_INTERVAL = 6 * 3600
_CLAUDE_QUOTA_RETRY_SCAN_INTERVAL = 5 * 60
_CLAUDE_CACHE_FILE_LIMIT = 16 * 1024 * 1024
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def _claude_record_signature(record):
    return f'{record["path"]}|{record["mtime_ns"]}|{record["size"]}'


def _load_claude_quota_state():
    state = _load_json(CLAUDE_QUOTA_CACHE, {})
    if not isinstance(state, dict) or state.get("version") != _CLAUDE_QUOTA_STATE_VERSION:
        return {"version": _CLAUDE_QUOTA_STATE_VERSION}
    return state


def _save_claude_quota_state(state):
    try:
        _atomic_write_json(CLAUDE_QUOTA_CACHE, state)
        os.chmod(CLAUDE_QUOTA_CACHE, 0o600)
    except Exception:
        pass


def _parse_claude_quota_record(record):
    if record["size"] <= 0 or record["size"] > _CLAUDE_CACHE_FILE_LIMIT:
        return None
    try:
        with open(record["path"], "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if b"organizations/" not in data or b"/usage" not in data:
        return None
    pos = data.find(_ZSTD_MAGIC)
    if pos < 0:
        return None
    raw = _zstd_decompress(data[pos:])
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    five_hour = payload.get("five_hour") or {}
    seven_day = payload.get("seven_day") or {}
    if not isinstance(five_hour, dict):
        five_hour = {}
    if not isinstance(seven_day, dict):
        seven_day = {}
    fable_limit = {}
    limits = payload.get("limits") or []
    if isinstance(limits, list):
        for limit in limits:
            if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
                continue
            scope = limit.get("scope") or {}
            model = scope.get("model") or {} if isinstance(scope, dict) else {}
            display_name = model.get("display_name") if isinstance(model, dict) else None
            if isinstance(display_name, str) and display_name.casefold() == "fable":
                fable_limit = limit
                break
    result = {
        "q5": five_hour.get("utilization"),
        "q5_reset": _iso_to_epoch(five_hour.get("resets_at")),
        "q7": seven_day.get("utilization"),
        "q7_reset": _iso_to_epoch(seven_day.get("resets_at")),
        "qf": fable_limit.get("percent"),
        "qf_reset": _iso_to_epoch(fable_limit.get("resets_at")),
        "q_updated": int(record["mtime_ns"] // 1_000_000_000),
    }
    return result if any(result[key] is not None for key in ("q5", "q7", "qf")) else None


def _claude_quota_with_freshness(snapshot, now=None):
    if not isinstance(snapshot, dict):
        return {}
    now = int(time.time()) if now is None else int(now)
    result = dict(snapshot)
    try:
        updated = int(result.get("q_updated"))
    except (TypeError, ValueError):
        updated = 0
    age = now - updated
    source_stale = updated <= 0 or age > _CLAUDE_QUOTA_STALE_TTL or age < -300
    for value_key, reset_key, stale_key in (
        ("q5", "q5_reset", "q5_stale"),
        ("q7", "q7_reset", "q7_stale"),
        ("qf", "qf_reset", "qf_stale"),
    ):
        reset = result.get(reset_key)
        try:
            reset_expired = reset is not None and int(reset) <= now
        except (TypeError, ValueError):
            reset_expired = False
        result[stale_key] = bool(result.get(value_key) is not None and
                                 (source_stale or reset_expired))
    return result


def _scan_claude_plan_raw(now=None):
    now = int(time.time()) if now is None else int(now)
    records = _claude_cache_records()
    records_by_path = {record["path"]: record for record in records}
    original = _load_claude_quota_state()
    state = dict(original)
    initial_scan = "scan_mtime_ns" not in state
    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else None
    candidate = state.get("candidate") if isinstance(state.get("candidate"), dict) else None
    last_scan_ns = int(state.get("scan_mtime_ns") or -1)
    scan_boundary = set(state.get("scan_boundary") or [])

    changed = [
        record for record in records
        if record["mtime_ns"] > last_scan_ns or
        (record["mtime_ns"] == last_scan_ns and
         _claude_record_signature(record) not in scan_boundary)
    ]
    inspected = set()
    selected = None

    def inspect(record):
        inspected.add(record["path"])
        parsed = _parse_claude_quota_record(record)
        return (record, parsed) if parsed else None

    for record in changed:
        selected = inspect(record)
        if selected:
            break

    candidate_invalid = False
    candidate_record = records_by_path.get(candidate.get("path")) if candidate else None
    if selected is None and candidate:
        if candidate_record is None:
            candidate_invalid = True
        else:
            candidate_changed = (
                candidate_record.get("mtime_ns") != candidate.get("mtime_ns") or
                candidate_record.get("size") != candidate.get("size")
            )
            if candidate_changed and candidate_record["path"] not in inspected:
                selected = inspect(candidate_record)
                candidate_invalid = selected is None
            elif candidate_changed:
                candidate_invalid = True

    if initial_scan:
        state["last_full_scan"] = now

    last_full_scan = int(state.get("last_full_scan") or 0)
    retry_interval = (_CLAUDE_QUOTA_RETRY_SCAN_INTERVAL if snapshot is None
                      else _CLAUDE_QUOTA_FULL_SCAN_INTERVAL)
    needs_full_scan = (candidate_invalid or now - last_full_scan >= retry_interval)
    if selected is None and needs_full_scan:
        for record in records:
            if record["path"] in inspected:
                continue
            selected = inspect(record)
            if selected:
                break
        state["last_full_scan"] = now

    if selected:
        record, snapshot = selected
        state["candidate"] = {
            "path": record["path"],
            "mtime_ns": record["mtime_ns"],
            "size": record["size"],
        }
        state["snapshot"] = snapshot
    elif candidate_invalid:
        state.pop("candidate", None)

    if records:
        newest_mtime = records[0]["mtime_ns"]
        state["scan_mtime_ns"] = newest_mtime
        state["scan_boundary"] = [
            _claude_record_signature(record)
            for record in records if record["mtime_ns"] == newest_mtime
        ]
    else:
        state["scan_mtime_ns"] = -1
        state["scan_boundary"] = []
    state["version"] = _CLAUDE_QUOTA_STATE_VERSION
    if state != original:
        _save_claude_quota_state(state)
    return _claude_quota_with_freshness(snapshot, now=now)


def scan_claude_plan(bounds=None, cache=None):
    return _scan_claude_plan_raw()
