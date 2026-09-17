"""Project Footprint & Attribution Engine for Cognitally / TokDash.

Aggregates project directory paths, active sessions, token expenditures,
costs, dominant models, and active local listening server ports.
"""

import os
import sys
import json
import subprocess

from core.config import (
    token_total,
    _GROK_DAYS_CACHE_KEY,
)
from core.pricing import nice_model

from core.storage import _load_scan_cache

_PROVIDERS = {
    "compute": None,
}


def register_projects_providers(load_scan_cache=None, compute=None, **kwargs):
    if compute:
        _PROVIDERS["compute"] = compute


def _resolve_provider(name):
    cb = _PROVIDERS.get(name)
    if cb is not None:
        return cb
    for mod_name in ("usage_30s", "usage_module", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, name):
            return getattr(mod, name)
    return lambda *args, **kwargs: {}


def _iter_workbuddy_records(file_cache):
    items = []
    for path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        for record in entry.get("records", []):
            if isinstance(record, dict):
                items.append((record.get("ts", 0), path, entry, record))
    items.sort(key=lambda x: (x[0], x[1]))

    seen = set()
    for _, path, entry, record in items:
        key = record.get("dedup") or f"{path}:{record.get('line', 0)}:{record.get('ts_key', '')}"
        if key in seen:
            continue
        seen.add(key)
        yield path, entry, record


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


def _detect_local_servers(project_paths):
    """检测哪些项目目录下有进程正在监听 TCP 端口。返回 {path: [port, ...]}。"""
    try:
        # 1) pid → ports (LISTEN)
        out1 = subprocess.check_output(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n", "-F", "pn"],
            stderr=subprocess.DEVNULL, timeout=10, text=True)
        pid_ports = {}
        cur_pid = None
        for line in out1.strip().split("\n"):
            if line.startswith("p"):
                cur_pid = line[1:]
            elif line.startswith("n") and cur_pid:
                addr = line[1:]
                port = addr.rsplit(":", 1)[-1] if ":" in addr else None
                if port and port.isdigit():
                    p = int(port)
                    if 1024 <= p <= 65535:
                        pid_ports.setdefault(cur_pid, set()).add(p)

        if not pid_ports:
            return {}

        # 2) pid → cwd (只查有监听端口的 pid，避免全系统扫描超时)
        pid_arg = ",".join(pid_ports.keys())
        out2 = subprocess.check_output(
            ["lsof", "-a", "-d", "cwd", "-p", pid_arg, "-F", "pn"],
            stderr=subprocess.DEVNULL, timeout=10, text=True)
        pid_cwd = {}
        cur_pid = None
        for line in out2.strip().split("\n"):
            if line.startswith("p"):
                cur_pid = line[1:]
            elif line.startswith("n") and cur_pid:
                pid_cwd[cur_pid] = line[1:]

        # 3) 交叉匹配: 进程 cwd 是项目路径或其子目录
        home = os.path.expanduser("~")
        sorted_projs = sorted(project_paths, key=len, reverse=True)
        result = {}
        for pid, ports in pid_ports.items():
            cwd = pid_cwd.get(pid, "")
            if not cwd or cwd == home:
                continue
            for proj in sorted_projs:
                if proj == home:
                    continue
                if cwd == proj or cwd.startswith(proj + "/"):
                    result.setdefault(proj, set()).update(ports)
                    break
        return result
    except Exception:
        return {}


def get_projects(refresh=True, _cache=None, _load_scan_cache_fn=None, _compute_fn=None):
    """项目足迹:从缓存聚合所有项目路径、活跃时间、session 数、token、成本。"""
    if refresh:
        compute_fn = _compute_fn or _resolve_provider("compute")
        compute_fn()
    load_cache = _load_scan_cache_fn or _resolve_provider("load_scan_cache")
    cache = _cache if _cache is not None else load_cache()

    proj_map = {}  # path → {sessions, tokens, cost, last_active, model_tok}

    # Claude sessions
    for f, entry in cache.get("claude", {}).items():
        if not isinstance(entry, dict):
            continue
        proj_path = entry.get("proj") or ""
        if not proj_path or proj_path == "?":
            continue
        p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["sessions"] += 1
        p["tools"].add("claude")
        for dk, day in entry.get("days", {}).items():
            tok = token_total(day)
            p["tokens"] += tok
            p["cost"] += day.get("cost", 0)
            if dk > p["last_active"]:
                p["last_active"] = dk
            for mn, mv in day.get("models", {}).items():
                nm = nice_model(mn)
                p["model_tok"][nm] = p["model_tok"].get(nm, 0) + token_total(mv)

    # Pi sessions
    for f, entry in cache.get("pi", {}).items():
        if not isinstance(entry, dict):
            continue
        proj_path = entry.get("proj") or ""
        if not proj_path or proj_path == "?":
            continue
        p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["sessions"] += 1
        p["tools"].add("pi")
        for dk, day in entry.get("days", {}).items():
            tok = token_total(day)
            p["tokens"] += tok
            p["cost"] += day.get("cost", 0)
            if dk > p["last_active"]:
                p["last_active"] = dk
            for mn, mv in day.get("models", {}).items():
                nm = f"{nice_model(mn)} (Pi)"
                p["model_tok"][nm] = p["model_tok"].get(nm, 0) + token_total(mv)

    # Prime Agent sessions
    for f, entry in cache.get("prime_agent", {}).items():
        if not isinstance(entry, dict):
            continue
        proj_path = entry.get("proj") or ""
        if not proj_path or proj_path == "?":
            continue
        p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["sessions"] += 1
        p["tools"].add("prime_agent")
        for dk, day in entry.get("days", {}).items():
            tok = token_total(day)
            p["tokens"] += tok; p["cost"] += day.get("cost", 0)
            if dk > p["last_active"]: p["last_active"] = dk
            for mn, mv in day.get("models", {}).items():
                nm = f"{nice_model(mn)} (Prime Agent)"
                p["model_tok"][nm] = p["model_tok"].get(nm, 0) + token_total(mv)

    # WorkBuddy 国内版与国际版 sessions
    for tool_key, suffix in (("workbuddy", "WorkBuddy"),
                             ("workbuddy_ai", "WorkBuddy Intl.")):
        workbuddy_sessions = {}
        for _, entry, record in _iter_workbuddy_records(cache.get(tool_key, {})):
            proj_path = entry.get("proj") or ""
            if not proj_path or proj_path == "?":
                continue
            p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                                 "last_active": "", "model_tok": {}, "tools": set()})
            p["tools"].add(tool_key)
            p["tokens"] += token_total(record)
            p["cost"] += record.get("cost", 0)
            dk = record.get("date", "")
            if dk > p["last_active"]:
                p["last_active"] = dk
            model_name = f"{nice_model(record.get('model', 'unknown'))} ({suffix})"
            p["model_tok"][model_name] = p["model_tok"].get(model_name, 0) + token_total(record)
            workbuddy_sessions.setdefault(proj_path, set()).add(
                record.get("session") or entry.get("sid"))
        for proj_path, session_ids in workbuddy_sessions.items():
            proj_map[proj_path]["sessions"] += len(session_ids)

    # DeepSeek Harness sessions
    deepseek_sessions = {}
    for _, entry, record in _iter_deepseek_harness_records(cache.get("deepseek_harness", {})):
        proj_path = entry.get("proj") or ""
        if not proj_path or proj_path == "?":
            continue
        p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["tools"].add("deepseek_harness")
        p["tokens"] += token_total(record)
        p["cost"] += record.get("cost", 0)
        dk = record.get("date", "")
        if dk > p["last_active"]:
            p["last_active"] = dk
        model_name = f"{nice_model(record.get('model', 'deepseek-v4-pro'))} (DeepSeek Harness)"
        p["model_tok"][model_name] = p["model_tok"].get(model_name, 0) + token_total(record)
        deepseek_sessions.setdefault(proj_path, set()).add(entry.get("sid"))
    for proj_path, session_ids in deepseek_sessions.items():
        proj_map[proj_path]["sessions"] += len({session for session in session_ids if session})

    # Kimi Code sessions
    kimi_sessions = {}
    for entry in cache.get("kimicode", {}).values():
        if not isinstance(entry, dict):
            continue
        proj_path = entry.get("proj") or ""
        if not proj_path or proj_path == "?":
            continue
        p = proj_map.setdefault(proj_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["tools"].add("kimicode")
        kimi_sessions.setdefault(proj_path, set()).add(entry.get("sid"))
        for dk, day in entry.get("days", {}).items():
            p["tokens"] += token_total(day)
            if dk > p["last_active"]:
                p["last_active"] = dk
            for model, usage in day.get("models", {}).items():
                name = f"{nice_model(model)} (Kimi Code)"
                p["model_tok"][name] = p["model_tok"].get(name, 0) + token_total(usage)
    for proj_path, session_ids in kimi_sessions.items():
        proj_map[proj_path]["sessions"] += len({session for session in session_ids if session})

    # Grok Build sessions + unified 日志
    grok_project_sessions = {}
    for entry in cache.get("grok", {}).values():
        if not isinstance(entry, dict):
            continue
        grok_path = entry.get("project") or ""
        if not grok_path:
            continue
        p = proj_map.setdefault(grok_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                             "last_active": "", "model_tok": {}, "tools": set()})
        p["tools"].add("grok")
        grok_project_sessions.setdefault(grok_path, set()).add(entry.get("sid"))
        dk = entry.get("date") or ""
        if dk > p["last_active"]:
            p["last_active"] = dk
    for dk, day in cache.get(_GROK_DAYS_CACHE_KEY, {}).items():
        for grok_path, usage in day.get("projects", {}).items():
            p = proj_map.setdefault(grok_path, {"sessions": 0, "tokens": 0, "cost": 0.0,
                                                 "last_active": "", "model_tok": {}, "tools": set()})
            p["tools"].add("grok")
            p["tokens"] += int(usage.get("tokens", 0) or 0)
            p["cost"] += float(usage.get("cost", 0) or 0)
            if dk > p["last_active"]:
                p["last_active"] = dk
            session_ids = {sid for sid in usage.get("sessions", []) if sid}
            grok_project_sessions.setdefault(grok_path, set()).update(session_ids)
            for model, amount in usage.get("models", {}).items():
                name = f"{nice_model(model)} (Grok Build)"
                p["model_tok"][name] = p["model_tok"].get(name, 0) + int(amount or 0)
    for grok_path, session_ids in grok_project_sessions.items():
        proj_map[grok_path]["sessions"] += len({sid for sid in session_ids if sid})

    # 检测本地 LISTEN 端口,匹配项目 cwd
    port_map = _detect_local_servers(set(proj_map.keys()))

    result = []
    for path, info in proj_map.items():
        name = os.path.basename(path.rstrip("/")) or path
        top_model = max(info["model_tok"].items(), key=lambda kv: kv[1])[0] if info["model_tok"] else ""
        entry = {
            "path": path,
            "name": name,
            "last_active": info["last_active"],
            "sessions": info["sessions"],
            "tokens": info["tokens"],
            "cost": round(info["cost"], 2),
            "top_model": top_model,
            "tools": sorted(info["tools"]),
        }
        if path in port_map:
            entry["ports"] = sorted(port_map[path])
        result.append(entry)
    result.sort(key=lambda x: x["last_active"], reverse=True)
    return result


def projects(cache=None):
    res = get_projects(_cache=cache)
    print(json.dumps(res, ensure_ascii=False))
