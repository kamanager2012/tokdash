"""Canonical Accounting-State SHA-256 Digest Calculation.

Generates a deterministic 16-character hexadecimal generation token from
usage telemetry, daily costs history, and project attribution.
"""

import json
import hashlib


def _canonical_accounting_digest(usage_data, daily_data, projects_data):
    """计算核心账务状态规范化数据摘要 (Canonical Accounting-State SHA-256 Digest)。
    覆盖所有工具各时段全部计量计数 (in, out, cr, cw, reason, cost, sessions)、
    模型级定价来源与归属 (pricing_provenance, pricing_source)、
    所有每日历史行与工具细分、以及项目成本与 Token 归属。
    """
    # 1. 抽取规范化 usage 状态映射 (含模型级定价来源)
    canonical_tools = {}
    for k in sorted(usage_data.keys()):
        if k.startswith("_"):
            continue
        tool_val = usage_data.get(k)
        if not isinstance(tool_val, dict):
            continue
        ranges = tool_val.get("ranges") or {}
        tool_ranges = {}
        for rk in sorted(ranges.keys()):
            r = ranges[rk]
            models_summary = []
            for m in sorted((r.get("models") or []), key=lambda x: str(x.get("name") or x.get("model_id") or "")):
                if isinstance(m, dict):
                    models_summary.append({
                        "name": str(m.get("name") or m.get("model_id") or ""),
                        "cost": round(float(m.get("cost", 0.0) or 0.0), 4),
                        "pricing_provenance": str(m.get("pricing_provenance") or ""),
                        "pricing_source": str(m.get("pricing_source") or ""),
                    })
            tool_ranges[rk] = {
                "in": r.get("in", 0),
                "out": r.get("out", 0),
                "cr": r.get("cr", 0) or r.get("cached", 0),
                "cw": r.get("cw", 0),
                "reason": r.get("reason", 0),
                "cost": round(float(r.get("cost", 0.0) or 0.0), 4),
                "sessions": len(r.get("sessions") or []) if isinstance(r.get("sessions"), (list, set)) else int(r.get("sessions") or 0),
                "models": models_summary,
            }
        canonical_tools[k] = tool_ranges

    # 2. 抽取规范化 daily 数据
    days_list = daily_data.get("daily", []) if isinstance(daily_data, dict) else (daily_data if isinstance(daily_data, list) else [])
    canonical_days = []
    for d in days_list:
        canonical_days.append({
            "date": d.get("date"),
            "total": round(float(d.get("total", 0.0) or 0.0), 2),
            "tokens": d.get("tokens", 0),
            "tool_costs": d.get("tool_costs") or {},
        })

    # 3. 抽取规范化 projects 数据
    canonical_projects = []
    for p in (projects_data or []):
        canonical_projects.append({
            "path": p.get("path"),
            "cost": round(float(p.get("cost", 0.0) or 0.0), 2),
            "tokens": p.get("tokens", 0),
        })

    state_obj = {
        "tools": canonical_tools,
        "daily": canonical_days,
        "projects": canonical_projects,
    }
    canonical_json = json.dumps(state_obj, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:16]


_canonical_snapshot_digest = _canonical_accounting_digest
_compute_state_digest = _canonical_accounting_digest
