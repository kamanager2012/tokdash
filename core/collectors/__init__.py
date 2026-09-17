"""Collectors module package.

Aggregates all tool-specific scanners, scrapers, quota integrations, and the core
compute() engine.
"""

from core.collectors.claude import (
    _claude_event_total,
    _prefer_claude_event,
    _dedupe_claude_events,
    scan_claude,
    _claude_usage,
    fmt_reset,
    _claude_cache_records,
    _claude_cache_files,
    _iso_to_epoch,
    _zstd_decompress,
    _claude_record_signature,
    _load_claude_quota_state,
    _save_claude_quota_state,
    _parse_claude_quota_record,
    _claude_quota_with_freshness,
    _scan_claude_plan_raw,
    scan_claude_plan,
)

from core.collectors.codex import (
    _CODEX_QUOTA_TTL,
    _CODEX_QUOTA_FALLBACK_TTL,
    _CODEX_USAGE_URL,
    _CODEX_USAGE_MAX_RESPONSE_BYTES,
    _CODEX_RESET_CARDS_URL,
    _CODEX_RESET_CARDS_REFRESH_INTERVAL,
    _CODEX_RESET_CARDS_RETRY_INTERVAL,
    _CODEX_RESET_CARDS_MAX_RESPONSE_BYTES,
    _window_from_codex_live,
    _codex_live_to_limits,
    _codex_limits_have_active_window,
    _cached_codex_live_limits,
    _codex_live_snapshot_is_current,
    _decode_jwt_claims,
    _codex_config,
    _codex_is_custom_provider,
    _codex_auth_context,
    fetch_codex_live_limits,
    _normalize_codex_reset_cards,
    _cached_codex_reset_cards,
    _codex_reset_cards_next_attempt,
    _save_codex_reset_cards_state,
    fetch_codex_reset_cards,
    _codex_event_key,
    _codex_event_cache_dir,
    _codex_event_cache_path,
    _codex_event_cache_ready,
    _codex_write_event_cache,
    _codex_append_event_cache,
    _codex_remove_event_cache,
    _codex_clear_event_cache,
    _iter_codex_cached_events,
    _codex_event_metadata,
    _codex_days_from_cached_events,
    _codex_entry_prefix_key,
    _codex_cached_prefix_match_count,
    _codex_cached_burst_count,
    _codex_cached_drop_count,
    _codex_migrate_event_cache,
    _codex_add_event,
    _codex_prefix_match_count,
    _codex_replayed_event_indexes,
    _codex_deduped_days,
    _codex_decode_json_string,
    _codex_probe_record_header,
    _iter_codex_usage_records,
    _codex_complete_offset,
    _codex_offset_guard,
    _iter_codex_token_lines,
    _codex_session_meta,
    _codex_rollout_files,
    _codex_canonical_file_cache,
    scan_codex,
    _codex_used_since,
    _codex_quota_values,
)

from core.collectors.gemini import (
    _gemini_session_files,
    _decode_proto_varint,
    _parse_proto_fields,
    _antigravity_gen_step,
    _decode_packed_varints,
    _antigravity_step_timestamp,
    _load_antigravity_db,
    _gemini_apply_messages,
    _load_gemini_usage_file,
    scan_gemini,
)

from core.collectors.grok import (
    _grok_file_signature,
    _load_grok_session,
    _grok_usage_record,
    _grok_usage_cost,
    _load_grok_usage_records,
    _grok_usage_days,
    _tokei_config,
    _grok_live_quota_enabled,
    _grok_auth_token,
    _normalize_grok_billing,
    _scan_grok_billing_from_log,
    _cached_grok_quota,
    _save_grok_quota_cache,
    fetch_grok_live_quota,
    scan_grok_quota,
    scan_grok,
)

from core.collectors.quotas import (
    _provider_quota_enabled,
    _provider_config_string,
    _provider_number,
    _provider_integer,
    _provider_percent,
    _provider_epoch,
    _provider_money,
    _provider_usage_int,
    _provider_usage_from_days,
    _provider_window,
    _provider_credential_marker,
    _cached_provider_quota,
    _latest_cached_provider_quota,
    _save_provider_quota_cache,
    _provider_quota_recent_attempt_result,
    _save_provider_quota_attempt,
    _provider_json_request,
    _grok_bot_active_account_id,
    _grok_bot_authorization_generation,
    _grok_bot_helper_path,
    _grok_bot_helper_sand_usage,
    _grok_bot_usage_from_bridge,
    _grok_bot_provider_data,
    _normalize_grok_bot_quota,
    _grok_bot_quota_from_cursor,
    _grok_bot_usage_only_fallback,
    fetch_grok_bot_quota,
    scan_grok_bot_quota,
    _local_timezone_name,
    _zai_quota_url,
    _zai_model_usage_url,
    _normalize_zai_model_usage,
    _zai_limit,
    _zai_limit_detail,
    _normalize_zai_quota,
    fetch_zai_quota,
    scan_zai_quota,
    _antigravity_extract_flag,
    _antigravity_process_kind,
    _antigravity_process_infos,
    _antigravity_scan_recently_empty,
    _record_antigravity_scan,
    _antigravity_running_processes,
    _antigravity_listening_ports,
    _antigravity_endpoints,
    _antigravity_request,
    _antigravity_remaining,
    _normalize_antigravity_quota_summary,
    _normalize_antigravity_user_status,
    fetch_antigravity_quota,
    scan_antigravity_quota,
    scan_provider_quotas,
)

from core.collectors.cursor import (
    _cursor_app_auth_paths,
    _cursor_app_session,
    _cursor_cookie_session,
    _cursor_session,
    _cursor_plan_name,
    _normalize_cursor_usage_events,
    _cursor_boundary_overlap,
    _fetch_cursor_usage_events,
    _normalize_cursor_quota,
    fetch_cursor_quota,
    scan_cursor_quota,
    scan_cursor,
)

from core.collectors.qoder import (
    _qoder_db_path,
    scan_qoder,
    _empty_qoder_ide,
    scan_qoder_ide,
    _qodercli_dir,
    _empty_qodercli,
    _est_tokens,
    _parse_qodercli_file,
    scan_qodercli,
)

from core.collectors.hermes import (
    _hermes_db_paths,
    _scan_hermes_db,
    scan_hermes,
)

from core.collectors.pi import (
    _pi_session_dirs,
    _pi_model_id,
    _pi_usage_int,
    _pi_usage_cost,
    scan_pi,
)

from core.collectors.codebuddy import (
    _workbuddy_number,
    _workbuddy_detail_total,
    _workbuddy_timestamp,
    _workbuddy_usage_record,
    _iter_workbuddy_records,
    _scan_workbuddy_root,
    scan_workbuddy,
    scan_workbuddy_ai,
    scan_codebuddy,
)

from core.collectors.grok_bot import (
    _grok_bot_parse_blob,
    scan_grok_bot,
)

from core.collectors.deepseek import (
    _deepseek_harness_usage_record,
    _iter_deepseek_harness_records,
    scan_deepseek_harness,
)

from core.collectors.opencode import (
    _opencode_db_paths,
    _opencode_json_dirs,
    _opencode_message_day,
    _scan_opencode_database,
    scan_opencode,
)

from core.collectors.glm import (
    _scan_zcode_database,
    scan_zcode,
)

from core.collectors.kimicode import (
    _kimi_roots,
    _kimi_wire_groups,
    _kimi_wire_files,
    _kimi_mirror_sources,
    _kimi_group_signature,
    _kimi_record_counts,
    _kimi_project_map,
    _kimi_wire_context,
    _kimi_events,
    _kimi_token,
    _kimi_datetime,
    _scan_kimi_wire,
    scan_kimicode,
)

from core.collectors.compute import (
    fetch_cursor_official_quota,
    _recalc_costs,
    compute,
)
