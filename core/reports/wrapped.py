import os
import sys
import json
from datetime import datetime, date

from core.config import (
    _GEMINI_DAYS_CACHE_KEY,
    _GROK_DAYS_CACHE_KEY,
    token_total,
)
from core.pricing import nice_model
from core.accounting import (
    _period_cutoff,
    _gemini_token_total,
)
from core.storage import (
    _load_scan_cache,
    _load_dashboard_cache,
    _load_ledger,
    _iter_cached_token_days,
    _ledger_token_sum,
)
from core.collectors.compute import compute
from core.collectors.misc import (
    _iter_deepseek_harness_records,
    _iter_workbuddy_records,
)

def _arg_period(default="all"):
    period = default
    for i, a in enumerate(sys.argv):
        if a == "--period" and i + 1 < len(sys.argv):
            period = sys.argv[i + 1]
            break
    return period


def _streak_info(dates):
    """dates: ISO 日期字符串列表。返回 (最长连续天数, 当前连续天数)。"""
    if not dates:
        return 0, 0
    ds = sorted(date.fromisoformat(x) for x in dates)
    max_run = run = 1
    for i in range(1, len(ds)):
        run = run + 1 if (ds[i] - ds[i - 1]).days == 1 else 1
        if run > max_run:
            max_run = run
    cur = 0
    if (date.today() - ds[-1]).days <= 1:   # 仅当最近活跃日是今/昨天才算"当前连续"
        cur = 1
        for i in range(len(ds) - 1, 0, -1):
            if (ds[i] - ds[i - 1]).days == 1:
                cur += 1
            else:
                break
    return max_run, cur


def build_wrapped(period="all", refresh=True, _cache=None):
    """Tokei 回顾数据。汇总全部工具,不联网。"""
    cutoff = _period_cutoff(period)
    if refresh:
        compute()
    cache = _cache if _cache is not None else _load_scan_cache()

    hours = [0] * 24
    weekday = [0] * 7
    day_tokens = {}
    day_cost = {}
    proj_tok = {}
    day_projs = {}
    model_tok = {}
    all_day_hours = set()

    def add_hours(day_key, values):
        if not isinstance(values, list) or len(values) != 24:
            return
        for hour, amount in enumerate(values):
            amount = int(amount or 0)
            hours[hour] += amount
            if amount:
                all_day_hours.add(f"{day_key}:{hour}")

    # --- Claude (有 hours / proj / models) ---
    fc = cache.get("claude", {})
    for f, entry in fc.items():
        if not isinstance(entry, dict):
            continue
        for day_key, day_hours in entry.get("day_hours", {}).items():
            if not cutoff or day_key >= cutoff:
                add_hours(day_key, day_hours)
        proj_path = entry.get("proj") or ""
        proj = os.path.basename(proj_path.rstrip("/")) or "?"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            pt = proj_tok.setdefault(proj, [0, 0.0])
            pt[0] += tok; pt[1] += day.get("cost", 0)
            day_projs.setdefault(dk, set()).add(proj)
            weekday[date.fromisoformat(dk).weekday()] += tok
            for mn, mv in day.get("models", {}).items():
                nm = nice_model(mn)
                model_tok[nm] = model_tok.get(nm, 0) + token_total(mv)

    # --- Codex (in + out + reason; in 已含 cached。与账本白名单/主页卡片总量同口径) ---
    for f, entry in cache.get("codex", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = day.get("in", 0) + day.get("out", 0) + day.get("reason", 0)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            for hour, amount in enumerate(day.get("hours", [])):
                hours[hour] += amount
                if amount:
                    all_day_hours.add(f"{dk}:{hour}")
            for model, usage in day.get("models", {}).items():
                name = f"{nice_model(model)} (Codex)"
                model_tokens = usage.get("in", 0) + usage.get("cr", 0) + usage.get("out", 0)
                model_tok[name] = model_tok.get(name, 0) + model_tokens

    # --- Gemini (input 含 cached，thoughts 按输出 token 计入) ---
    for dk, day in cache.get(_GEMINI_DAYS_CACHE_KEY, {}).items():
        if cutoff and dk < cutoff:
            continue
        tok = _gemini_token_total(day)
        day_tokens[dk] = day_tokens.get(dk, 0) + tok
        day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
        weekday[date.fromisoformat(dk).weekday()] += tok
        add_hours(dk, day.get("hours"))
        for model, usage in day.get("models", {}).items():
            name = f"{nice_model(model)} (Gemini)"
            amount = _gemini_token_total(usage)
            model_tok[name] = model_tok.get(name, 0) + amount

    # --- Grok Build（unified 日志中的真实 token）---
    for dk, day in cache.get(_GROK_DAYS_CACHE_KEY, {}).items():
        if cutoff and dk < cutoff:
            continue
        tok = token_total(day)
        day_tokens[dk] = day_tokens.get(dk, 0) + tok
        day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
        weekday[date.fromisoformat(dk).weekday()] += tok
        add_hours(dk, day.get("hours"))
        for model, usage in day.get("models", {}).items():
            name = f"{nice_model(model)} (Grok Build)"
            model_tok[name] = model_tok.get(name, 0) + token_total(usage)

    # --- Hermes (in + out + cr + cw + reason) ---
    for f, entry in cache.get("hermes", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            for model, usage in day.get("models", {}).items():
                name = f"{nice_model(model)} (Hermes)"
                model_tok[name] = model_tok.get(name, 0) + token_total(usage)

    # --- OpenClaw (in + out + cr + cw) ---
    for f, entry in cache.get("openclaw", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            for model, usage in day.get("models", {}).items():
                name = f"{nice_model(model)} (OpenClaw)"
                model_tok[name] = model_tok.get(name, 0) + token_total(usage)

    # --- OpenCode (in + out + cr + cw + reason) ---
    for dk, day in _iter_cached_token_days(cache.get("opencode", {})):
        if cutoff and dk < cutoff:
            continue
        tok = token_total(day)
        day_tokens[dk] = day_tokens.get(dk, 0) + tok
        day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
        weekday[date.fromisoformat(dk).weekday()] += tok
        for hour, amount in enumerate(day.get("hours", [])):
            hours[hour] += amount
            if amount:
                all_day_hours.add(f"{dk}:{hour}")
        for model, usage in day.get("models", {}).items():
            name = f"{nice_model(model)} (OpenCode)"
            model_tok[name] = model_tok.get(name, 0) + token_total(usage)

    # --- ZCode / MiMoCode ---
    for tool_key, suffix in (("zcode", "ZCode"), ("mimocode", "MiMoCode")):
        for dk, day in _iter_cached_token_days(cache.get(tool_key, {})):
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            for hour, amount in enumerate(day.get("hours", [])):
                hours[hour] += amount
                if amount:
                    all_day_hours.add(f"{dk}:{hour}")
            for model, usage in day.get("models", {}).items():
                name = f"{nice_model(model)} ({suffix})"
                model_tok[name] = model_tok.get(name, 0) + token_total(usage)

    # --- Qwen Code (in + out + cr + reason) ---
    for entry in cache.get("qwencode", {}).get("entries", []):
        dk = entry.get("date")
        if not dk:
            continue
        if cutoff and dk < cutoff:
            continue
        tok = token_total(entry)
        day_tokens[dk] = day_tokens.get(dk, 0) + tok
        day_cost[dk] = day_cost.get(dk, 0.0) + entry.get("cost", 0)
        weekday[date.fromisoformat(dk).weekday()] += tok
        hour = entry.get("hour")
        if isinstance(hour, int) and 0 <= hour < 24:
            hours[hour] += tok
            all_day_hours.add(f"{dk}:{hour}")
        for mn, mv in entry.get("models", {}).items():
            nm = f"{nice_model(mn)} (Qwen Code)"
            model_tok[nm] = model_tok.get(nm, 0) + token_total(mv)

    # --- Kimi Code (legacy StatusUpdate and protocol 1.5 usage.record) ---
    for _, entry in cache.get("kimicode", {}).items():
        if not isinstance(entry, dict):
            continue
        project_path = entry.get("proj") or ""
        project = os.path.basename(project_path.rstrip("/")) or "Kimi Code"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            pt = proj_tok.setdefault(project, [0, 0.0])
            pt[0] += tok
            day_projs.setdefault(dk, set()).add(project)
            for mn, mv in day.get("models", {}).items():
                model_name = f"{nice_model(mn)} (Kimi Code)"
                model_tok[model_name] = model_tok.get(model_name, 0) + token_total(mv)

    # --- Pi Coding Agent (in + out + cr + cw + reason) ---
    for f, entry in cache.get("pi", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            for mn, mv in day.get("models", {}).items():
                nm = f"{nice_model(mn)} (Pi)"
                model_tok[nm] = model_tok.get(nm, 0) + token_total(mv)

    # --- Prime Agent (Pi-compatible persisted assistant usage) ---
    for f, entry in cache.get("prime_agent", {}).items():
        if not isinstance(entry, dict):
            continue
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = token_total(day)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + day.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            for mn, mv in day.get("models", {}).items():
                nm = f"{nice_model(mn)} (Prime Agent)"
                model_tok[nm] = model_tok.get(nm, 0) + token_total(mv)

    # --- WorkBuddy 国内版与国际版（逐次调用，output 已含 reasoning） ---
    for tool_key, suffix in (("workbuddy", "WorkBuddy"),
                             ("workbuddy_ai", "WorkBuddy Intl.")):
        for _, entry, record in _iter_workbuddy_records(cache.get(tool_key, {})):
            dk = record.get("date", "")
            if not dk or (cutoff and dk < cutoff):
                continue
            tok = token_total(record)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            day_cost[dk] = day_cost.get(dk, 0.0) + record.get("cost", 0)
            weekday[date.fromisoformat(dk).weekday()] += tok
            hour = record.get("hour")
            if isinstance(hour, int) and 0 <= hour < 24:
                hours[hour] += tok
                all_day_hours.add(f"{dk}:{hour}")
            project_path = entry.get("proj") or ""
            project = os.path.basename(project_path.rstrip("/")) or suffix
            pt = proj_tok.setdefault(project, [0, 0.0])
            pt[0] += tok; pt[1] += record.get("cost", 0)
            day_projs.setdefault(dk, set()).add(project)
            model_name = f"{nice_model(record.get('model', 'unknown'))} ({suffix})"
            model_tok[model_name] = model_tok.get(model_name, 0) + tok

    # --- DeepSeek Harness (最终 message 优先，异常中断用 usage chunk) ---
    for _, entry, record in _iter_deepseek_harness_records(cache.get("deepseek_harness", {})):
        dk = record.get("date", "")
        if not dk or (cutoff and dk < cutoff):
            continue
        tok = token_total(record)
        day_tokens[dk] = day_tokens.get(dk, 0) + tok
        day_cost[dk] = day_cost.get(dk, 0.0) + record.get("cost", 0)
        weekday[date.fromisoformat(dk).weekday()] += tok
        hour = record.get("hour")
        if isinstance(hour, int) and 0 <= hour < 24:
            hours[hour] += tok
            all_day_hours.add(f"{dk}:{hour}")
        project_path = entry.get("proj") or ""
        project = os.path.basename(project_path.rstrip("/")) or "DeepSeek Harness"
        pt = proj_tok.setdefault(project, [0, 0.0])
        pt[0] += tok; pt[1] += record.get("cost", 0)
        day_projs.setdefault(dk, set()).add(project)
        model_name = f"{nice_model(record.get('model', 'deepseek-v4-pro'))} (DeepSeek Harness)"
        model_tok[model_name] = model_tok.get(model_name, 0) + tok

    # --- QoderWork (in + out, no cost) ---
    for f, entry in cache.get("qoder", {}).items():
        if not isinstance(entry, dict):
            continue
        model_name = entry.get("model") or "QoderWork"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = day.get("in", 0) + day.get("out", 0)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            name = f"{nice_model(model_name)} (QoderWork)"
            model_tok[name] = model_tok.get(name, 0) + tok

    # --- Qoder IDE (in + out, no cost; cached is subset of in) ---
    for f, entry in cache.get("qoder_ide", {}).items():
        if not isinstance(entry, dict):
            continue
        model_name = entry.get("model") or "Qoder"
        for dk, day in entry.get("days", {}).items():
            if cutoff and dk < cutoff:
                continue
            tok = day.get("in", 0) + day.get("out", 0)
            day_tokens[dk] = day_tokens.get(dk, 0) + tok
            weekday[date.fromisoformat(dk).weekday()] += tok
            add_hours(dk, day.get("hours"))
            nm = f"{nice_model(model_name)} (Qoder)"
            model_tok[nm] = model_tok.get(nm, 0) + tok

    # --- 持久账本合并:全部指标统一账本口径 ---
    # 账本是同一份数据的高水位存档:同一天取 max(账本合计, 实时值),绝不相加以免重复计数
    # (天级整取整用;账本理论上 ≥ 实时,max 只是保险)。token 按白名单口径求和(含 cached/thoughts,
    # 见 _ledger_token_sum),cost 同理逐日取 max。被清理日志的历史天由账本兜底补回,
    # 让 total/active/streak/busiest/peak 与 peak_days 同口径,避免同页口径分裂。
    ledger_day_tokens = {}
    ledger_day_cost = {}
    for tool_days in _load_ledger().get("tools", {}).values():
        if not isinstance(tool_days, dict):
            continue
        for dk, day in tool_days.items():
            if not isinstance(day, dict):
                continue
            if cutoff and dk < cutoff:
                continue
            tok = _ledger_token_sum(day)
            if tok:
                ledger_day_tokens[dk] = ledger_day_tokens.get(dk, 0) + tok
            cost = day.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
                ledger_day_cost[dk] = ledger_day_cost.get(dk, 0.0) + float(cost)
            projects = day.get("projects")
            if isinstance(projects, list):  # 账本存档的项目名:日志被清后"那天在干什么"的记忆
                names = {p for p in projects if isinstance(p, str) and p}
                if names:
                    day_projs.setdefault(dk, set()).update(names)
    for dk, tok in ledger_day_tokens.items():
        day_tokens[dk] = max(day_tokens.get(dk, 0), tok)
    for dk, cost in ledger_day_cost.items():
        day_cost[dk] = max(day_cost.get(dk, 0.0), cost)
    total_tokens = sum(day_tokens.values())
    total_cost = sum(day_cost.values())

    active = sorted(day_tokens.keys())
    streak_max, streak_cur = _streak_info(active)

    # --- 巅峰日 Top 3:直接取合并后的 day_tokens,与 total_tokens/active_days 同一份数据 ---
    peak_days = [
        {"date": dk, "tokens": tok,
         "projects": sorted(day_projs.get(dk) or ())[:3]}  # 账本天的项目名同样能命中
        for dk, tok in sorted(day_tokens.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        if tok > 0
    ]
    # busiest 向后兼容保留,取值 = peak_days[0],保证两者一致;成就计算同样用合并后的冠军值。
    busiest_merged = ({"date": peak_days[0]["date"], "tokens": peak_days[0]["tokens"]}
                      if peak_days else {"date": "", "tokens": 0})
    busiest_tok = busiest_merged["tokens"]
    top_model_name, top_model_tok = (max(model_tok.items(), key=lambda kv: kv[1])
                                     if model_tok else ("-", 0))
    projects = sorted(
        ({"name": p, "tokens": v[0], "cost": round(v[1], 2)} for p, v in proj_tok.items()),
        key=lambda x: -x["tokens"])[:8]
    max_projs_day = max((len(s) for s in day_projs.values()), default=0)
    hours_total = sum(hours)
    night = sum(hours[0:6])
    night_share = round(night / hours_total * 100, 1) if hours_total else 0.0

    ach = []
    def add(icon, title, desc, tint):
        ach.append({"icon": icon, "title": title, "desc": desc, "tint": tint})

    # Token 里程碑(金,取最高档)
    if total_tokens >= 1_000_000_000_000:
        add("crown.fill", "万亿先生", f"{total_tokens/1e12:.2f} 万亿 token", "gold")
    elif total_tokens >= 100_000_000_000:
        add("hexagon.fill", "千亿先生", f"{total_tokens/1e8:.0f} 亿 token", "gold")
    elif total_tokens >= 10_000_000_000:
        add("diamond.fill", "百亿先生", f"{total_tokens/1e8:.0f} 亿 token", "gold")
    elif total_tokens >= 1_000_000_000:
        add("diamond", "十亿先生", f"{total_tokens/1e8:.1f} 亿 token", "gold")

    # 成本里程碑(绿,取最高档)
    if total_cost >= 100000:
        add("dollarsign.circle.fill", "十万刀", f"≈${int(total_cost):,}", "green")
    elif total_cost >= 10000:
        add("banknote.fill", "破万刀", f"≈${int(total_cost):,}", "green")
    elif total_cost >= 1000:
        add("banknote", "破千刀", f"≈${int(total_cost):,}", "green")

    # 连续打卡(火橙,取最高档)
    if streak_max >= 100:
        add("flame.fill", "百日筑基", f"连续 {streak_max} 天", "coral")
    elif streak_max >= 30:
        add("flame.fill", "铁人", f"连续 {streak_max} 天", "coral")
    elif streak_max >= 7:
        add("flame.fill", "坚持", f"连续 {streak_max} 天", "coral")

    # 单日爆发(火橙)
    if busiest_tok >= 1_000_000_000:
        add("bolt.fill", "爆肝日", f"单日 {busiest_tok/1e8:.0f} 亿 token", "coral")

    # 项目维度(青蓝)
    if max_projs_day >= 5:
        add("square.grid.3x3.fill", "多线作战", f"单日 {max_projs_day} 个项目", "blue")
    elif max_projs_day >= 3:
        add("square.grid.2x2.fill", "多面手", f"单日 {max_projs_day} 个项目", "blue")
    claude_tokens = sum(v[0] for v in proj_tok.values())
    top_share = (max(v[0] for v in proj_tok.values()) / claude_tokens * 100) if (proj_tok and claude_tokens) else 0
    if top_share >= 50:
        add("scope", "专一", f"主项目占 {top_share:.0f}%", "blue")
    if len(proj_tok) >= 10:
        add("rectangle.3.group.fill", "广撒网", f"{len(proj_tok)} 个项目", "blue")

    # 作息彩蛋(紫)
    active_hours = sum(1 for h in hours if h > 0)
    if active_hours >= 24:
        add("clock.badge.checkmark.fill", "永动机", "24h 每个时段都有活跃", "purple")
    # Loop 成就: 连续 N 天每天 24h 全时段有 agent 活跃
    day_hour_map = {}
    for item in all_day_hours:
        dk, h = item.rsplit(":", 1)
        day_hour_map.setdefault(dk, set()).add(int(h))
    full_days = sorted(dk for dk, hs in day_hour_map.items() if len(hs) >= 24)
    loop_streak = 0
    if full_days:
        cur = 1
        for i in range(1, len(full_days)):
            if (date.fromisoformat(full_days[i]) - date.fromisoformat(full_days[i - 1])).days == 1:
                cur += 1
            else:
                loop_streak = max(loop_streak, cur)
                cur = 1
        loop_streak = max(loop_streak, cur)
    if loop_streak >= 30:
        add("repeat.circle.fill", "Loop滴神", f"连续 {loop_streak} 天 24/7", "purple")
    elif loop_streak >= 3:
        add("repeat.circle", "Loop Engineering !!", f"连续 {loop_streak} 天 24/7", "purple")
    if night_share >= 5:
        add("moon.stars.fill", "夜猫子", f"{night_share:.0f}% 在凌晨", "purple")
    morning_share = (sum(hours[5:9]) / hours_total * 100) if hours_total else 0
    if morning_share >= 12:
        add("sunrise.fill", "早起鸟", f"{morning_share:.0f}% 在清晨", "purple")
    weekday_total = sum(weekday)
    weekend_share = ((weekday[5] + weekday[6]) / weekday_total * 100) if weekday_total else 0
    if weekend_share >= 30:
        add("beach.umbrella.fill", "周末战士", f"周末占 {weekend_share:.0f}%", "purple")

    # 资历(玫红)
    if len(active) >= 100:
        add("calendar", "元老", f"{len(active)} 天活跃", "pink")

    return {
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 2),
        "active_days": len(active),
        "streak_max": streak_max,
        "streak_cur": streak_cur,
        "busiest": busiest_merged,
        "peak_days": peak_days,
        "day_projects": {dk: sorted(projs)[:3] for dk, projs in day_projs.items() if projs},
        "top_model": {"name": top_model_name, "tokens": top_model_tok},
        "hours": hours,
        "weekday": weekday,
        "projects": projects,
        "max_projs_day": max_projs_day,
        "night_share": night_share,
        "first_day": cutoff if cutoff else (active[0] if active else ""),
        "achievements": ach,
        "period": period,
    }


def wrapped():
    """Tokei 回顾:作息 / 项目 / 连续 / 成就。汇总全部工具,不联网。"""
    cache = _load_dashboard_cache()
    print(json.dumps(build_wrapped(_arg_period(), refresh=False, _cache=cache), ensure_ascii=False))


