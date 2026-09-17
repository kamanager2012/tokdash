"""Storage, Scan Cache, and Persistent Ledger Engine for Cognitally / TokDash.

Handles atomic file updates, cross-process cache locking, schema migrations,
and durable multi-agent ledger high-water marks.
"""

import os
import json
import tempfile as _tempfile
from datetime import date, timedelta

from core.config import (
    HOME,
    _USER_DIR,
    _SCAN_CACHE_FILE,
    _DEFAULT_SCAN_CACHE_FILE,
    _LEGACY_SCAN_CACHE_FILE,
    _PREV_TOKEI_CACHE_FILE,
    _LEDGER_TOKEN_FIELDS,
)
from core.pricing import nice_model, price_for

_SCAN_CACHE_VERSION = 21
_SCAN_CACHE_MIGRATABLE_VERSION = 19
_CODEX_EVENT_CACHE_SUFFIX = ".codex-events"
_CODEX_PARSER_VERSION = 3
_CODEX_SCAN_CHECKPOINT_INTERVAL = 5.0


def _codex_event_cache_dir():
    return f"{_SCAN_CACHE_FILE}{_CODEX_EVENT_CACHE_SUFFIX}"


def _remove_codex_event_cache_dir():
    import shutil
    shutil.rmtree(f"{_SCAN_CACHE_FILE}{_CODEX_EVENT_CACHE_SUFFIX}", ignore_errors=True)


def _migrate_legacy_scan_cache():
    if (_SCAN_CACHE_FILE != _DEFAULT_SCAN_CACHE_FILE
            or os.path.exists(_SCAN_CACHE_FILE)):
        return

    source_file = None
    if os.path.isfile(_PREV_TOKEI_CACHE_FILE):
        source_file = _PREV_TOKEI_CACHE_FILE
    elif os.path.isfile(_LEGACY_SCAN_CACHE_FILE):
        source_file = _LEGACY_SCAN_CACHE_FILE
    else:
        return

    import shutil
    directory = os.path.dirname(_SCAN_CACHE_FILE)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass

    fd, tmp = _tempfile.mkstemp(prefix=".scan-cache-", suffix=".json", dir=directory)
    try:
        os.close(fd)
        shutil.copyfile(source_file, tmp)
        os.chmod(tmp, 0o600)
        legacy_events = f"{source_file}{_CODEX_EVENT_CACHE_SUFFIX}"
        current_events = _codex_event_cache_dir()
        if os.path.isdir(legacy_events) and not os.path.exists(current_events):
            shutil.copytree(legacy_events, current_events)
            try:
                os.chmod(current_events, 0o700)
            except OSError:
                pass
        os.replace(tmp, _SCAN_CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _load_scan_cache():
    _migrate_legacy_scan_cache()
    try:
        with open(_SCAN_CACHE_FILE, "r") as f:
            c = json.load(f)
        version = c.get("v")
        if version not in (_SCAN_CACHE_VERSION, _SCAN_CACHE_MIGRATABLE_VERSION):
            _remove_codex_event_cache_dir()
            return {"v": _SCAN_CACHE_VERSION, "_dirty": True}
        if version == _SCAN_CACHE_MIGRATABLE_VERSION:
            c["v"] = _SCAN_CACHE_VERSION
            c["_dirty"] = True
        else:
            c["_dirty"] = False
        c["_keys"] = {k for k in c if not k.startswith("_")}
        return c
    except Exception:
        _remove_codex_event_cache_dir()
        return {"v": _SCAN_CACHE_VERSION, "_dirty": True}


def _save_scan_cache(cache):
    prev_keys = cache.pop("_keys", set())
    current_keys = {k for k in cache if not k.startswith("_")}
    dirty = cache.pop("_dirty", False) or current_keys != prev_keys
    if not dirty:
        return
    cache["v"] = _SCAN_CACHE_VERSION
    tmp = None
    try:
        directory = os.path.dirname(_SCAN_CACHE_FILE)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        fd, tmp = _tempfile.mkstemp(prefix="_tokei_scan_cache.", suffix=".json",
                                    dir=directory or None)
        payload = json.dumps(cache, separators=(',', ':')).encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _SCAN_CACHE_FILE)
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        pass


def _load_dashboard_cache():
    return _load_scan_cache()


# ---------- Persistent Ledger ----------
_LEDGER_FILE = (
    os.environ.get("COGNITALLY_LEDGER_FILE")
    or os.environ.get("TOKDASH_LEDGER_FILE")
    or os.environ.get("TOKEI_LEDGER_FILE")
    or os.path.join(_USER_DIR, "ledger.json")
)
_LEDGER_VERSION = 1
_LEDGER_FIELDS = ("in", "out", "cr", "cw", "reason", "cached", "cost")

_LEDGER_CACHE = {"data": None, "dirty": False}


def _load_tokei_config():
    cfg_path = os.path.join(HOME, ".tokei", "config.json")
    try:
        with open(cfg_path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_ledger():
    if _LEDGER_CACHE["data"] is not None:
        return _LEDGER_CACHE["data"]
    _LEDGER_CACHE["data"] = _load_ledger_from_disk()
    return _LEDGER_CACHE["data"]


def _load_ledger_from_disk():
    try:
        with open(_LEDGER_FILE, "r") as f:
            ledger = json.load(f)
        if isinstance(ledger, dict) and ledger.get("v") == _LEDGER_VERSION:
            return ledger
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    # Recovery from device snapshot
    try:
        cfg = _load_tokei_config() or {}
        device = (cfg.get("device_id") or "").strip()
        sync_dir = (cfg.get("sync_dir") or "").strip()
        if device and sync_dir:
            snap_path = os.path.join(os.path.expanduser(sync_dir), f"{device}.json")
            with open(snap_path, "r") as f:
                backup = json.load(f).get("_ledger")
            if (isinstance(backup, dict) and backup.get("v") == _LEDGER_VERSION
                    and backup.get("tools")):
                _save_ledger(backup)
                return backup
    except Exception:
        pass
    return {"v": _LEDGER_VERSION, "tools": {}}


def ledger_flush():
    """把内存账本变更落盘:短锁内与磁盘最新状态做天级高水位合并后原子写。"""
    if not _LEDGER_CACHE["dirty"] or _LEDGER_CACHE["data"] is None:
        return
    lock_fd = None
    try:
        import fcntl
        os.makedirs(os.path.dirname(_LEDGER_FILE), mode=0o700, exist_ok=True)
        lock_fd = os.open(f"{_LEDGER_FILE}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except OSError:
        lock_fd = None
    try:
        fresh = _load_ledger_from_disk()
        memo = _LEDGER_CACHE["data"]
        for tool, days in memo.get("tools", {}).items():
            stored = fresh["tools"].setdefault(tool, {})
            for dk, day in days.items():
                kept = stored.get(dk)
                if (kept is None
                        or _ledger_cost_version(day) > _ledger_cost_version(kept)
                        or (_ledger_cost_version(day) == _ledger_cost_version(kept)
                            and _ledger_day_total(day) > _ledger_day_total(kept))):
                    stored[dk] = day
        _save_ledger(fresh)
        _LEDGER_CACHE["data"] = fresh
        _LEDGER_CACHE["dirty"] = False
    finally:
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)


def _save_ledger(ledger):
    tmp = None
    try:
        directory = os.path.dirname(_LEDGER_FILE)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = _tempfile.mkstemp(prefix=".ledger-", suffix=".json", dir=directory)
        with os.fdopen(fd, "w") as f:
            json.dump(ledger, f, separators=(',', ':'))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _LEDGER_FILE)
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _ledger_day_total(day):
    return sum(float(v) for k, v in day.items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)
               and k != "cost" and not k.startswith("_"))


def _ledger_cost_version(day):
    value = day.get("_cost_version", 0) if isinstance(day, dict) else 0
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _ledger_token_sum(day):
    tok = 0
    for field in _LEDGER_TOKEN_FIELDS:
        value = day.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tok += int(value)
    return tok


def ledger_reconcile(tool, live_days):
    """对账: live_days={day: day_dict} (现存日志实时聚合,任意字段结构)。"""
    ledger = _load_ledger()
    stored = ledger["tools"].setdefault(tool, {})
    dirty = False
    merged = {}
    max_day = (date.today() + timedelta(days=1)).isoformat()
    for dk, live in live_days.items():
        kept = stored.get(dk)
        kept_version = _ledger_cost_version(kept)
        live_version = _ledger_cost_version(live)
        if (kept and kept_version > live_version
                or (kept and kept_version == live_version
                    and _ledger_day_total(kept) > _ledger_day_total(live))):
            merged[dk] = kept
        else:
            merged[dk] = live
            if not ("2025-01-01" <= dk <= max_day):
                continue
            snapshot = {k: v for k, v in live.items()
                        if not isinstance(v, set)}
            if kept != snapshot:
                stored[dk] = snapshot
                dirty = True
    for dk, kept in stored.items():
        if dk not in merged:
            merged[dk] = kept
    if dirty:
        _LEDGER_CACHE["dirty"] = True
    return merged


def ledger_touch(tool):
    """确保账本 tools 中存在该工具的键,标记 scanner 已接入。"""
    try:
        ledger = _load_ledger()
        if tool not in ledger.get("tools", {}):
            ledger.setdefault("tools", {})[tool] = {}
            _LEDGER_CACHE["dirty"] = True
    except Exception:
        pass


def _with_scan_cache_lock(fn):
    def locked(*args, **kwargs):
        try:
            import fcntl
        except ImportError:
            return fn(*args, **kwargs)

        lock_path = f"{_SCAN_CACHE_FILE}.lock"
        lock_dir = os.path.dirname(lock_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            return fn(*args, **kwargs)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
    return locked


def _cache_dashboard_days(cache, key, days):
    def serializable(value):
        if isinstance(value, dict):
            return {str(k): serializable(v) for k, v in value.items()}
        if isinstance(value, set):
            return sorted(serializable(v) for v in value)
        if isinstance(value, (list, tuple)):
            return [serializable(v) for v in value]
        return value

    payload = serializable(days if isinstance(days, dict) else {})
    if cache.get(key) != payload:
        cache[key] = payload
        cache["_dirty"] = True


def _merge_dashboard_days(cache, key, days):
    if not isinstance(days, dict) or not days:
        return
    existing = cache.get(key)
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(days)
    _cache_dashboard_days(cache, key, merged)


def _iter_cached_token_days(tool_cache):
    for entry in tool_cache.values():
        if not isinstance(entry, dict):
            continue
        for day_key, day in entry.get("days", {}).items():
            if isinstance(day, dict):
                yield day_key, day
        day = entry.get("day")
        if isinstance(day, dict) and day.get("date"):
            yield day["date"], day


def _add_model_usage(models, model, inp=0, out=0, cr=0, cw=0, reason=0, cost=0.0):
    if not model:
        return
    mm = models.setdefault(model, {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0, "cost": 0.0})
    mm["in"] += int(inp or 0); mm["out"] += int(out or 0)
    mm["cr"] += int(cr or 0); mm["cw"] += int(cw or 0); mm["reason"] += int(reason or 0)
    mm["cost"] += float(cost or 0)


def _add_token_usage(target, inp=0, out=0, cr=0, cw=0, reason=0, cost=0.0, model=None):
    target["in"] += int(inp or 0); target["out"] += int(out or 0)
    target["cr"] += int(cr or 0); target["cw"] += int(cw or 0); target["reason"] += int(reason or 0)
    target["cost"] += float(cost or 0)
    _add_model_usage(target.get("models", {}), model, inp, out, cr, cw, reason, cost)


def _merge_token_day(bucket, day, session=None):
    if session is not None:
        bucket["sessions"].add(session)
    _add_token_usage(bucket, day.get("in", 0), day.get("out", 0), day.get("cr", 0),
                     day.get("cw", 0), day.get("reason", 0), day.get("cost", 0))
    for model, mv in day.get("models", {}).items():
        _add_model_usage(bucket["models"], model, mv.get("in", 0), mv.get("out", 0),
                         mv.get("cr", 0), mv.get("cw", 0), mv.get("reason", 0), mv.get("cost", 0))


def _merge_live_token_day(agg, day):
    _add_token_usage(agg, day.get("in", 0), day.get("out", 0), day.get("cr", 0),
                     day.get("cw", 0), day.get("reason", 0), day.get("cost", 0))
    for model, mv in (day.get("models") or {}).items():
        _add_model_usage(agg["models"], model, mv.get("in", 0), mv.get("out", 0),
                         mv.get("cr", 0), mv.get("cw", 0), mv.get("reason", 0), mv.get("cost", 0))
    hours = day.get("hours")
    if isinstance(hours, list):
        agg_hours = agg.setdefault("hours", [0] * 24)
        for hour, amount in enumerate(hours[:24]):
            agg_hours[hour] += amount


def _format_token_models(models, include_prices=True):
    result = []
    sort_key = (lambda kv: -kv[1].get("cost", 0)) if include_prices else (
        lambda kv: -(kv[1].get("in", 0) + kv[1].get("out", 0)))
    for model_name, usage in sorted(models.items(), key=sort_key):
        in_tokens = usage.get("in", 0)
        cached = usage.get("cr", 0) or usage.get("cached", 0)
        non_cached = max(in_tokens - cached, 0)
        entry = {
            "name": nice_model(model_name),
            "in": non_cached,
            "out": usage.get("out", 0),
            "cr": cached,
            "cw": usage.get("cw", 0),
            "reason": usage.get("reason", 0) or usage.get("thoughts", 0),
        }
        if include_prices:
            p = price_for(model_name)
            entry.update({
                "cost": usage.get("cost", 0.0),
                "pin": p["in"],
                "pout": p["out"],
            })
        result.append(entry)
    return result


def _safe_scan(name, fn, fallback, errors):
    try:
        return fn()
    except Exception as e:
        errors[name] = f"{type(e).__name__}: {e}"
        return fallback()
