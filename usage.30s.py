#!/usr/bin/env python3
# <bitbar.title>AI Usage Bar</bitbar.title>
# <bitbar.version>v0.1</bitbar.version>
# <bitbar.author>local</bitbar.author>
# <bitbar.desc>本地 AI coding tools token / 缓存命中 / 花费 / 额度</bitbar.desc>
# <swiftbar.runInBash>false</swiftbar.runInBash>
#
# 数据主要读自本地会话日志,不改动任何 CLI；Codex 额度会短缓存查询官方 live usage。
# Grok 额度默认只读本地 unified.jsonl billing 日志；实时账单接口需显式开启
# (config grok_live_quota_enabled 或 TOKEI_GROK_LIVE_QUOTA=1)。
# Cursor / Zed / Sub2API / z.ai 额度默认关闭；开启卡片后才复用本机登录态或 Keychain
# API Key 查询对应官方/自托管接口。Antigravity 额度只探测已运行的 127.0.0.1 服务。
# --update-prices 仍只在用户显式触发时更新价格表。
#   Claude Code: ~/.claude/projects/<proj>/<session>.jsonl
#   Codex:       ~/.codex/{sessions,archived_sessions}/**/rollout-*.jsonl
#   Pi:          ~/.pi/agent/sessions/**/*.jsonl + ~/.omp/agent/sessions/**/*.jsonl
#   WorkBuddy:   ~/.workbuddy/projects/**/*.jsonl
#   WorkBuddy AI:~/.workbuddy-ai/projects/**/*.jsonl
#   Grok Bot:    ~/Library/Application Support/Grok Bot/sand-client-persistence/*.blob
#   DeepSeek:    ~/.dsh/sessions/**/*.jsonl.zstd
#   Kimi Code:   ~/.kimi-code/sessions/*/*/agents/*/wire.jsonl

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import glob
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import threading
import tempfile as _tempfile
import time as _time
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

# ---------- Core Architecture Imports & Re-exports ----------
from core.config import (
    HOME,
    APPDATA,
    LOCALAPPDATA,
    BASE_DIR,
    _CONFIG_BASE,
    _COGNITALLY_DIR,
    _LEGACY_TOKDASH_DIR,
    _LEGACY_TOKEI_DIR,
    _USER_DIR,
    _SCAN_CACHE_DIR,
    _DEFAULT_SCAN_CACHE_FILE,
    _SCAN_CACHE_FILE,
    _LEGACY_SCAN_CACHE_FILE,
    _PREV_TOKEI_CACHE_FILE,
    _SNAPSHOT_CACHE_FILE,
    _SNAPSHOT_LOCK_FILE,
    _SNAPSHOT_TTL,
    _expand_path,
    _path_candidates,
    _first_existing_file,
    _existing_dirs,
    _writable_path,
    _load_json,
    _atomic_write_json,
    _migrate_legacy_cognitally_state,
    # Agent directories & configs
    CLAUDE_DIR,
    CODEX_DIR,
    CODEX_ARCHIVED_DIR,
    CODEX_AUTH,
    CODEX_CONFIG,
    GEMINI_DIR,
    ANTIGRAVITY_DIR,
    GEMINI_DIRS,
    GROK_HOME,
    GROK_DIR,
    GROK_LOG,
    GROK_AUTH,
    WORKBUDDY_DIR,
    WORKBUDDY_AI_DIR,
    CODEBUDDY_DIR,
    GROK_BOT_DIRS,
    GROK_BOT_SECRET_PATHS,
    GROK_BOT_AUTH_MARKER,
    DEEPSEEK_HARNESS_DIR,
    QODER_IDE_DB,
    QODER_IDE_DB_PATHS,
    _qoder_ide_db_path,
    HERMES_DB,
    OPENCODE_DATA_DIR,
    OPENCODE_DIR,
    OPENCODE_DB,
    OPENCODE_DATA_DIRS,
    ZCODE_DB,
    PI_AGENT_DIR,
    PI_SESSION_DIR,
    OMP_SESSION_DIR,
    _KIMI_CODE_DEFAULT_DIR,
    _KIMI_CODE_LEGACY_DIR,
    KIMI_CODE_DIR,
    # Caches
    PRICING_FILE,
    OVERRIDES_FILE,
    CODEX_QUOTA_CACHE,
    CODEX_RESET_CARDS_CACHE,
    CLAUDE_QUOTA_CACHE,
    GROK_QUOTA_CACHE,
    PROVIDER_QUOTA_CACHE,
    ANTIGRAVITY_SCAN_CACHE,
    _GEMINI_DAYS_CACHE_KEY,
    _GROK_DAYS_CACHE_KEY,
    _CURSOR_PROVIDER_DAYS_CACHE_KEY,
    _ZAI_PROVIDER_DAYS_CACHE_KEY,
    _GROK_BOT_PROVIDER_DAYS_CACHE_KEY,
    # Ranges & tokens
    RANGE_KEYS,
    TOKEN_FIELDS,
    _LEDGER_TOKEN_FIELDS,
    range_bounds,
    range_boundaries,
    classify,
    classify_date,
    parse_ts,
    human,
    token_total,
    _sqlite_ro_uri,
    _sqlite_signature,
    # Empty bucket templates
    _empty_token_bucket,
    _empty_token_day,
    _empty_token_ranges,
    _empty_claude,
    _empty_codex,
    _empty_gemini,
    _empty_grok,
    _empty_qoder,
    _empty_hermes,
    _empty_opencode,
    _empty_pi,
    _empty_workbuddy,
    _empty_grok_bot,
    _empty_deepseek_harness,
    _empty_kimicode,
    _empty_zcode,
)

from core.pricing import (
    _DEFAULT_PRICES,
    _DEEPSEEK_OFFICIAL_PRICES,
    _FAMILY,
    VALID_PROVENANCES,
    _COST_KIND_BY_PROVENANCE,
    _PRICING_DB,
    _OVERRIDES,
    _OV_MODELS,
    _OV_ALIASES,
    reload_pricing,
    _deepseek_official_price,
    _normalize,
    _alias_target_and_prov,
    resolve_pricing_entry,
    _resolve_id,
    _raw_price,
    price_for,
    gemini_price,
    _known_id_or_raw,
    _model_identity_id,
    _exact_pricing_id,
    _pricing_id,
    _has_known_price,
    nice_model,
)

from core.concurrency import (
    _canonical_accounting_digest,
    _canonical_snapshot_digest,
    _compute_state_digest,
    get_canonical_snapshot,
    register_snapshot_generator,
)

from core.accounting import (
    build_daily_costs,
    daily_costs,
    _period_cutoff,
    _gemini_token_total,
    _provider_number,
    _provider_integer,
    _provider_usage_int,
    _ledger_token_sum,
    register_daily_cost_providers,
    get_projects,
    projects,
    _detect_local_servers,
    register_projects_providers,
)

from core.storage import (
    _SCAN_CACHE_VERSION,
    _SCAN_CACHE_MIGRATABLE_VERSION,
    _CODEX_EVENT_CACHE_SUFFIX,
    _CODEX_PARSER_VERSION,
    _CODEX_SCAN_CHECKPOINT_INTERVAL,
    _codex_event_cache_dir,
    _remove_codex_event_cache_dir,
    _migrate_legacy_scan_cache,
    _load_scan_cache,
    _save_scan_cache,
    _load_dashboard_cache,
    _LEDGER_FILE,
    _LEDGER_VERSION,
    _LEDGER_FIELDS,
    _LEDGER_CACHE,
    _load_tokei_config,
    _load_ledger,
    _load_ledger_from_disk,
    ledger_flush,
    _save_ledger,
    _ledger_day_total,
    _ledger_cost_version,
    ledger_reconcile,
    ledger_touch,
    _with_scan_cache_lock,
    _cache_dashboard_days,
    _merge_dashboard_days,
    _iter_cached_token_days,
    _add_model_usage,
    _add_token_usage,
    _merge_token_day,
    _merge_live_token_day,
    _format_token_models,
    _safe_scan,
)

from core.collectors import (
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
    # Codex
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
    # Gemini
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
    # Grok
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
    # Provider Quotas
    _provider_quota_enabled,
    _provider_config_string,
    _provider_percent,
    _provider_epoch,
    _provider_money,
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
    scan_zai_quota as _scan_zai_quota,
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
    # Cursor
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
    # Qoder & Hermes
    _qoder_db_path,
    scan_qoder,
    _empty_qoder_ide,
    scan_qoder_ide,
    _hermes_db_paths,
    _scan_hermes_db,
    _qodercli_dir,
    _empty_qodercli,
    _est_tokens,
    _parse_qodercli_file,
    scan_qodercli,
    scan_hermes,
    # Misc Tools
    _pi_session_dirs,
    _pi_model_id,
    _pi_usage_int,
    _pi_usage_cost,
    scan_pi,
    _workbuddy_number,
    _workbuddy_detail_total,
    _workbuddy_timestamp,
    _workbuddy_usage_record,
    _iter_workbuddy_records,
    _scan_workbuddy_root,
    scan_workbuddy,
    scan_workbuddy_ai,
    scan_codebuddy,
    _grok_bot_parse_blob,
    scan_grok_bot,
    _deepseek_harness_usage_record,
    _iter_deepseek_harness_records,
    scan_deepseek_harness,
    _opencode_db_paths,
    _opencode_json_dirs,
    _opencode_message_day,
    _scan_opencode_database,
    scan_opencode,
    _scan_zcode_database,
    scan_zcode,
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
    # Computation Engine
    fetch_cursor_official_quota,
    _recalc_costs,
    compute,
)

from core.pricing.update import (
    update_prices,
    _scan_local_models,
    _is_exact_match,
    _estimate_from_sibling,
    update_unknown,
)

from core.sync import (
    _TOKEI_CONFIG,
    _load_tokei_config,
    _sync_snapshot_filename,
    _write_sync_snapshot,
    _sync_safe_usage_payload,
    _write_configured_sync_snapshot,
    write_sync_snapshot,
)

from core.reports import (
    _arg_period,
    _streak_info,
    build_wrapped,
    wrapped,
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

from core.diagnostics import doctor


# ---------- Product Boundary Compliance Definition ----------
def scan_zai_quota(cache=None):
    """Z.AI provider quota integration (conforms to test_product_boundary.py)."""
    return _scan_zai_quota(cache)


def main_json():
    d = compute()
    meta = _load_json(PRICING_FILE, {}).get("_meta", {})
    d["_pricing"] = {"updated_at": meta.get("updated_at", ""), "count": meta.get("count", 0)}
    print(json.dumps(d, ensure_ascii=False))
    if "--no-sync-snapshot" not in sys.argv:
        _write_configured_sync_snapshot(d)



def main():
    d = compute()
    c, x = d["claude"], d["codex"]
    ct = c["ranges"]["today"]
    xt = x["ranges"]["today"]
    cc_hit = ct["hit"]
    cc_cost = ct["cost"]
    cur = {"name": c["session_name"]}
    cur_total = c["session_total"]
    cx_hit = xt["hit"]
    p5, pw, r5, rw = x["p5"], x["pw"], x["r5"], x["rw"]
    p5_stale, pw_stale = x.get("p5_stale"), x.get("pw_stale")

    # ---- menu bar 标题(紧凑):⚡Claude命中率  ◷Codex周额度 ----
    parts = [f"⚡{cc_hit:.0f}"]
    if p5 is not None and not p5_stale:
        parts.append(f"◷{p5:.0f}")
    elif pw is not None and not pw_stale:
        parts.append(f"◷{pw:.0f}")
    print(" ".join(parts))
    print("---")

    F = "| font=Menlo size=14"
    HEAD = "| font=Menlo-Bold size=15"
    # Claude 块
    print(f"Claude Code {HEAD}")
    print(f"命中率   {cc_hit:5.1f}% {F}")
    print(f"今日 输入   {human(ct['in']):>6} {F}")
    print(f"今日 输出   {human(ct['out']):>6} {F}")
    print(f"今日 缓存读 {human(ct['cr']):>6} {F}")
    print(f"今日 缓存写 {human(ct['cw']):>6} {F}")
    print(f"今日 ≈成本  ${cc_cost:.2f} {F}")
    print(f"  (按 API 价估,非订阅实付) | font=Menlo size=11")
    print(f"本会话({cur['name']}) {human(cur_total)} {F}")
    print("---")
    # Codex 块
    print(f"Codex {HEAD}")
    print(f"命中率   {cx_hit:5.1f}% {F}")
    print(f"今日 输入   {human(xt['in']):>6} {F}")
    print(f"今日 缓存读 {human(xt['cached']):>6} {F}")
    print(f"今日 输出   {human(xt['out']):>6} {F}")
    if xt.get("reason"):
        print(f"今日 推理   {human(xt['reason']):>6} {F}")
    print(f"今日 ≈成本  ${xt['cost']:.2f} {F}")
    print(f"  (按 API 价估,订阅实付不按此) | font=Menlo size=11")
    if p5 is not None:
        if p5_stale:
            print(f"5h 额度  已过期 {F}")
        else:
            print(f"5h 额度  {p5:5.1f}%  reset {fmt_reset(r5)} {F}")
    if pw is not None:
        if pw_stale:
            print(f"周额度   已过期 {F}")
        else:
            print(f"周额度   {pw:5.1f}%  reset {fmt_reset(rw)} {F}")
    if x["plan"]:
        print(f"plan: {x['plan']} {F}")
    print("---")
    # Gemini / Antigravity 块
    g = d["gemini"]
    gt = g["ranges"]["today"]
    print(f"Gemini / Antigravity {HEAD}")
    print(f"命中率   {gt['hit']:5.1f}% {F}")
    print(f"今日 输入   {human(gt['in']):>6} {F}")
    print(f"今日 输出   {human(gt['out']):>6} {F}")
    print(f"今日 缓存   {human(gt['cached']):>6} {F}")
    if gt.get("thoughts"):
        print(f"今日 推理   {human(gt['thoughts']):>6} {F}")
    print(f"今日 ≈成本  ${gt['cost']:.2f} {F}")
    print(f"  (按 API 价估,非订阅实付) | font=Menlo size=11")
    print("---")
    # Grok Build 块：新版日志展示真实 token，旧版日志降级为上下文快照。
    gk = d["grok"]
    kt = gk["ranges"]["today"]
    print(f"Grok Build {HEAD}")
    print(f"今日 会话   {kt['sessions']:>6} {F}")
    if gk.get("pct") is not None and not gk.get("stale"):
        remaining = 100 - float(gk["pct"])
        print(f"周剩余   {remaining:5.1f}%  reset {fmt_reset(gk.get('reset'))} {F}")
        if gk.get("plan"):
            print(f"plan: {gk['plan']} {F}")
    if kt.get("usage_available"):
        print(f"今日 输入   {human(kt['in']):>6} {F}")
        print(f"今日 缓存   {human(kt['cr']):>6} {F}")
        print(f"今日 输出   {human(kt['out']):>6} {F}")
        if kt.get("reason"):
            print(f"今日 推理   {human(kt['reason']):>6} {F}")
        if kt.get("cost", 0) > 0:
            print(f"今日 ≈成本  ${kt['cost']:.2f} {F}")
    else:
        print(f"上下文快照 {human(kt['ctx_used']):>6} {F}")
    if gk.get("model"):
        print(f"model: {gk['model']} {F}")
    print(f"  (成本按 API 价估,订阅实付不按此) | font=Menlo size=11")
    print("---")
    # Pi 块
    pt = d.get("pi", {}).get("ranges", {}).get("today", {})
    if pt.get("sessions", 0) > 0 or pt.get("in", 0) > 0:
        print(f"Pi Coding Agent {HEAD}")
        print(f"命中率   {pt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(pt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(pt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(pt.get('cr', 0)):>6} {F}")
        print(f"今日 缓存写 {human(pt.get('cw', 0)):>6} {F}")
        print(f"今日 ≈成本  ${pt.get('cost', 0):.2f} {F}")
        print("---")
    # WorkBuddy 块
    wt = d.get("workbuddy", {}).get("ranges", {}).get("today", {})
    if wt.get("sessions", 0) > 0 or wt.get("in", 0) > 0:
        print(f"WorkBuddy {HEAD}")
        print(f"命中率   {wt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(wt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(wt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(wt.get('cr', 0)):>6} {F}")
        print(f"今日 ≈成本  ${wt.get('cost', 0):.2f} {F}")
        print("---")
    # WorkBuddy AI 国际版块
    wat = d.get("workbuddy_ai", {}).get("ranges", {}).get("today", {})
    if wat.get("sessions", 0) > 0 or wat.get("in", 0) > 0:
        print(f"WorkBuddy Intl. {HEAD}")
        print(f"命中率   {wat.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(wat.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(wat.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(wat.get('cr', 0)):>6} {F}")
        print(f"今日 ≈成本  ${wat.get('cost', 0):.2f} {F}")
        print("---")
    # DeepSeek Harness 块
    dt = d.get("deepseek_harness", {}).get("ranges", {}).get("today", {})
    if dt.get("sessions", 0) > 0 or dt.get("in", 0) > 0:
        print(f"DeepSeek Harness {HEAD}")
        print(f"命中率   {dt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(dt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(dt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(dt.get('cr', 0)):>6} {F}")
        if dt.get("reason"):
            print(f"今日 推理   {human(dt['reason']):>6} {F}")
        print(f"今日 ≈成本  ${dt.get('cost', 0):.2f} {F}")
        print("---")
    # GLM Code (ZCode) 块
    zt = d.get("zcode", {}).get("ranges", {}).get("today", {})
    if zt.get("sessions", 0) > 0 or zt.get("in", 0) > 0:
        print(f"GLM Code (ZCode) {HEAD}")
        print(f"命中率   {zt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(zt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(zt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(zt.get('cr', 0)):>6} {F}")
        if zt.get("reason"):
            print(f"今日 推理   {human(zt['reason']):>6} {F}")
        print(f"今日 ≈成本  ${zt.get('cost', 0):.2f} {F}")
        print("---")
    # Kimi Code 块（protocol 1.5 提供模型，但 wire 不持久化实际成本）
    kt = d.get("kimicode", {}).get("ranges", {}).get("today", {})
    if kt.get("sessions", 0) > 0 or kt.get("in", 0) > 0:
        print(f"Kimi Code {HEAD}")
        print(f"命中率   {kt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(kt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(kt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(kt.get('cr', 0)):>6} {F}")
        if kt.get("cw"):
            print(f"今日 缓存写 {human(kt['cw']):>6} {F}")
        print("---")
    # Cursor 块
    cur_t = d.get("cursor", {}).get("ranges", {}).get("today", {})
    if cur_t.get("sessions", 0) > 0 or cur_t.get("in", 0) > 0:
        print(f"Cursor Composer {HEAD}")
        print(f"今日 会话   {cur_t.get('sessions', 0):>6} {F}")
        print(f"今日 输入   {human(cur_t.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(cur_t.get('out', 0)):>6} {F}")
        print(f"今日 ≈成本  ${cur_t.get('cost', 0):.2f} {F}")
        print("---")
    # CodeBuddy 块
    cbt = d.get("codebuddy", {}).get("ranges", {}).get("today", {})
    if cbt.get("sessions", 0) > 0 or cbt.get("in", 0) > 0:
        print(f"CodeBuddy {HEAD}")
        print(f"命中率   {cbt.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(cbt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(cbt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(cbt.get('cr', 0)):>6} {F}")
        print(f"今日 ≈成本  ${cbt.get('cost', 0):.2f} {F}")
        print("---")
    # OpenCode 块
    oct = d.get("opencode", {}).get("ranges", {}).get("today", {})
    if oct.get("sessions", 0) > 0 or oct.get("in", 0) > 0:
        print(f"OpenCode (DeepSeek) {HEAD}")
        print(f"命中率   {oct.get('hit', 0):5.1f}% {F}")
        print(f"今日 输入   {human(oct.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(oct.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(oct.get('cr', 0)):>6} {F}")
        print(f"今日 ≈成本  ${oct.get('cost', 0):.2f} {F}")
        print("---")
    # Hermes 块
    hmt = d.get("hermes", {}).get("ranges", {}).get("today", {})
    if hmt.get("sessions", 0) > 0 or hmt.get("in", 0) > 0:
        print(f"Hermes Agent {HEAD}")
        print(f"今日 输入   {human(hmt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(hmt.get('out', 0)):>6} {F}")
        print(f"今日 ≈成本  ${hmt.get('cost', 0):.2f} {F}")
        print("---")
    # Qoder 块
    qdt = d.get("qoder", {}).get("ranges", {}).get("today", {})
    if qdt.get("sessions", 0) > 0 or qdt.get("in", 0) > 0:
        print(f"Qoder IDE {HEAD}")
        print(f"今日 输入   {human(qdt.get('in', 0)):>6} {F}")
        print(f"今日 输出   {human(qdt.get('out', 0)):>6} {F}")
        print(f"今日 缓存读 {human(qdt.get('cached', 0)):>6} {F}")
        print(f"今日 ≈成本  ${qdt.get('cost', 0):.2f} {F}")
        print("---")
    print("刷新 | refresh=true")




if __name__ == "__main__":
    if "--doctor" in sys.argv:
        sys.exit(doctor(as_json="--json" in sys.argv))
    elif "--mcp" in sys.argv:
        from mcp_server import serve_stdio
        serve_stdio()
    elif "--export" in sys.argv:
        from export_engine import export_canonical_dataset
        fmt = "json"
        for i, a in enumerate(sys.argv):
            if a == "--export" and i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
                fmt = sys.argv[i + 1]
        out_path = None
        for i, a in enumerate(sys.argv):
            if a == "--out" and i + 1 < len(sys.argv):
                out_path = sys.argv[i + 1]
        snap = get_canonical_snapshot(force="--force" in sys.argv)
        content = export_canonical_dataset(snap, format_type=fmt, period=_arg_period(), out_path=out_path)
        if not out_path:
            sys.stdout.write(content)
            if not content.endswith("\n"):
                sys.stdout.write("\n")
    elif "--statusline" in sys.argv or "--status-line" in sys.argv:
        from statusline_engine import render_statusline
        fmt = "default"
        for i, a in enumerate(sys.argv):
            if a in ("--format", "-f") and i + 1 < len(sys.argv):
                fmt = sys.argv[i + 1]
        if "--json" in sys.argv:
            fmt = "json"
        snap = get_canonical_snapshot(force="--force" in sys.argv)
        show_icons = False if "--no-icons" in sys.argv else None
        line = render_statusline(snap, period=_arg_period(default="today"), format_type=fmt, show_icons=show_icons)
        print(line)
    elif "--budget-check" in sys.argv:
        from budget_guard import check_budget
        threshold = None
        for i, a in enumerate(sys.argv):
            if a == "--budget-limit" and i + 1 < len(sys.argv):
                try:
                    threshold = float(sys.argv[i + 1])
                except ValueError:
                    pass
        snap = get_canonical_snapshot(force="--force" in sys.argv)
        report = check_budget(
            snap,
            period=_arg_period(default="today"),
            threshold_usd=threshold,
            send_notification="--notify" in sys.argv
        )
        if "--json" in sys.argv:
            import json
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            status_tag = "🚨 EXCEEDED" if report["exceeded"] else "✅ WITHIN BUDGET"
            limit_str = f"${report['threshold_usd']:.2f}" if report['threshold_usd'] is not None else "Not set"
            print(f"[{status_tag}] {report['period']} cost: ${report['current_cost_usd']:.2f} | Limit: {limit_str}")
    elif "--update-prices" in sys.argv:
        sys.exit(update_prices())
    elif "--update-unknown" in sys.argv:
        sys.exit(update_unknown())
    elif "--dashboard" in sys.argv:
        dashboard()
    elif "--snapshot" in sys.argv:
        snapshot()
    elif "--quota-detail" in sys.argv:
        quota_detail()
    elif "--daily-costs" in sys.argv:
        daily_costs()
    elif "--write-sync" in sys.argv:
        sys.exit(write_sync_snapshot())
    elif "--projects" in sys.argv:
        projects()
    elif "--wrapped" in sys.argv:
        wrapped()
    elif "--json" in sys.argv:
        main_json()
    else:
        main()
