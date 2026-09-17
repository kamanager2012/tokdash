import os
from core.config import (
    HOME,
    RANGE_KEYS,
    range_bounds,
    _GEMINI_DAYS_CACHE_KEY,
    _GROK_DAYS_CACHE_KEY,
    _CURSOR_PROVIDER_DAYS_CACHE_KEY,
    _ZAI_PROVIDER_DAYS_CACHE_KEY,
    _GROK_BOT_PROVIDER_DAYS_CACHE_KEY,
    _empty_claude,
    _empty_codex,
    _empty_gemini,
    _empty_grok,
    _empty_qoder,
    _empty_hermes,
    _empty_zcode,
    _empty_pi,
    _empty_workbuddy,
    _empty_grok_bot,
    _empty_deepseek_harness,
    _empty_opencode,
    _empty_kimicode,
    _empty_token_ranges,
)
from core.pricing import (
    _raw_price,
    price_for,
    gemini_price,
    nice_model,
    resolve_pricing_entry,
    _COST_KIND_BY_PROVENANCE,
    _deepseek_official_price,
)
from core.storage import (
    _load_scan_cache,
    _save_scan_cache,
    _safe_scan,
    ledger_flush,
    _cache_dashboard_days,
    _merge_dashboard_days,
    _format_token_models,
)
from core.collectors.claude import (
    scan_claude,
    scan_claude_plan,
)
from core.collectors.codex import (
    scan_codex,
    _codex_quota_values,
    fetch_codex_reset_cards,
)
from core.collectors.gemini import scan_gemini
from core.collectors.grok import (
    scan_grok,
    scan_grok_quota,
)
from core.collectors.quotas import (
    _provider_quota_enabled,
    _provider_usage_from_days,
    scan_provider_quotas,
)
from core.collectors.cursor import (
    scan_cursor,
    fetch_cursor_quota,
)
from core.collectors.qoder import (
    _empty_qoder_ide,
    _empty_qodercli,
    scan_qoder,
    scan_qoder_ide,
    scan_qodercli,
)
from core.collectors.hermes import scan_hermes
from core.collectors.pi import scan_pi
from core.collectors.codebuddy import (
    scan_workbuddy,
    scan_workbuddy_ai,
    scan_codebuddy,
)
from core.collectors.grok_bot import scan_grok_bot
from core.collectors.deepseek import scan_deepseek_harness
from core.collectors.opencode import scan_opencode
from core.collectors.glm import scan_zcode
from core.collectors.kimicode import scan_kimicode

def compute(return_cache=False):
    bounds = range_bounds()
    cache = _load_scan_cache()
    errors = {}
    cc = _safe_scan("claude", lambda: scan_claude(bounds, cache), _empty_claude, errors)
    cx = _safe_scan("codex", lambda: scan_codex(bounds, cache), _empty_codex, errors)
    gm = _safe_scan("gemini", lambda: scan_gemini(bounds, cache), _empty_gemini, errors)
    gk = _safe_scan("grok", lambda: scan_grok(bounds, cache), _empty_grok, errors)
    qd = _safe_scan("qoderwork", lambda: scan_qoder(bounds, cache), _empty_qoder, errors)
    qi = _safe_scan("qoder_ide", lambda: scan_qoder_ide(bounds, cache), _empty_qoder_ide, errors)
    qcli = _safe_scan("qodercli", lambda: scan_qodercli(bounds, cache), _empty_qodercli, errors)
    hm = _safe_scan("hermes", lambda: scan_hermes(bounds, cache), _empty_hermes, errors)
    zc = _safe_scan("zcode", lambda: scan_zcode(bounds, cache), _empty_zcode, errors)
    pi = _safe_scan("pi", lambda: scan_pi(bounds, cache), _empty_pi, errors)
    wb = _safe_scan("workbuddy", lambda: scan_workbuddy(bounds, cache), _empty_workbuddy, errors)
    wbai = _safe_scan("workbuddy_ai", lambda: scan_workbuddy_ai(bounds, cache),
                      _empty_workbuddy, errors)
    grok_bot = _safe_scan("grok_bot", lambda: scan_grok_bot(bounds, cache),
                          _empty_grok_bot, errors)
    dsh = _safe_scan("deepseek_harness", lambda: scan_deepseek_harness(bounds, cache),
                     _empty_deepseek_harness, errors)
    ocode = _safe_scan("opencode", lambda: scan_opencode(bounds, cache), _empty_opencode, errors)
    kimi = _safe_scan("kimicode", lambda: scan_kimicode(bounds, cache), _empty_kimicode, errors)
    cb = _safe_scan("codebuddy", lambda: scan_codebuddy(bounds, cache), _empty_workbuddy, errors)
    cursor_res = _safe_scan("cursor", lambda: scan_cursor(bounds, cache), lambda: {"ranges": _empty_token_ranges(), "model": "Composer 2.5"}, errors)
    
    # 清理已裁剪的小众/实验性扫描器残留缓存，不占用内存与 IO
    for _pruned_key in ("mimocode", "openclaw", "prime_agent", "qwencode"):
        if cache.pop(_pruned_key, None) is not None:
            cache["_dirty"] = True

    _cache_dashboard_days(cache, _GEMINI_DAYS_CACHE_KEY, gm.get("days", {}))
    _cache_dashboard_days(cache, _GROK_DAYS_CACHE_KEY, gk.get("days", {}))
    _save_scan_cache(cache)
    ledger_flush()

    def claude_range(b):
        denom = b["cr"] + b["cw"] + b["in"]
        hit = (b["cr"] / denom * 100) if denom else 0.0
        models = []
        for n, v in sorted(b["models"].items(), key=lambda kv: -kv[1]["cost"]):
            p = price_for(n)
            models.append({"name": nice_model(n), "in": v["in"], "out": v["out"],
                           "cr": v["cr"], "cw": v["cw"], "cost": v["cost"],
                           "pin": p["in"], "pout": p["out"]})
        return {"hit": hit, "in": b["in"], "out": b["out"],
                "cr": b["cr"], "cw": b["cw"], "cost": b["cost"], "models": models,
                "sessions": len(b["sessions"])}

    def codex_range(b):
        hit = (b["cached"] / b["in"] * 100) if b["in"] else 0.0
        return {"hit": hit, "in": b["in"] - b["cached"], "cached": b["cached"],
                "out": b["out"], "reason": b["reason"], "cost": b["cost"],
                "sessions": len(b["sessions"]), "models": _format_token_models(b.get("models", {}))}

    def gemini_range(b):
        # tokens.input 含 cached,展示口径与 Codex 一致:输入=非缓存部分
        hit = (b["cached"] / b["in"] * 100) if b["in"] else 0.0
        models = []
        for n, v in sorted(b["models"].items(), key=lambda kv: -kv[1]["cost"]):
            p = gemini_price(n)
            models.append({"name": nice_model(n), "in": max(v["in"] - v["cached"], 0),
                           "out": v["out"], "cached": v["cached"], "thoughts": v["thoughts"],
                           "cost": v["cost"], "pin": p["in"], "pout": p["out"]})
        return {"hit": hit, "in": max(b["in"] - b["cached"], 0), "out": b["out"],
                "cached": b["cached"], "thoughts": b["thoughts"], "cost": b["cost"],
                "models": models, "sessions": len(b["sessions"])}

    def grok_range(b):
        latency_count = b.get("latency_count", 0)
        ctx_window = b.get("ctx_window", 0)
        ctx_pct = (b.get("ctx_used", 0) / ctx_window * 100) if ctx_window else 0.0
        usage_total = sum(int(b.get(key, 0) or 0) for key in ("in", "out", "cr", "reason"))
        usage_available = b.get("usage_calls", 0) > 0
        input_total = b.get("in", 0) + b.get("cr", 0)
        hit = (b.get("cr", 0) / input_total * 100) if input_total else 0.0
        return {"tokens": usage_total if usage_available else b.get("ctx_used", 0),
                "hit": hit, "in": b.get("in", 0), "out": b.get("out", 0),
                "cr": b.get("cr", 0), "reason": b.get("reason", 0),
                "cost": b.get("cost", 0.0),
                "models": _format_token_models(b.get("models", {}), include_prices=True),
                "usage_available": usage_available,
                "usage_calls": b.get("usage_calls", 0),
                "usage_sessions": len(b.get("usage_sessions", [])),
                "sessions": len(b.get("sessions", [])),
                "turns": b.get("turns", 0), "tools": b.get("tools", 0),
                "duration": b.get("duration", 0), "ctx_used": b.get("ctx_used", 0),
                "ctx_window": ctx_window, "ctx": ctx_pct,
                "errors": b.get("errors", 0), "cancellations": b.get("cancellations", 0),
                "ttft": int(b.get("ttft_sum", 0) / latency_count) if latency_count else 0,
                "response": int(b.get("response_sum", 0) / latency_count) if latency_count else 0}

    def qoderwork_range(b):
        ctx_count = b.get("ctx_count", 0)
        ctx = (b.get("ctx_sum", 0.0) / ctx_count * 100) if ctx_count else 0.0
        return {"in": b.get("in", 0), "out": b.get("out", 0),
                "sessions": b.get("sessions", 0), "calls": b.get("calls", 0),
                "sub_agents": b.get("sub_agents", 0),
                "turns": b.get("turns", 0),
                "duration": b.get("duration", 0), "ctx": ctx}

    def qoder_range(b):
        total_in = b.get("in", 0)
        cached = b.get("cached", 0)
        out = b.get("out", 0)
        uncached = max(total_in - cached, 0)
        ctx = (cached / total_in * 100) if total_in else 0.0
        p = _raw_price("qmodel_38max")
        cost = (uncached / 1e6 * p["in"]) + (cached / 1e6 * p["cache_read"]) + (out / 1e6 * p["out"])
        models = [{"name": "Qwen 3.8 Max", "in": uncached, "out": out, "cached": cached, "cr": cached, "cost": cost}] if total_in or out else []
        return {"in": uncached, "out": out, "cached": cached, "cr": cached, "cost": cost,
                "sessions": b.get("sessions", 0), "sub_agents": b.get("sub_agents", 0),
                "calls": b.get("calls", 0), "messages": b.get("messages", 0),
                "hit": ctx, "duration": b.get("duration", 0), "models": models}

    cranges = {k: claude_range(cc["ranges"][k]) for k in RANGE_KEYS}
    xranges = {k: codex_range(cx["ranges"][k]) for k in RANGE_KEYS}
    granges = {k: gemini_range(gm["ranges"][k]) for k in RANGE_KEYS}
    kranges = {k: grok_range(gk["ranges"][k]) for k in RANGE_KEYS}
    qwranges = {k: qoderwork_range(qd["ranges"][k]) for k in RANGE_KEYS}
    qranges = {k: qoder_range(qi["ranges"][k]) for k in RANGE_KEYS}

    def qodercli_range(b):
        r = qoderwork_range(b)
        r["tools"] = b.get("tools", 0)
        r["est"] = int(b.get("est", 0))
        return r

    qcliranges = {k: qodercli_range(qcli["ranges"][k]) for k in RANGE_KEYS}

    def hermes_range(b):
        denom = b["cr"] + b["cw"] + b["in"]
        hit = (b["cr"] / denom * 100) if denom else 0.0
        return {"hit": hit, "in": b["in"], "out": b["out"], "cr": b["cr"], "cw": b["cw"],
                "reason": b["reason"], "cost": b["cost"], "sessions": b["sessions"],
                "models": _format_token_models(b["models"])}

    hranges = {k: hermes_range(hm["ranges"][k]) for k in RANGE_KEYS}

    def token_usage_range(b):
        denom = b["cr"] + b["cw"] + b["in"]
        hit = (b["cr"] / denom * 100) if denom else 0.0
        return {"hit": hit, "in": b["in"], "out": b["out"], "cr": b["cr"], "cw": b["cw"],
                "reason": b["reason"], "cost": b["cost"], "sessions": len(b["sessions"]),
                "models": _format_token_models(b["models"])}

    piranges = {k: token_usage_range(pi["ranges"][k]) for k in RANGE_KEYS}
    zcranges = {k: token_usage_range(zc["ranges"][k]) for k in RANGE_KEYS}
    wbranges = {k: token_usage_range(wb["ranges"][k]) for k in RANGE_KEYS}
    wbairanges = {k: token_usage_range(wbai["ranges"][k]) for k in RANGE_KEYS}
    grok_bot_ranges = {
        key: {
            "in": 0, "out": 0,
            "sessions": len(grok_bot["ranges"][key].get("sessions", [])),
            "calls": grok_bot["ranges"][key].get("calls", 0),
            "turns": grok_bot["ranges"][key].get("turns", 0),
            "tools": grok_bot["ranges"][key].get("tools", 0),
            "duration": grok_bot["ranges"][key].get("duration", 0),
        }
        for key in RANGE_KEYS
    }
    dshranges = {k: token_usage_range(dsh["ranges"][k]) for k in RANGE_KEYS}
    ocranges = {k: token_usage_range(ocode["ranges"][k]) for k in RANGE_KEYS}
    kimiranges = {k: token_usage_range(kimi["ranges"][k]) for k in RANGE_KEYS}
    cbranges = {k: token_usage_range(cb["ranges"][k]) for k in RANGE_KEYS}
    cursor_ranges = {k: token_usage_range(cursor_res["ranges"][k]) for k in RANGE_KEYS}

    cur = cc["cur"]
    cur_total = cur["in"] + cur["out"] + cur["cr"] + cur["cw"]

    quota = _codex_quota_values(cx["limits"], consumed=cx.get("limits_consumed"))
    p5, pw = quota["p5"], quota["pw"]
    r5, rw = quota["r5"], quota["rw"]

    plan = _safe_scan("claude_plan", scan_claude_plan, lambda: {}, errors) or {}
    grok_quota = _safe_scan("grok_quota", scan_grok_quota, lambda: {}, errors) or {}
    provider_quotas = scan_provider_quotas(errors)
    _cache_dashboard_days(
        cache, _CURSOR_PROVIDER_DAYS_CACHE_KEY,
        ((provider_quotas.get("cursor") or {}).get("usage") or {}).get("days", {}))
    _cache_dashboard_days(
        cache, _ZAI_PROVIDER_DAYS_CACHE_KEY,
        ((provider_quotas.get("zai") or {}).get("usage") or {}).get("days", {}))
    _merge_dashboard_days(
        cache, _GROK_BOT_PROVIDER_DAYS_CACHE_KEY,
        ((provider_quotas.get("grok_bot") or {}).get("usage") or {}).get("days", {}))
    grok_bot_days = cache.get(_GROK_BOT_PROVIDER_DAYS_CACHE_KEY)
    if isinstance(grok_bot_days, dict) and grok_bot_days:
        grok_bot_quota = dict(provider_quotas.get("grok_bot") or {})
        if not grok_bot_quota:
            grok_bot_quota = {
                "available": False, "plan": None, "account": None,
                "windows": [], "details": [], "source": "cache",
                "updated": None, "stale": True,
            }
        grok_bot_quota["usage"] = _provider_usage_from_days(grok_bot_days)
        provider_quotas["grok_bot"] = grok_bot_quota
    _save_scan_cache(cache)
    codex_reset_cards = _safe_scan(
        "codex_reset_cards", fetch_codex_reset_cards, lambda: {}, errors) or {}

    result = {
        "claude": {
            "ranges": cranges,
            "session_name": cur["name"], "session_total": cur_total,
            "q5": plan.get("q5"), "q5_reset": plan.get("q5_reset"),
            "q7": plan.get("q7"), "q7_reset": plan.get("q7_reset"),
            "qf": plan.get("qf"), "qf_reset": plan.get("qf_reset"),
            "q_updated": plan.get("q_updated"),
            "q5_stale": plan.get("q5_stale"), "q7_stale": plan.get("q7_stale"),
            "qf_stale": plan.get("qf_stale"),
        },
        "codex": {
            "ranges": xranges,
            "p5": p5, "pw": pw, "r5": r5, "rw": rw,
            "q_updated": cx.get("limits_updated"),
            "p5_stale": quota["p5_stale"], "pw_stale": quota["pw_stale"],
            "plan": cx["plan"],
            "reset_cards": codex_reset_cards if codex_reset_cards.get("count", 0) > 0 else None,
        },
        "gemini": {
            "ranges": granges,
        },
        "antigravity": provider_quotas["antigravity"],
        "zai": provider_quotas["zai"],
        "grok": {
            "ranges": kranges,
            "model": gk["model"],
            "pct": grok_quota.get("pct"),
            "reset": grok_quota.get("reset"),
            "plan": grok_quota.get("plan"),
            "products": grok_quota.get("products") or [],
            "window": grok_quota.get("window"),
            "source": grok_quota.get("source"),
            "q_updated": grok_quota.get("updated"),
            "stale": grok_quota.get("stale"),
        },
        "grok_bot": {
            "ranges": grok_bot_ranges,
            "quota": provider_quotas["grok_bot"],
        },
        "qoderwork": {
            "ranges": qwranges,
            "model": qd.get("model"),
        },
        "qoder": {
            "ranges": qranges,
            "model": qi.get("model"),
        },
        "qodercli": {
            "ranges": qcliranges,
            "model": qcli.get("model"),
        },
        "hermes": {
            "ranges": hranges,
        },
        "zcode": {
            "ranges": zcranges,
        },
        "pi": {
            "ranges": piranges,
        },
        "workbuddy": {
            "ranges": wbranges,
        },
        "workbuddy_ai": {
            "ranges": wbairanges,
        },
        "deepseek_harness": {
            "ranges": dshranges,
        },
        "opencode": {
            "ranges": ocranges,
        },
        "kimicode": {
            "ranges": kimiranges,
        },
        "codebuddy": {
            "ranges": cbranges,
        },
        "cursor": {
            "ranges": cursor_ranges,
            "model": cursor_res.get("model", "Composer 2.5"),
            "quota": provider_quotas.get("cursor") or {},
            "estimated": True,
            "provenance": "heuristic_char_div_4",
        },
    }
    cq = provider_quotas.get("cursor")
    if cq and isinstance(cq, dict) and cq.get("available"):
        result["cursor_quota"] = cq
    if errors:
        result["_errors"] = errors
    _recalc_costs(result)
    if return_cache:
        return result, cache
    return result


def fetch_cursor_official_quota():
    """Legacy alias: redirected to TTL-cached fetch_cursor_quota to prevent high-frequency API spam."""
    return fetch_cursor_quota() if _provider_quota_enabled("cursor") else None


def _recalc_costs(result):
    """只重算缺少权威账单的工具；已有日志成本的工具保留原值。"""
    for tool_key in ("gemini", "grok", "hermes", "zcode", "workbuddy",
                     "workbuddy_ai",
                     "deepseek_harness", "kimicode", "codebuddy", "cursor"):
        tool = result.get(tool_key)
        if not tool or "ranges" not in tool:
            continue
        ranges = tool["ranges"]
        for rk in RANGE_KEYS:
            r = ranges.get(rk)
            if not r or "models" not in r:
                continue
            total_cost = 0.0
            for m in r["models"]:
                name = m.get("name", "")
                model_id = m.get("model_id")
                target_model = model_id if (isinstance(model_id, str) and model_id.strip()) else name
                price_id, prov = resolve_pricing_entry(target_model)

                authoritative_cost = float(m.get("cost", 0) or 0)
                if tool_key == "hermes" and authoritative_cost:
                    total_cost += authoritative_cost
                    m["pricing_provenance"] = "authoritative"
                    m["pricing_source"] = "hermes_local_ledger"
                    m["cost_kind"] = "authoritative_log"
                    if price_id:
                        price = _raw_price(price_id)
                        m["pin"] = price["in"]
                        m["pout"] = price["out"]
                    continue

                m["pricing_provenance"] = prov
                m["pricing_source"] = price_id or target_model
                m["cost_kind"] = _COST_KIND_BY_PROVENANCE.get(prov, "unknown")

                if not price_id or prov == "unknown":
                    total_cost += authoritative_cost
                    m["pin"] = 0
                    m["pout"] = 0
                    continue

                p = _raw_price(price_id)
                ti = m.get("in", 0)
                to = m.get("out", 0)
                if tool_key == "gemini":
                    cached = m.get("cached", 0)
                    thoughts = m.get("thoughts", 0)
                    cost = (ti / 1e6 * p["in"] + (to + thoughts) / 1e6 * p["out"]
                            + cached / 1e6 * p["cache_read"])
                elif tool_key == "deepseek_harness":
                    cr = m.get("cr", 0)
                    cw = m.get("cw", 0)
                    reason = m.get("reason", 0)
                    p = _deepseek_official_price(name) or p
                    cost = (ti / 1e6 * p["in"] + (to + reason) / 1e6 * p["out"]
                            + cr / 1e6 * p["cache_read"] + cw / 1e6 * p["cache_write"])
                elif tool_key in ("hermes", "zcode", "mimocode"):
                    cr = m.get("cr", 0)
                    cw = m.get("cw", 0)
                    reason = m.get("reason", 0)
                    cost = (ti / 1e6 * p["in"] + (to + reason) / 1e6 * p["out"]
                            + cr / 1e6 * p["cache_read"] + cw / 1e6 * p["cache_write"])
                elif tool_key in ("grok", "qwencode"):
                    cr = m.get("cr", 0)
                    reason = m.get("reason", 0)
                    cost = (ti / 1e6 * p["in"] + (to + reason) / 1e6 * p["out"]
                            + cr / 1e6 * p["cache_read"])
                else:
                    cr = m.get("cr", 0)
                    cw = m.get("cw", 0)
                    cost = ti / 1e6 * p["in"] + to / 1e6 * p["out"] + cr / 1e6 * p["cache_read"] + cw / 1e6 * p["cache_write"]
                m["cost"] = round(cost, 6)
                m["pin"] = p["in"]
                m["pout"] = p["out"]
                total_cost += cost
            r["cost"] = round(total_cost, 6)


