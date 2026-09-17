"""High-Performance Statusline Engine for Cognitally.

Generates ultra-compact, millisecond-fast, single-line telemetry badges
designed for seamless integration into Tmux, Starship, Zsh/Bash prompts,
and terminal statuslines.
"""

import sys
from typing import Dict, Any, Optional


def _can_support_unicode() -> bool:
    """Detect if stdout encoding supports Unicode emoji safely."""
    encoding = getattr(sys.stdout, "encoding", None) or ""
    return "utf" in encoding.lower()


def _human_tokens(count: int) -> str:
    """Format token count into compact human-readable representation (e.g. 1.2k, 3.4M)."""
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        return f"{count / 1000:.1f}k"
    if count < 1_000_000_000:
        return f"{count / 1_000_000:.2f}M"
    return f"{count / 1_000_000_000:.2f}B"


def render_statusline(
    snapshot: Dict[str, Any],
    period: str = "today",
    format_type: str = "default",
    show_icons: Optional[bool] = None
) -> str:
    """Render a single-line statusline badge from a canonical snapshot.
    
    Guarantees:
    - Zero newlines (safe for tmux and shell PS1)
    - Millisecond formatting execution
    - Auto ASCII fallback when Unicode emoji are not supported
    - Graceful zero-loss fallback on empty snapshots
    """
    if show_icons is None:
        show_icons = _can_support_unicode()
    usage = snapshot.get("usage", {}) if isinstance(snapshot, dict) else {}
    
    total_cost = 0.0
    total_tokens = 0
    active_agents = []
    quota_alerts = []

    for agent_key, agent_val in sorted(usage.items()):
        if agent_key.startswith("_") or not isinstance(agent_val, dict):
            continue
        ranges = agent_val.get("ranges", {})
        r = ranges.get(period, {})
        if not isinstance(r, dict):
            continue

        c = float(r.get("cost", 0.0) or 0.0)
        tokens = int(
            (r.get("in", 0) or 0)
            + (r.get("out", 0) or 0)
            + (r.get("cr", 0) or 0)
            + (r.get("cw", 0) or 0)
            + (r.get("reason", 0) or 0)
        )

        if c > 0 or tokens > 0:
            total_cost += c
            total_tokens += tokens
            active_agents.append(agent_key)

        # Quota checks
        limits = agent_val.get("limits")
        if isinstance(limits, dict):
            pct = limits.get("pct") or limits.get("used_pct") or limits.get("creditUsagePercent")
            if pct is not None:
                try:
                    pct_val = float(pct)
                    if pct_val >= 80:
                        quota_alerts.append(f"{agent_key}:{int(pct_val)}%")
                except (ValueError, TypeError):
                    pass

    cost_str = f"${total_cost:.2f}"
    tokens_str = _human_tokens(total_tokens)
    
    if format_type == "cost":
        return cost_str
    if format_type == "tokens":
        return tokens_str
    if format_type == "compact":
        return f"{cost_str} ({tokens_str})"
    if format_type == "json":
        import json
        return json.dumps({
            "period": period,
            "cost_usd": round(total_cost, 4),
            "tokens": total_tokens,
            "tokens_human": tokens_str,
            "active_agents_count": len(active_agents),
            "active_agents": active_agents,
            "quota_alerts": quota_alerts,
            "generation": snapshot.get("generation", "")
        }, ensure_ascii=False)

    # Default format
    icon = "⚡ " if show_icons else ""
    parts = [f"{icon}{cost_str}", f"{tokens_str} tok"]
    
    if len(active_agents) > 0:
        agents_label = f"{len(active_agents)} agent" if len(active_agents) == 1 else f"{len(active_agents)} agents"
        parts.append(agents_label)

    if quota_alerts:
        alert_prefix = "⚠️ " if show_icons else "[!] "
        parts.append(f"{alert_prefix}{' '.join(quota_alerts)}")

    return " | ".join(parts)
