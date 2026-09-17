import os
import glob
import json
from datetime import date

from core.config import (
    HOME,
    OMP_SESSION_DIR,
    PI_AGENT_DIR,
    PI_SESSION_DIR,
    _empty_token_day,
    _empty_token_ranges,
    classify_date,
    parse_ts,
)
from core.pricing import _raw_price
from core.storage import (
    _add_token_usage,
    _merge_live_token_day,
    _merge_token_day,
    ledger_reconcile,
    ledger_touch,
)


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
