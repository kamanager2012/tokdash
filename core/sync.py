import os
import sys
import json
import tempfile as _tempfile

from core.config import (
    HOME,
    PRICING_FILE,
    _load_json,
    range_boundaries,
)
from core.storage import (
    _load_scan_cache,
    _load_ledger,
)


_TOKEI_CONFIG = os.path.join(HOME, ".tokei", "config.json")


def _load_tokei_config():
    try:
        with open(_TOKEI_CONFIG) as f:
            return json.load(f)
    except Exception:
        return None


def _sync_snapshot_filename(device_id):
    if not isinstance(device_id, str):
        return None
    value = device_id.strip()
    if (not value or value in (".", "..") or len(value) > 128
            or any(ch in "/\\\0" or ord(ch) < 32 for ch in value)):
        return None
    return f"{value}.json"


def _write_sync_snapshot(sync_dir, device_id, payload):
    own_name = _sync_snapshot_filename(device_id)
    if not own_name or not os.path.isdir(sync_dir):
        return False

    sync_root = os.path.realpath(sync_dir)
    try:
        for fn in os.listdir(sync_root):
            if fn.casefold() == own_name.casefold():
                own_name = fn
                break
    except OSError:
        return False

    destination = os.path.abspath(os.path.join(sync_root, own_name))
    try:
        if os.path.commonpath((sync_root, destination)) != sync_root:
            return False
    except ValueError:
        return False

    tmp = None
    try:
        fd, tmp = _tempfile.mkstemp(prefix=".tokei-sync-", suffix=".json", dir=sync_root)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destination)
        return True
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _sync_safe_usage_payload(payload):
    snapshot = dict(payload)
    for key in ("cursor", "zed", "sub2api", "zai", "antigravity"):
        snapshot.pop(key, None)
    return snapshot


def _write_configured_sync_snapshot(d):
    cfg = _load_tokei_config()
    if not cfg:
        return False
    sync_dir = os.path.expanduser(cfg.get("sync_dir", ""))
    if not sync_dir:
        sync_dir = os.path.join(HOME, ".tokei", "sync")
    device_id = cfg.get("device_id", "")
    if not _sync_snapshot_filename(device_id) or not os.path.isdir(sync_dir):
        return False

    import time
    from core.accounting.daily import build_daily_costs
    from core.reports.wrapped import build_wrapped

    snapshot = _sync_safe_usage_payload(d)
    snapshot["_device"] = device_id
    snapshot["_ts"] = int(time.time())
    snapshot["_range_bounds"] = range_boundaries()
    cache = _load_scan_cache()
    snapshot["_dashboard"] = {
        "daily": build_daily_costs("all", refresh=False, _cache=cache).get("daily", []),
        "wrapped": {p: build_wrapped(p, refresh=False, _cache=cache)
                    for p in ["all", "1d", "7d", "30d", "365d"]},
    }
    return _write_sync_snapshot(sync_dir, device_id, snapshot)


def write_sync_snapshot():
    from core.collectors.compute import compute
    from core.reports.dashboard import _load_quota_anchors

    d = compute()
    meta = _load_json(PRICING_FILE, {}).get("_meta", {})
    d["_pricing"] = {"updated_at": meta.get("updated_at", ""), "count": meta.get("count", 0)}
    ledger = _load_ledger()
    if ledger.get("tools"):
        d["_ledger"] = ledger
    anchors = _load_quota_anchors()
    if anchors:
        d["_quota_anchors"] = anchors
    return 0 if _write_configured_sync_snapshot(d) else 1
