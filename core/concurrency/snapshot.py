"""Atomic Canonical Snapshot with Single-Flight Process Coordination.

Guarantees single execution across concurrent processes (CLI, MCP, Electron UI)
using flock mutual exclusion, and caches the result within the TTL window.
"""

import os
import sys
import json
import time as _time
import uuid
import tempfile
from datetime import datetime

from core.config import (
    _SCAN_CACHE_DIR,
    _SNAPSHOT_CACHE_FILE,
    _SNAPSHOT_LOCK_FILE,
    _SNAPSHOT_TTL,
    PRICING_FILE,
    _load_json,
    _migrate_legacy_cognitally_state,
)
from core.concurrency.digest import _canonical_accounting_digest

_SNAPSHOT_GENERATOR = None


def register_snapshot_generator(fn):
    """Register a callable that returns (usage_data, daily_data, projects_data)."""
    global _SNAPSHOT_GENERATOR
    _SNAPSHOT_GENERATOR = fn


def _resolve_snapshot_generator():
    if _SNAPSHOT_GENERATOR is not None:
        return _SNAPSHOT_GENERATOR
    for mod_name in ("usage_30s", "usage_module", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "compute") and hasattr(mod, "build_daily_costs") and hasattr(mod, "get_projects"):
            def _discovered():
                u, c = mod.compute(return_cache=True)
                m = _load_json(PRICING_FILE, {}).get("_meta", {})
                u["_pricing"] = {"updated_at": m.get("updated_at", ""), "count": m.get("count", 0)}
                period = getattr(mod, "_arg_period")() if hasattr(mod, "_arg_period") else "all"
                d = mod.build_daily_costs(period, refresh=False, _cache=c)
                p = mod.get_projects(refresh=False, _cache=c)
                return u, d, p
            return _discovered
    raise RuntimeError("No snapshot generator registered and could not resolve compute/accounting functions.")


def get_canonical_snapshot(ttl=_SNAPSHOT_TTL, force=False, generator=None):
    """原子一致性快照(带跨进程单飞防护): 仅执行单次 compute(), 在单一内存世代内派生 usage, daily_costs 和 projects。
    
    多进程(如 MCP Server, Electron UI, 命令行)同时请求时，仅有一个进程计算，其余等待并直接复用新鲜快照，彻底杜绝内存风暴与重叠计算。
    """
    _migrate_legacy_cognitally_state()
    try:
        os.makedirs(_SCAN_CACHE_DIR, mode=0o700, exist_ok=True)
    except OSError:
        pass

    # 1. 快速路径: 若缓存文件存在且在 TTL 内，直接读取返回
    if not force and os.path.isfile(_SNAPSHOT_CACHE_FILE):
        try:
            mtime = os.path.getmtime(_SNAPSHOT_CACHE_FILE)
            if _time.time() - mtime < ttl:
                with open(_SNAPSHOT_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if isinstance(cached, dict) and "generation" in cached and "usage" in cached:
                    return cached
        except Exception:
            pass

    # 2. 跨进程单飞锁
    lock_fd = None
    try:
        lock_fd = os.open(_SNAPSHOT_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # 再次检查: 排队等待锁期间是否已有先行进程计算完成
        if not force and os.path.isfile(_SNAPSHOT_CACHE_FILE):
            try:
                mtime = os.path.getmtime(_SNAPSHOT_CACHE_FILE)
                if _time.time() - mtime < ttl:
                    with open(_SNAPSHOT_CACHE_FILE, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if isinstance(cached, dict) and "generation" in cached and "usage" in cached:
                        return cached
            except Exception:
                pass

        gen_fn = generator or _resolve_snapshot_generator()
        snap_id = str(uuid.uuid4())
        gen_time = datetime.now().astimezone().isoformat()

        # 核心计算并获取当前内存世代引用，完全避开磁盘读取窗口
        usage_data, daily_data, projects_data = gen_fn()

        # 真实计算基于该完整内存世代数据的规范化 SHA-256 状态摘要
        gen_token = _canonical_accounting_digest(usage_data, daily_data, projects_data)

        payload = {
            "snapshot_id": snap_id,
            "generation": gen_token,
            "generated_at": gen_time,
            "usage": usage_data,
            "daily_costs": daily_data,
            "projects": projects_data,
        }

        # 原子落盘快照缓存
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".snap-", suffix=".json", dir=_SCAN_CACHE_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(',', ':'), ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _SNAPSHOT_CACHE_FILE)
        except Exception:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return payload
    finally:
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
