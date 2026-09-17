"""Reports package: wrapped, quota detail, and dashboard snapshots.
"""

from core.reports.wrapped import (
    _arg_period,
    _streak_info,
    build_wrapped,
    wrapped,
)
from core.reports.dashboard import (
    _load_dashboard_cache,
    _quota_local_day_range,
    _quota_device_ledgers,
    _quota_day_tokens,
    _quota_daily_from_tools,
    _quota_window_days,
    _quota_day_hour_bounds,
    _quota_claude_events,
    _quota_codex_events,
    _quota_peer_boundary,
    _quota_window_tokens,
    _quota_tool_reading,
    _load_quota_anchors,
    _save_quota_anchors,
    _record_quota_anchor,
    _merge_quota_anchors,
    _quota_anchor_cycles,
    _quota_cycle_specs,
    build_quota_detail,
    quota_detail,
    build_dashboard,
    dashboard,
    _default_snapshot_generator,
    snapshot,
)
