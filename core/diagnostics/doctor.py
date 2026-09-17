"""Diagnostics and Health Probe Engine for Cognitally / TokDash.

Probes local AI coding agents, log directories, file accessibility,
activity timestamps, and pricing engine metadata.
"""

import os
import sys
import glob
import json
import platform
from datetime import datetime

from core.config import (
    HOME,
    CLAUDE_DIR,
    CODEX_DIR,
    KIMI_CODE_DIR,
    DEEPSEEK_HARNESS_DIR,
    OPENCODE_DATA_DIR,
    PI_AGENT_DIR,
    ZCODE_DB,
    CODEBUDDY_DIR,
)
from core.pricing import (
    _PRICING_DB,
    _OV_MODELS,
    _OV_ALIASES,
)


def doctor(as_json=False, return_dict=False):
    """诊断本机各主流 AI Coding Agent 采集器路径、健康度与元数据可用性。"""
    # 1. 探测各主流 Agent
    probes = []
    
    def _inspect_files(name, pattern, recursive=True):
        try:
            files = glob.glob(pattern, recursive=recursive)
            if not files:
                return {"name": name, "installed": False, "readable": True, "files": 0, "last_activity": None, "status": "not_installed"}
            last_mtime = max(os.path.getmtime(f) for f in files)
            last_dt = datetime.fromtimestamp(last_mtime).astimezone().strftime("%Y-%m-%d %H:%M")
            return {"name": name, "installed": True, "readable": True, "files": len(files), "last_activity": last_dt, "status": "healthy"}
        except Exception as e:
            return {"name": name, "installed": True, "readable": False, "files": 0, "last_activity": None, "status": f"error: {e}"}

    def _inspect_file(name, file_path):
        if not os.path.exists(file_path):
            return {"name": name, "installed": False, "readable": True, "files": 0, "last_activity": None, "status": "not_installed"}
        try:
            st = os.stat(file_path)
            last_dt = datetime.fromtimestamp(st.st_mtime).astimezone().strftime("%Y-%m-%d %H:%M")
            return {"name": name, "installed": True, "readable": True, "files": 1, "last_activity": last_dt, "status": "healthy"}
        except Exception as e:
            return {"name": name, "installed": True, "readable": False, "files": 0, "last_activity": None, "status": f"error: {e}"}

    # 14 mainstream agents
    probes.append(_inspect_files("Claude Code", os.path.join(CLAUDE_DIR, "**", "*.jsonl")))
    probes.append(_inspect_files("Codex CLI", os.path.join(CODEX_DIR, "**", "*.jsonl")))
    probes.append(_inspect_files("Grok Build", os.path.join(HOME, ".tokei", "*grok*")))
    probes.append(_inspect_files("Grok Bot", os.path.join(HOME, ".grok-bot", "**", "*")))
    probes.append(_inspect_file("Cursor Composer", os.path.join(HOME, ".config", "Cursor", "User", "globalStorage", "state.vscdb")))
    probes.append(_inspect_files("Antigravity (AGY) / Gemini", os.path.join(HOME, ".gemini", "**", "*.db")))
    probes.append(_inspect_files("Kimi Code", os.path.join(KIMI_CODE_DIR, "**", "*.jsonl")))
    probes.append(_inspect_files("DeepSeek Harness", os.path.join(DEEPSEEK_HARNESS_DIR, "**", "*.jsonl")))
    probes.append(_inspect_files("OpenCode", os.path.join(OPENCODE_DATA_DIR, "**", "*.json*")))
    probes.append(_inspect_files("Hermes Agent", os.path.join(HOME, ".hermes", "**", "*")))
    probes.append(_inspect_files("Pi Agent CLI", os.path.join(PI_AGENT_DIR, "**", "*.json*")))
    probes.append(_inspect_file("GLM Code (ZCode)", ZCODE_DB))
    probes.append(_inspect_files("CodeBuddy / WorkBuddy", os.path.join(CODEBUDDY_DIR, "**", "*")))
    probes.append(_inspect_files("Qoder", os.path.join(HOME, ".qoder", "**", "*")))

    # Environment & metadata info
    meta_info = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "pricing_models_count": len(_PRICING_DB),
        "pricing_overrides_count": len(_OV_MODELS) + len(_OV_ALIASES),
        "active_collectors": sum(1 for p in probes if p["installed"]),
        "timestamp": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }

    report = {
        "system": meta_info,
        "agents": probes,
    }

    if return_dict:
        return report

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    # ASCII Format
    print("=" * 82)
    print("🩺 Cognitally / TokDash Doctor — AI Agent Health & Collector Diagnostics")
    print(f"System: {meta_info['platform']} | Python: {meta_info['python_version']}")
    print(f"Pricing Catalog: {meta_info['pricing_models_count']} models | Overrides: {meta_info['pricing_overrides_count']} rules")
    print("=" * 82)
    print(f"{'AI Coding Agent':<28} {'Status':<14} {'Files':<8} {'Last Activity':<18} {'Health'}")
    print("-" * 82)
    for p in probes:
        status_str = "DETECTED" if p["installed"] else "NOT FOUND"
        health_icon = "✅ OK" if p["status"] == "healthy" else ("⚪ IDLE" if p["status"] == "not_installed" else f"❌ {p['status']}")
        act = p["last_activity"] or "—"
        print(f"{p['name']:<28} {status_str:<14} {p['files']:<8} {act:<18} {health_icon}")
    print("=" * 82)
    print(f"Detected {meta_info['active_collectors']}/{len(probes)} mainstream coding agents on this machine.")
    print("Run `cognitally` (or `tokdash`) or `./start.sh` to launch desktop observatory.")
    return 0
