"""Active Budget Guardrails and Desktop Notifications for Cognitally.

Monitors spending rate and thresholds, issuing proactive desktop alerts
via Linux native notification daemons (notify-send / libnotify) when
budget boundaries are exceeded.
"""

import os
import shutil
import subprocess
from typing import Dict, Any, Optional


def check_budget(
    snapshot: Dict[str, Any],
    period: str = "today",
    threshold_usd: Optional[float] = None,
    send_notification: bool = False
) -> Dict[str, Any]:
    """Inspect snapshot spending against configured budget threshold.
    
    If threshold is exceeded and send_notification is True, triggers a Linux
    desktop alert via notify-send.
    """
    usage = snapshot.get("usage", {}) if isinstance(snapshot, dict) else {}
    
    current_cost = 0.0
    for agent_key, agent_val in usage.items():
        if agent_key.startswith("_") or not isinstance(agent_val, dict):
            continue
        r = agent_val.get("ranges", {}).get(period, {})
        if isinstance(r, dict):
            current_cost += float(r.get("cost", 0.0) or 0.0)

    current_cost = round(current_cost, 4)

    # If no threshold provided, read from environment or default
    if threshold_usd is None:
        env_val = os.environ.get("COGNITALLY_BUDGET_LIMIT") or os.environ.get("TOKDASH_BUDGET_LIMIT")
        if env_val:
            try:
                threshold_usd = float(env_val)
            except ValueError:
                threshold_usd = None

    exceeded = False
    if threshold_usd is not None and threshold_usd > 0:
        exceeded = current_cost >= threshold_usd

    notified = False
    if exceeded and send_notification:
        notified = _trigger_desktop_notification(current_cost, threshold_usd, period)

    return {
        "period": period,
        "current_cost_usd": current_cost,
        "threshold_usd": threshold_usd,
        "exceeded": exceeded,
        "notified": notified,
        "generation": snapshot.get("generation", "")
    }


def _trigger_desktop_notification(cost: float, threshold: float, period: str) -> bool:
    """Send native Linux desktop notification via notify-send."""
    notify_bin = shutil.which("notify-send")
    if not notify_bin:
        return False

    title = "⚠️ Cognitally: Budget Limit Exceeded!"
    body = f"AI coding agent spending for {period} has reached ${cost:.2f} (Limit: ${threshold:.2f})."
    
    try:
        subprocess.run(
            [notify_bin, "-u", "critical", "-a", "Cognitally", title, body],
            timeout=2,
            check=False,
            capture_output=True
        )
        return True
    except Exception:
        return False
