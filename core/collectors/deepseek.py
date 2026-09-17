import os
import glob
import json
from datetime import datetime, date

from core.config import (
    DEEPSEEK_HARNESS_DIR,
    _empty_token_day,
    _empty_token_ranges,
    classify_date,
    token_total,
)
from core.pricing import (
    _deepseek_official_price,
    _pricing_id,
    _raw_price,
)
from core.storage import (
    _add_token_usage,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)

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
