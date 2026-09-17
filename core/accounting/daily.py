"""Daily Cost Aggregation Engine for Cognitally / TokDash.

Aggregates daily and per-model costs and token metrics from scan caches,
combining live telemetry with persistent ledger high-water marks.
"""

import json
import math
import sys
from datetime import date, timedelta

from core.config import (
    TOKEN_FIELDS,
    _LEDGER_TOKEN_FIELDS,
    _GEMINI_DAYS_CACHE_KEY,
    _GROK_DAYS_CACHE_KEY,
    _CURSOR_PROVIDER_DAYS_CACHE_KEY,
    _ZAI_PROVIDER_DAYS_CACHE_KEY,
    _GROK_BOT_PROVIDER_DAYS_CACHE_KEY,
    token_total,
)
from core.pricing import (
    nice_model,
    resolve_pricing_entry,
    _COST_KIND_BY_PROVENANCE,
)

_PROVIDER_USAGE_FIELDS = ("in", "out", "cr", "cw", "reason")

from core.storage import _load_scan_cache, _load_ledger, _load_dashboard_cache

_PROVIDERS = {
    "compute": None,
}


def register_daily_cost_providers(load_scan_cache=None, load_ledger=None, compute=None, **kwargs):
    """Register data provider callbacks."""
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


def _period_cutoff(period):
    cutoff = None
    today = date.today()
    if period == "1d":
        cutoff = today.isoformat()
    elif period == "7d":
        cutoff = (today - timedelta(days=today.weekday())).isoformat()
    elif period == "30d":
        cutoff = today.replace(day=1).isoformat()
    elif period == "365d":
        cutoff = today.replace(month=1, day=1).isoformat()
    return cutoff


def _gemini_token_total(day):
    input_total = int(day.get("in", 0) or 0)
    cached = min(int(day.get("cached", 0) or 0), input_total)
    return max(input_total - cached, 0) + cached + int(day.get("out", 0) or 0) \
        + int(day.get("thoughts", 0) or 0)


def _provider_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (TypeError, ValueError):
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _provider_integer(value):
    number = _provider_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _provider_usage_int(value):
    number = _provider_integer(value)
    return number if number is not None and number >= 0 else 0


def _ledger_token_sum(day):
    tok = 0
    for field in _LEDGER_TOKEN_FIELDS:
        value = day.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tok += int(value)
    return tok


def build_daily_costs(period="all", refresh=True, _cache=None,
                      _load_scan_cache_fn=None, _load_ledger_fn=None, _compute_fn=None):
    """按天+按模型的成本 JSON 数据,从扫描缓存聚合。"""
    cutoff = _period_cutoff(period)
    if refresh:
        compute_fn = _compute_fn or _resolve_provider("compute")
        compute_fn()
    load_cache = _load_scan_cache_fn or _resolve_provider("load_scan_cache")
    cache = _cache if _cache is not None else load_cache()
    days = {}
    models = {}
    live_tool_tokens = {}   # {day: {ledger工具名: 实时token}},供账本逐工具高水位合并

    def _add_day_tokens(d, dk, tool, amount):
        d["tokens"] += amount
        per_tool = live_tool_tokens.setdefault(dk, {})
        per_tool[tool] = per_tool.get(tool, 0) + amount

    _empty = lambda: {"claude": 0.0, "codex": 0.0, "gemini": 0.0, "grok": 0.0,
                       "zcode": 0.0, "mimocode": 0.0, "pi": 0.0,
                       "workbuddy": 0.0, "workbuddy_ai": 0.0, "codebuddy": 0.0, "cursor": 0.0,
                       "deepseek_harness": 0.0,
                       "opencode": 0.0, "qwencode": 0.0, "kimicode": 0.0,
                       "prime_agent": 0.0,
                       "hermes": 0.0, "openclaw": 0.0,
                       "c_in": 0, "c_out": 0, "c_cr": 0, "c_cw": 0,
                       "x_in": 0, "x_out": 0, "x_cached": 0, "x_reason": 0,
                       "p_in": 0, "p_out": 0, "p_cr": 0, "p_cw": 0, "p_reason": 0,
                       "pa_in": 0, "pa_out": 0, "pa_cr": 0, "pa_cw": 0, "pa_reason": 0,
                       "w_in": 0, "w_out": 0, "w_cr": 0, "w_cw": 0,
                       "wa_in": 0, "wa_out": 0, "wa_cr": 0, "wa_cw": 0,
                       "cb_in": 0, "cb_out": 0, "cb_cr": 0, "cb_cw": 0,
                       "d_in": 0, "d_out": 0, "d_cr": 0, "d_cw": 0, "d_reason": 0,
                       "q_in": 0, "q_out": 0, "q_cr": 0, "q_reason": 0,
                       "g_in": 0, "g_out": 0, "g_cr": 0, "g_reason": 0,
                       "tokens": 0, "sessions": 0}

    for fp, entry in cache.get("claude", {}).items():
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["claude"] += day.get("cost", 0)
            d["c_in"] += day.get("in", 0); d["c_out"] += day.get("out", 0)
            d["c_cr"] += day.get("cr", 0); d["c_cw"] += day.get("cw", 0)
            _add_day_tokens(d, dk, "claude",
                            day.get("in", 0) + day.get("out", 0) + day.get("cr", 0) + day.get("cw", 0))
            d["sessions"] += 1
            for mn, mv in day.get("models", {}).items():
                nm = nice_model(mn)
                m = models.setdefault(nm, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0, "tool": "claude"})
                m["cost"] += mv.get("cost", 0)
                m["in"] += mv.get("in", 0); m["out"] += mv.get("out", 0)
                m["cr"] += mv.get("cr", 0); m["cw"] += mv.get("cw", 0)

    for fp, entry in cache.get("codex", {}).items():
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["codex"] += day.get("cost", 0)
            d["x_in"] += day.get("in", 0); d["x_out"] += day.get("out", 0)
            d["x_cached"] += day.get("cached", 0); d["x_reason"] += day.get("reason", 0)
            _add_day_tokens(d, dk, "codex",
                            day.get("in", 0) + day.get("out", 0) + day.get("reason", 0))
            for mn, mv in day.get("models", {}).items():
                name = f"{nice_model(mn)} (Codex)"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                  "cr": 0, "cw": 0, "reason": 0,
                                                  "tool": "codex"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for dk, day in cache.get(_GEMINI_DAYS_CACHE_KEY, {}).items():
        if cutoff and dk < cutoff:
            continue
        d = days.setdefault(dk, _empty())
        d["gemini"] += day.get("cost", 0)
        _add_day_tokens(d, dk, "gemini", _gemini_token_total(day))
        for model_name, usage in day.get("models", {}).items():
            cached = min(int(usage.get("cached", 0) or 0), int(usage.get("in", 0) or 0))
            name = f"{nice_model(model_name)} (Gemini)"
            model = models.setdefault(
                name, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                       "reason": 0, "tool": "gemini"})
            model["cost"] += usage.get("cost", 0)
            model["in"] += max(int(usage.get("in", 0) or 0) - cached, 0)
            model["out"] += int(usage.get("out", 0) or 0)
            model["cr"] += cached
            model["reason"] += int(usage.get("thoughts", 0) or 0)

    for fp, entry in cache.get("prime_agent", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["prime_agent"] += day.get("cost", 0)
            d["pa_in"] += day.get("in", 0); d["pa_out"] += day.get("out", 0)
            d["pa_cr"] += day.get("cr", 0); d["pa_cw"] += day.get("cw", 0)
            d["pa_reason"] += day.get("reason", 0)
            d["tokens"] += token_total(day)
            for model_name, usage in day.get("models", {}).items():
                name = f"{nice_model(model_name)} (Prime Agent)"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                 "cr": 0, "cw": 0, "reason": 0,
                                                 "tool": "prime_agent"})
                model["cost"] += usage.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += usage.get(key, 0)

    for dk, day in cache.get(_GROK_DAYS_CACHE_KEY, {}).items():
        if cutoff and dk < cutoff:
            continue
        d = days.setdefault(dk, _empty())
        d["grok"] += day.get("cost", 0)
        d["g_in"] += day.get("in", 0); d["g_out"] += day.get("out", 0)
        d["g_cr"] += day.get("cr", 0); d["g_reason"] += day.get("reason", 0)
        _add_day_tokens(d, dk, "grok", token_total(day))
        for model_name, usage in day.get("models", {}).items():
            name = f"{nice_model(model_name)} (Grok Build)"
            model = models.setdefault(
                name, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                       "reason": 0, "tool": "grok"})
            model["cost"] += usage.get("cost", 0)
            model["tokens"] = model.get("tokens", 0) + token_total(usage)
            for key in TOKEN_FIELDS:
                model[key] += usage.get(key, 0)

    for fp, entry in cache.get("pi", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["pi"] += day.get("cost", 0)
            d["p_in"] += day.get("in", 0); d["p_out"] += day.get("out", 0)
            d["p_cr"] += day.get("cr", 0); d["p_cw"] += day.get("cw", 0)
            d["p_reason"] += day.get("reason", 0)
            _add_day_tokens(d, dk, "pi", token_total(day))
            for mn, mv in day.get("models", {}).items():
                name = f"{nice_model(mn)} (Pi)"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                  "cr": 0, "cw": 0, "reason": 0,
                                                  "tool": "pi"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for fp, entry in cache.get("opencode", {}).items():
        for dk, day_data in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["opencode"] += day_data.get("cost", 0)
            _add_day_tokens(d, dk, "opencode", token_total(day_data))
            for mn, mv in day_data.get("models", {}).items():
                name = f"{nice_model(mn)} (OpenCode)"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                  "cr": 0, "cw": 0, "reason": 0,
                                                  "tool": "opencode"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for tool_key, in_k, out_k, cr_k, cw_k, suffix in (
            ("workbuddy", "w_in", "w_out", "w_cr", "w_cw", "WorkBuddy"),
            ("workbuddy_ai", "wa_in", "wa_out", "wa_cr", "wa_cw", "WorkBuddy Intl.")):
        for fp, entry in cache.get(tool_key, {}).items():
            for dk, day_data in entry.get("days", {}).items():
                if cutoff and dk < cutoff:
                    continue
                d = days.setdefault(dk, _empty())
                d[tool_key] += day_data.get("cost", 0)
                d[in_k] += day_data.get("in", 0); d[out_k] += day_data.get("out", 0)
                d[cr_k] += day_data.get("cr", 0); d[cw_k] += day_data.get("cw", 0)
                _add_day_tokens(d, dk, tool_key, token_total(day_data))
                for mn, mv in day_data.get("models", {}).items():
                    name = f"{nice_model(mn)} ({suffix})"
                    model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                      "cr": 0, "cw": 0, "reason": 0,
                                                      "tool": tool_key})
                    model["cost"] += mv.get("cost", 0)
                    for key in TOKEN_FIELDS:
                        model[key] += mv.get(key, 0)

    for tool_key, in_k, out_k, cr_k, cw_k, suffix in (
            ("codebuddy", "cb_in", "cb_out", "cb_cr", "cb_cw", "CodeBuddy"),):
        for fp, entry in cache.get(tool_key, {}).items():
            for record in entry.get("records", []):
                if not isinstance(record, dict):
                    continue
                dk = record.get("date") or ""
                if cutoff and dk < cutoff:
                    continue
                d = days.setdefault(dk, _empty())
                d[tool_key] += record.get("cost", 0)
                d[in_k] += record.get("in", 0); d[out_k] += record.get("out", 0)
                d[cr_k] += record.get("cr", 0); d[cw_k] += record.get("cw", 0)
                _add_day_tokens(d, dk, tool_key, token_total(record))
                name = f"{nice_model(record.get('model', 'unknown'))} ({suffix})"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                  "cr": 0, "cw": 0, "reason": 0,
                                                  "tool": tool_key})
                model["cost"] += record.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += record.get(key, 0)

    for fp, entry in cache.get("cursor", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day_data in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["cursor"] += day_data.get("cost", 0)
            _add_day_tokens(d, dk, "cursor", token_total(day_data))
            name = f"{nice_model(day_data.get('model', 'Composer 2.5'))} (Cursor)"
            model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                              "cr": 0, "cw": 0, "reason": 0,
                                              "tool": "cursor"})
            model["cost"] += day_data.get("cost", 0)
            for key in TOKEN_FIELDS:
                model[key] += day_data.get(key, 0)

    for fp, entry in cache.get("deepseek_harness", {}).items():
        if not isinstance(entry, dict):
            continue
        for record in entry.get("records", []):
            if not isinstance(record, dict):
                continue
            dk = record.get("date") or ""
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["deepseek_harness"] += record.get("cost", 0)
            d["d_in"] += record.get("in", 0); d["d_out"] += record.get("out", 0)
            d["d_cr"] += record.get("cr", 0); d["d_cw"] += record.get("cw", 0)
            d["d_reason"] += record.get("reason", 0)
            _add_day_tokens(d, dk, "deepseek_harness", token_total(record))
            name = f"{nice_model(record.get('model', 'deepseek-v4-pro'))} (DeepSeek Harness)"
            model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                              "cr": 0, "cw": 0, "reason": 0,
                                              "tool": "deepseek_harness"})
            model["cost"] += record.get("cost", 0)
            for key in TOKEN_FIELDS:
                model[key] += record.get(key, 0)

    for fp, entry in cache.get("qwencode", {}).items():
        for record in entry.get("records", []):
            if not isinstance(record, dict):
                continue
            dk = record.get("date") or ""
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["qwencode"] += record.get("cost", 0)
            d["q_in"] += record.get("in", 0); d["q_out"] += record.get("out", 0)
            d["q_cr"] += record.get("cr", 0); d["q_reason"] += record.get("reason", 0)
            _add_day_tokens(d, dk, "qwencode", token_total(record))
            for mn, mv in record.get("models", {}).items():
                nm = f"{nice_model(mn)} (Qwen Code)"
                model = models.setdefault(nm, {"cost": 0.0, "in": 0, "out": 0,
                                               "cr": 0, "cw": 0, "reason": 0,
                                               "tool": "qwencode"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for fp, entry in cache.get("kimicode", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["kimicode"] += day.get("cost", 0)
            _add_day_tokens(d, dk, "kimicode", token_total(day))
            for model_name, usage in day.get("models", {}).items():
                name = f"{nice_model(model_name)} (Kimi Code)"
                model = models.setdefault(name, {"cost": 0.0, "in": 0, "out": 0,
                                                  "cr": 0, "cw": 0, "reason": 0,
                                                  "tool": "kimicode"})
                model["cost"] += usage.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += usage.get(key, 0)

    for fp, entry in cache.get("hermes", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["hermes"] += day.get("cost", 0)
            _add_day_tokens(d, dk, "hermes", token_total(day))
            for mn, mv in day.get("models", {}).items():
                name = f"{nice_model(mn)} (Hermes)"
                model = models.setdefault(
                    name, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                           "reason": 0, "tool": "hermes"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for fp, entry in cache.get("openclaw", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            d["openclaw"] += day.get("cost", 0)
            _add_day_tokens(d, dk, "openclaw", token_total(day))
            for mn, mv in day.get("models", {}).items():
                name = f"{nice_model(mn)} (OpenClaw)"
                model = models.setdefault(
                    name, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                           "reason": 0, "tool": "openclaw"})
                model["cost"] += mv.get("cost", 0)
                for key in TOKEN_FIELDS:
                    model[key] += mv.get(key, 0)

    for fp, entry in cache.get("qoder", {}).items():
        model_name = entry.get("model") or "QoderWork"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            input_tokens = day.get("in", 0)
            output_tokens = day.get("out", 0)
            _add_day_tokens(d, dk, "qoderwork", input_tokens + output_tokens)
            name = f"{nice_model(model_name)} (QoderWork)"
            model = models.setdefault(
                name, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0,
                       "reason": 0, "tool": "qoderwork"})
            model["in"] += input_tokens
            model["out"] += output_tokens

    for fp, entry in cache.get("qoder_ide", {}).items():
        model_name = entry.get("model") or "Qoder"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            d = days.setdefault(dk, _empty())
            input_total = day.get("in", 0)
            cached = day.get("cached", 0)
            output = day.get("out", 0)
            _add_day_tokens(d, dk, "qoder_ide", input_total + output)
            nm = f"{nice_model(model_name)} (Qoder)"
            m = models.setdefault(nm, {"cost": 0.0, "in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0, "tool": "qoder"})
            m["in"] += max(input_total - cached, 0)
            m["out"] += output
            m["cr"] += cached

    # --- 持久账本高水位合并: 逐工具逐日取 max ---
    _LEDGER_COST_COLUMNS = frozenset((
        "claude", "codex", "gemini", "grok", "hermes", "openclaw", "zcode",
        "mimocode", "pi", "workbuddy", "workbuddy_ai", "deepseek_harness",
        "opencode", "qwencode"))
    load_ledger = _load_ledger_fn or _resolve_provider("load_ledger")
    ledger_data = load_ledger() or {}
    for tool, tool_days in ledger_data.get("tools", {}).items():
        if not isinstance(tool_days, dict):
            continue
        column = tool if tool in _LEDGER_COST_COLUMNS else None
        for dk, day in tool_days.items():
            if not isinstance(day, dict):
                continue
            if cutoff and dk < cutoff:
                continue
            ledger_tok = _ledger_token_sum(day)
            cost = day.get("cost")
            ledger_cost = (float(cost) if isinstance(cost, (int, float))
                           and not isinstance(cost, bool) else 0.0)
            if ledger_tok <= 0 and ledger_cost <= 0:
                continue
            d = days.setdefault(dk, _empty())
            live_tok = live_tool_tokens.get(dk, {}).get(tool, 0)
            if ledger_tok > live_tok:
                d["tokens"] += ledger_tok - live_tok
            if column and ledger_cost > d[column]:
                d[column] = ledger_cost

    codex_total = sum(d["codex"] for d in days.values())
    codex_in = sum(d["x_in"] for d in days.values())
    codex_out = sum(d["x_out"] for d in days.values())
    codex_reason = sum(d["x_reason"] for d in days.values())
    if codex_total > 0 and not any(v.get("tool") == "codex" for v in models.values()):
        models["GPT-5.5 (Codex)"] = {"cost": round(codex_total, 2), "in": codex_in, "out": codex_out,
                                      "reason": codex_reason, "tool": "codex"}

    _TOOL_COST_KEYS = (
        "claude", "codex", "gemini", "grok", "zcode", "pi",
        "workbuddy", "workbuddy_ai", "deepseek_harness", "opencode",
        "kimicode", "hermes", "cursor", "codebuddy"
    )

    daily = []
    for dk, v in sorted(days.items()):
        tool_costs = {
            t: round(v.get(t, 0.0), 2)
            for t in _TOOL_COST_KEYS
            if round(v.get(t, 0.0), 2) > 0
        }
        total_cost = round(sum(tool_costs.values()), 2)
        daily.append({
            "date": dk,
            "total": total_cost,
            "tokens": v["tokens"],
            "tool_costs": tool_costs,
            "claude": round(v["claude"], 2), "codex": round(v["codex"], 2),
            "gemini": round(v["gemini"], 2), "grok": round(v["grok"], 2),
            "cursor": round(v.get("cursor", 0.0), 2),
            "codebuddy": round(v.get("codebuddy", 0.0), 2),
            "opencode": round(v.get("opencode", 0.0), 2),
            "hermes": round(v["hermes"], 2),
            "openclaw": round(v["openclaw"], 2),
            "zcode": round(v["zcode"], 2), "mimocode": round(v["mimocode"], 2), "pi": round(v["pi"], 2),
            "workbuddy": round(v["workbuddy"], 2),
            "workbuddy_ai": round(v["workbuddy_ai"], 2),
            "deepseek_harness": round(v["deepseek_harness"], 2),
            "qwencode": round(v["qwencode"], 2),
            "kimicode": round(v["kimicode"], 2),
            "prime_agent": round(v["prime_agent"], 2),
            "c_in": v["c_in"], "c_out": v["c_out"], "c_cr": v["c_cr"], "c_cw": v["c_cw"],
            "x_in": v["x_in"], "x_out": v["x_out"], "x_cached": v["x_cached"], "x_reason": v["x_reason"],
            "p_in": v["p_in"], "p_out": v["p_out"], "p_cr": v["p_cr"], "p_cw": v["p_cw"], "p_reason": v["p_reason"],
            "pa_in": v["pa_in"], "pa_out": v["pa_out"], "pa_cr": v["pa_cr"], "pa_cw": v["pa_cw"], "pa_reason": v["pa_reason"],
            "w_in": v["w_in"], "w_out": v["w_out"], "w_cr": v["w_cr"], "w_cw": v["w_cw"],
            "wa_in": v["wa_in"], "wa_out": v["wa_out"], "wa_cr": v["wa_cr"], "wa_cw": v["wa_cw"],
            "d_in": v["d_in"], "d_out": v["d_out"], "d_cr": v["d_cr"],
            "d_cw": v["d_cw"], "d_reason": v["d_reason"],
            "q_in": v["q_in"], "q_out": v["q_out"], "q_cr": v["q_cr"], "q_reason": v["q_reason"],
            "g_in": v["g_in"], "g_out": v["g_out"], "g_cr": v["g_cr"], "g_reason": v["g_reason"],
        })

    def model_tokens(v):
        if v.get("tool") == "codex":
            return v["in"] + v.get("cr", 0) + v["out"]  # out 已含 reasoning
        return v["in"] + v["out"] + v.get("cr", 0) + v.get("cw", 0) + v.get("reason", 0)

    model_list = []
    for n, v in sorted(models.items(), key=lambda kv: (-kv[1]["cost"], -model_tokens(kv[1]))):
        total_tok = model_tokens(v)
        if v["cost"] <= 0 and total_tok <= 0:
            continue
        out_k = v["out"] / 1000 if v["out"] else 0
        cost_per_k = round(v["cost"] / out_k, 3) if out_k > 0 else 0
        out_ratio = round(v["out"] / total_tok * 100, 1) if total_tok > 0 else 0
        price_id, prov = resolve_pricing_entry(n)
        model_list.append({"name": n, "cost": round(v["cost"], 2),
                           "in": v["in"], "out": v["out"], "cr": v.get("cr", 0), "cw": v.get("cw", 0),
                           "reason": v.get("reason", 0), "tokens": total_tok, "tool": v["tool"],
                           "cost_per_k": cost_per_k, "out_ratio": out_ratio,
                           "pricing_provenance": v.get("pricing_provenance") or prov,
                           "pricing_source": v.get("pricing_source") or price_id or n,
                           "cost_kind": v.get("cost_kind") or _COST_KIND_BY_PROVENANCE.get(v.get("pricing_provenance") or prov, "unknown")})

    def account_model_rows(specs):
        aggregated = {}
        for cache_key, tool, suffix in specs:
            for day_key, day in (cache.get(cache_key) or {}).items():
                if cutoff and day_key < cutoff or not isinstance(day, dict):
                    continue
                for raw_name, raw_usage in (day.get("models") or {}).items():
                    if not isinstance(raw_usage, dict):
                        continue
                    display_name = nice_model(raw_name)
                    name = f"{display_name} ({suffix})" if suffix else display_name
                    model = aggregated.setdefault(
                        name,
                        {"name": name, "cost": 0.0, "in": 0, "out": 0,
                         "cr": 0, "cw": 0, "reason": 0, "tokens": 0,
                         "tool": tool})
                    components = {field: _provider_usage_int(raw_usage.get(field))
                                  for field in _PROVIDER_USAGE_FIELDS}
                    model["tokens"] += _provider_usage_int(raw_usage.get("tokens")) \
                        or sum(components.values())
                    for field, value in components.items():
                        model[field] += value
                    cost = _provider_number(raw_usage.get("cost")) or 0.0
                    if cost >= 0:
                        model["cost"] += cost

        rows = []
        for model in sorted(
                aggregated.values(), key=lambda item: (-item["tokens"], item["name"])):
            out_k = model["out"] / 1000 if model["out"] else 0
            rows.append({
                **model,
                "cost": round(model["cost"], 2),
                "cost_per_k": round(model["cost"] / out_k, 3) if out_k > 0 else 0,
                "out_ratio": round(model["out"] / model["tokens"] * 100, 1)
                if model["tokens"] > 0 else 0,
            })
        return rows

    model_list.extend(account_model_rows((
        (_GROK_BOT_PROVIDER_DAYS_CACHE_KEY, "grok_bot", None),
    )))
    provider_model_list = account_model_rows((
        (_CURSOR_PROVIDER_DAYS_CACHE_KEY, "cursor", "Cursor 账号"),
        (_ZAI_PROVIDER_DAYS_CACHE_KEY, "zai", "z.ai 账号"),
    ))

    return {"daily": daily, "models": model_list, "provider_models": provider_model_list}


def daily_costs(period=None, cache=None):
    """输出按天+按模型的成本 JSON(从扫描缓存读,无额外 I/O)。"""
    if period is None:
        period = "all"
        for i, a in enumerate(sys.argv):
            if a == "--period" and i + 1 < len(sys.argv):
                period = sys.argv[i + 1]
                break
    if cache is None:
        load_cache = _resolve_provider("load_dashboard_cache")
        cache = load_cache()
    print(json.dumps(build_daily_costs(period, refresh=False, _cache=cache), ensure_ascii=False))
