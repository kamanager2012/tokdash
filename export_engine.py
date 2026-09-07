"""Canonical Export Engine for Cognitally.

Exports deterministic token usage, cost data, and model breakdowns to standard
JSON or CSV formats conforming to canonical schema specifications.
"""

import os
import sys
import json
import csv
import io
from datetime import datetime
from typing import Optional, Dict, Any


def export_canonical_dataset(
    snapshot: Dict[str, Any],
    format_type: str = "json",
    period: str = "all",
    out_path: Optional[str] = None
) -> str:
    """Export snapshot dataset to structured JSON or tabular CSV.
    
    Returns the serialized string and optionally writes it to `out_path`.
    """
    format_type = format_type.strip().lower()
    if format_type not in ("json", "csv"):
        raise ValueError(f"Unsupported export format '{format_type}'. Allowed: 'json', 'csv'")

    if format_type == "json":
        export_payload = {
            "schema_version": "1.0.0",
            "generator": "cognitally",
            "snapshot_id": snapshot.get("snapshot_id"),
            "generation": snapshot.get("generation"),
            "exported_at": datetime.now().astimezone().isoformat(),
            "period": period,
            "usage": snapshot.get("usage", {}),
            "daily_costs": snapshot.get("daily_costs", {}),
            "projects": snapshot.get("projects", [])
        }
        content = json.dumps(export_payload, ensure_ascii=False, indent=2)
    else:
        # CSV format
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "snapshot_generation",
            "agent",
            "period",
            "model",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost_usd",
            "pricing_provenance",
            "pricing_source",
            "cost_kind"
        ])
        
        gen = snapshot.get("generation", "")
        usage = snapshot.get("usage", {})
        for agent_key, agent_val in sorted(usage.items()):
            if agent_key.startswith("_"):
                continue
            ranges = agent_val.get("ranges", {})
            r = ranges.get(period) or ranges.get("all") or {}
            models = r.get("models", [])
            for m in models:
                writer.writerow([
                    gen,
                    agent_key,
                    period,
                    m.get("name") or m.get("model_id", "unknown"),
                    m.get("in", 0),
                    m.get("out", 0),
                    m.get("cr", 0),
                    m.get("cw", 0),
                    m.get("reason", 0),
                    f"{m.get('cost', 0.0):.6f}",
                    m.get("pricing_provenance", "unknown"),
                    m.get("pricing_source", ""),
                    m.get("cost_kind", "standard")
                ])
        content = buffer.getvalue()

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

    return content
