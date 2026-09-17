import os
import sys
import json
import tempfile as _tempfile
from datetime import datetime, date, timedelta

from core.config import (
    HOME,
    PRICING_FILE,
    _load_json,
    parse_ts,
)
from core.concurrency import (
    get_canonical_snapshot,
    register_snapshot_generator,
)
from core.storage import (
    _load_scan_cache,
    _load_ledger,
    _load_dashboard_cache,
)
from core.accounting import (
    build_daily_costs,
    daily_costs,
    get_projects,
    projects,
    register_daily_cost_providers,
    register_projects_providers,
)
from core.collectors import (
    compute,
    _claude_event_total,
    _dedupe_claude_events,
    _iso_to_epoch,
    _iter_codex_cached_events,
)
from core.reports.wrapped import (
    _arg_period,
    build_wrapped,
    wrapped,
)
from core.sync import (
    _load_tokei_config,
    _sync_snapshot_filename,
)

def _load_dashboard_cache():
    """复用主刷新生成的扫描缓存；首次运行或缓存损坏时才补一次扫描。"""
    cache = _load_scan_cache()
    if cache.get("_dirty"):
        compute()
        cache = _load_scan_cache()
    return cache


# ---------- 周额度消耗 ----------
# 一个周期 = 两次「余量 100%」之间,起点取 resets_at - 7天。
# 实测:resets_at 会不定期重锚(观测到的间隔有 0.75 / 2.1 / 7.0 天),所以历史边界
# 推不出来,只能观测一次记一次 —— 见 _QUOTA_ANCHOR_FILE。
# 周期边界落在半天,日级账本切不出来,所以整日部分取账本(权威,不受 CLI 清理旧日志
# 影响),首尾半天取更细的来源:本机用带时间戳的事件缓存,peer 用日条目里的 hours[24]。
_QUOTA_CYCLE_HISTORY = 8
_QUOTA_WEEK_HOURS = 7 * 24
_QUOTA_SELF_DEVICE = "本机"
_QUOTA_ANCHOR_FILE = os.path.join(HOME, ".tokei", "quota_cycles.json")
# resets_in_seconds 是整秒截断的,同一个锚点读出来会有几秒抖动。
_QUOTA_ANCHOR_JITTER = 120
_QUOTA_CYCLE_MIN_USED_PCT = 2
# 有周额度窗口的三个工具 → 日表里的短键。
_QUOTA_TOOLS = (("claude", "c"), ("codex", "x"), ("grok", "g"))


def _quota_local_day_range(day_key):
    base = datetime.strptime(day_key, "%Y-%m-%d")
    start = base.astimezone()
    end = (base + timedelta(days=1)).astimezone()
    return int(start.timestamp()), int(end.timestamp())


def _quota_device_ledgers():
    """→ ([(设备名, 账本 tools, 快照, 日表)], [peer 锚点表]) —— 本机 + 各 peer;本机快照为 None。

    额度% 是账号级的,只算本机会对不上(活儿可能全在另一台机器上干的);
    额度读数本身也可能只有另一台机器有(比如 Grok 只在 Air 上登录)。
    """
    own_tools = (_load_ledger() or {}).get("tools") or {}
    devices = [(_QUOTA_SELF_DEVICE, own_tools, None, _quota_daily_from_tools(own_tools))]
    peer_anchors = []
    cfg = _load_tokei_config() or {}
    sync_dir = (os.path.expanduser(cfg.get("sync_dir") or "")
                or os.path.join(HOME, ".tokei", "sync"))
    own = _sync_snapshot_filename(cfg.get("device_id", "")) or ""
    try:
        names = sorted(os.listdir(sync_dir))
    except OSError:
        return devices, peer_anchors
    for name in names:
        # 自己那份快照是本地账本的副本,再算一遍就是双倍。
        if not name.endswith(".json") or name.casefold() == own.casefold():
            continue
        try:
            with open(os.path.join(sync_dir, name), encoding="utf-8") as f:
                snapshot = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        # 锚点不看 _ledger:周期历史推不出来,哪台机器观测到的都得收下。
        incoming = snapshot.get("_quota_anchors")
        if isinstance(incoming, dict):
            peer_anchors.append(incoming)
        tools = (snapshot.get("_ledger") or {}).get("tools")
        if isinstance(tools, dict) and tools:
            devices.append((snapshot.get("_device") or name[:-5], tools, snapshot,
                            _quota_daily_from_tools(tools)))
    return devices, peer_anchors


def _quota_day_tokens(tool, entry):
    """一天的 token 数。三个工具口径不同,别混。"""
    if not isinstance(entry, dict):
        return 0
    if tool == "claude":
        return sum(int(entry.get(k, 0) or 0) for k in ("in", "out", "cr", "cw"))
    if tool == "codex":
        # 账本里的 codex "in" 已含 cached(与 ranges 相反,那边 in 是未缓存部分),
        # 再加 cached 会翻倍。
        return sum(int(entry.get(k, 0) or 0) for k in ("in", "out"))
    if entry.get("tokens") is not None:
        return int(entry["tokens"] or 0)
    return sum(int(entry.get(k, 0) or 0) for k in ("in", "out", "cr", "reason"))


def _quota_daily_from_tools(tools):
    """账本日表 → {日: {"c": …, "x": …, "g": …}}。"""
    out = {}
    for tool, key in _QUOTA_TOOLS:
        for day, entry in (tools.get(tool) or {}).items():
            if isinstance(entry, dict):
                out.setdefault(day, {k: 0 for _t, k in _QUOTA_TOOLS})[key] = \
                    _quota_day_tokens(tool, entry)
    return out


def _quota_window_days(start, end):
    """[start, end) 覆盖的本地自然日 → (完整落在窗口内的, 只覆盖一部分的)。"""
    interior, boundary = set(), []
    cursor = datetime.fromtimestamp(start).date()
    last = datetime.fromtimestamp(max(end - 1, start)).date()
    while cursor <= last:
        key = cursor.isoformat()
        day_start, day_end = _quota_local_day_range(key)
        if day_start >= start and day_end <= end:
            interior.add(key)
        else:
            boundary.append(key)
        cursor += timedelta(days=1)
    return interior, boundary


def _quota_day_hour_bounds(day_key, start, end):
    """该自然日与 [start, end) 相交的小时下标 [lo, hi);无交集返回 None。"""
    day_start, day_end = _quota_local_day_range(day_key)
    lo, hi = max(start, day_start), min(end, day_end)
    if lo >= hi:
        return None
    lo_hour = 0 if lo <= day_start else datetime.fromtimestamp(lo).hour
    if hi >= day_end:
        hi_hour = 24
    else:
        moment = datetime.fromtimestamp(hi)
        hi_hour = moment.hour + (1 if moment.minute or moment.second else 0)
    return lo_hour, max(hi_hour, lo_hour + 1)


def _quota_claude_events():
    """去重后的 Claude 事件 → [(epoch, 本地日, tokens)]。去重逻辑与 scan_claude 一致。"""
    file_cache = (_load_scan_cache() or {}).get("claude") or {}
    all_events = []
    for path, entry in file_cache.items():
        if isinstance(entry, dict):
            for event in entry.get("events", []):
                all_events.append((path, event))
    events = []
    for _path, event in _dedupe_claude_events(all_events):
        dt = parse_ts(event.get("timestamp", ""))
        if dt is None:
            continue
        dt = dt.astimezone()
        events.append((int(dt.timestamp()), dt.date().isoformat(),
                       _claude_event_total(event)))
    return events


def _quota_codex_events(spans):
    """只读与 spans 有交集的事件文件。行内 idx6 已含 cached,故 tokens = idx6 + idx8。

    续接会话会把父会话的事件整段重放,口径必须和账本一致(见 :1862):只认 canonical
    文件,并跳过开头 drop_count 行重放,否则重的日子能比账本多出几十倍。
    """
    if not spans:
        return []
    lo_min = min(lo for lo, _ in spans)
    hi_max = max(hi for _, hi in spans)
    file_cache = (_load_scan_cache() or {}).get("codex") or {}
    events = []
    for path, entry in file_cache.items():
        if not isinstance(entry, dict) or not entry.get("event_count"):
            continue
        if not entry.get("canonical"):
            continue
        first = _iso_to_epoch(entry.get("first_event_ts"))
        last = _iso_to_epoch(entry.get("last_event_ts"))
        if first is not None and first > hi_max:
            continue
        if last is not None and last < lo_min:
            continue
        try:
            for row in _iter_codex_cached_events(
                    path, start_index=int(entry.get("drop_count", 0) or 0)):
                if len(row) < 9:
                    continue
                dt = parse_ts(row[0])
                if dt is None:
                    continue
                events.append((int(dt.timestamp()), str(row[1]),
                               int(row[6] or 0) + int(row[8] or 0)))
        except (OSError, ValueError):
            continue
    return events


def _quota_peer_boundary(tools, tool, day_key, bounds, day_total):
    """没有事件缓存时:有 hours[24] 就按小时切,没有就按覆盖小时数折算。"""
    lo_hour, hi_hour = bounds
    entry = (tools.get(tool) or {}).get(day_key)
    hours = entry.get("hours") if isinstance(entry, dict) else None
    if isinstance(hours, list) and len(hours) >= 24:
        return sum(int(hours[h] or 0) for h in range(lo_hour, hi_hour)), False
    return day_total * (hi_hour - lo_hour) // 24, True


def _quota_window_tokens(devices, tool, key, start, end, self_events):
    """→ (合计, {设备: token}, 是否含折算值)。self_events 为 None 时本机也走 hours。"""
    interior, boundary = _quota_window_days(start, end)
    self_by_day = {}
    for ts, day, amount in self_events or ():
        if start <= ts < end and day not in interior:
            self_by_day[day] = self_by_day.get(day, 0) + amount

    per_device = {}
    approx = False
    for name, tools, _snapshot, daily in devices:
        own = name == _QUOTA_SELF_DEVICE and self_events is not None
        total = sum(int((daily.get(day) or {}).get(key, 0)) for day in interior)
        for day in boundary:
            bounds = _quota_day_hour_bounds(day, start, end)
            if bounds is None:
                continue
            day_total = int((daily.get(day) or {}).get(key, 0))
            # 事件有就用事件(最准);事件空但账本当天有量 = 日志被清理过,退回折算。
            if own and (self_by_day.get(day) or not day_total):
                total += self_by_day.get(day, 0)
                continue
            amount, guessed = _quota_peer_boundary(tools, tool, day, bounds, day_total)
            total += amount
            approx = approx or (guessed and amount > 0)
        per_device[name] = total
    return sum(per_device.values()), per_device, approx


def _quota_tool_reading(source, tool):
    """从一份 payload/同步快照里取 (used_pct, reset_epoch, 读数时间);取不到返回 None。"""
    data = (source or {}).get(tool) or {}
    if tool == "claude":
        if data.get("q7_stale"):
            return None
        used, reset, updated = data.get("q7"), data.get("q7_reset"), data.get("q_updated")
    elif tool == "codex":
        if data.get("pw_stale"):
            return None
        used, reset, updated = data.get("pw"), data.get("rw"), data.get("q_updated")
    else:
        # Grok 也可能是月套餐,月窗口长度不定又没有数据可验证,先只认周。
        if data.get("stale") or data.get("window") != "week":
            return None
        used, reset, updated = data.get("pct"), data.get("reset"), data.get("q_updated")
    if not isinstance(reset, (int, float)):
        reset = _iso_to_epoch(reset)
    if not isinstance(reset, (int, float)) or reset <= 0:
        return None
    return used, int(reset), int(updated or 0)


def _load_quota_anchors():
    try:
        with open(_QUOTA_ANCHOR_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    anchors = data.get("anchors") if isinstance(data, dict) else None
    return anchors if isinstance(anchors, dict) else {}


def _save_quota_anchors(anchors):
    directory = os.path.dirname(_QUOTA_ANCHOR_FILE)
    tmp = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = _tempfile.mkstemp(prefix=".tokei-cycles-", suffix=".json", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"anchors": anchors}, f, ensure_ascii=False)
        os.replace(tmp, _QUOTA_ANCHOR_FILE)
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _record_quota_anchor(anchors, tool, reset, used, now, confirmed_reset=False):
    """记下一次额度读数。同一个锚点只留一条,used 取见过的最大值。

    额度从有消耗的旧窗口回到 0%,同时 reset 前移到新窗口,已经足以确认提前重置。
    后续空闲时 reset 继续漂移不会连开新周期:只有紧邻的上一锚点有真实消耗时
    才确认这次 0% 重置。
    """
    used = float(used or 0)
    rows = anchors.setdefault(tool, [])
    previous = [
        row for row in rows
        if int(row.get("reset", 0)) < reset - _QUOTA_ANCHOR_JITTER
    ]
    latest_previous = max(previous, key=lambda row: int(row.get("reset", 0)), default=None)
    confirmed_reset = bool(
        confirmed_reset or
        used >= _QUOTA_CYCLE_MIN_USED_PCT or
        (latest_previous and
         latest_previous.get("max_used", 0) >= _QUOTA_CYCLE_MIN_USED_PCT)
    )
    for row in rows:
        if abs(int(row.get("reset", 0)) - reset) <= _QUOTA_ANCHOR_JITTER:
            changed = (
                used > row.get("max_used", 0) or
                now > row.get("last_seen", 0) or
                (confirmed_reset and not row.get("confirmed_reset"))
            )
            row["max_used"] = max(row.get("max_used", 0), used)
            row["last_seen"] = max(row.get("last_seen", 0), now)
            if confirmed_reset:
                row["confirmed_reset"] = True
            return changed
    row = {"reset": reset, "first_seen": now, "last_seen": now, "max_used": used}
    if confirmed_reset:
        row["confirmed_reset"] = True
    rows.append(row)
    return True


def _merge_quota_anchors(anchors, incoming):
    """把 peer 快照里的锚点并进内存表 —— 只为渲染,不回写自己的账。

    周期边界只有亲眼观测到的那台机器知道,所以谁看到都算数。抖动对齐交给
    _record_quota_anchor:同一个窗口在两台机器上读出的 reset 差几秒也能并成一条。
    """
    known = {tool for tool, _key in _QUOTA_TOOLS}
    for tool, rows in (incoming or {}).items():
        if tool not in known or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            # 快照是别的进程写的,字段可能是任何东西 —— 挑不出数的直接跳过。
            reset, used, seen = row.get("reset"), row.get("max_used"), row.get("last_seen")
            if not isinstance(reset, (int, float)) or reset <= 0:
                continue
            _record_quota_anchor(
                anchors, tool, int(reset),
                used if isinstance(used, (int, float)) else 0,
                int(seen) if isinstance(seen, (int, float)) else 0,
                confirmed_reset=row.get("confirmed_reset") is True)
    return anchors


def _quota_anchor_cycles(anchors, tool, span, limit, now):
    """→ [(start, end, max_used, 是否进行中)]，最新的排最前。

    按 reset 排序而不是按观测顺序:陈旧的会话记录偶尔会抢赢新记录,
    照观测顺序切会切出负时长的区间。排序后每段时长天然为正。
    """
    rows = sorted(anchors.get(tool) or [], key=lambda r: int(r.get("reset", 0)))
    # 窗口空着的时候 reset 会一直跟着 now+7d 漂,那不是真周期。真实消耗和
    # 「旧窗口有消耗后回到 0%」两种证据都能确认周期;没有证据时只展示最新读数。
    rows = [
        row for row in rows
        if row.get("max_used", 0) >= _QUOTA_CYCLE_MIN_USED_PCT or
        row.get("confirmed_reset") is True
    ] or rows[-1:]

    cycles = []
    for index, row in enumerate(rows):
        reset = int(row["reset"])
        start = reset - span
        if index + 1 < len(rows):
            end = min(reset, int(rows[index + 1]["reset"]) - span)
        else:
            end = reset
        if end > start:
            # 读数断了就不会再有新锚点,最后一条也可能早已过期 —— 那是历史,不是进行中。
            # 但「进行中」仍只能是最后一条:多标一条会被前端当成当前卡片,另一条就没了。
            current = index + 1 == len(rows) and reset > now
            cycles.append((start, end, row.get("max_used", 0), current))
    return cycles[-limit:][::-1]


def _quota_cycle_specs(payload, devices, peer_anchors, now):
    """→ (能切出周期的工具, 一条锚点都没有的工具, 锚点表)，顺便把这次读到的锚点落盘。

    额度是账号级的:本机读不到就用同步过来的(Grok 只在 Air 上登录就属于这种),
    所以每台设备的读数都记 —— 谁先看到重锚都算数。

    落盘只写本机这轮亲眼读到的;peer 锚点在落盘之后才并进来,免得把别人的记录
    反复回写成自己的观测。
    """
    anchors = _load_quota_anchors()
    dirty = False
    for tool, _key in _QUOTA_TOOLS:
        for _name, _tools, snapshot, _daily in devices:
            reading = _quota_tool_reading(snapshot if snapshot else payload, tool)
            if reading:
                dirty |= _record_quota_anchor(anchors, tool, reading[1], reading[0], now)
    if dirty:
        _save_quota_anchors(anchors)
    for incoming in peer_anchors:
        _merge_quota_anchors(anchors, incoming)
    # 当前读数断了不等于历史没了 —— 有锚点就照旧切周期,
    # 只有一条都没有才算真没有,那才需要提示怎么把额度读数找回来。
    charted = [tool for tool, _key in _QUOTA_TOOLS if anchors.get(tool)]
    missing = [tool for tool, _key in _QUOTA_TOOLS if not anchors.get(tool)]
    return charted, missing, anchors


def build_quota_detail():
    payload = compute()
    now = int(datetime.now().timestamp())
    devices, peer_anchors = _quota_device_ledgers()
    span = _QUOTA_WEEK_HOURS * 3600
    charted, missing, anchors = _quota_cycle_specs(payload, devices, peer_anchors, now)

    planned = []
    for tool in charted:
        for start, end, used, current in _quota_anchor_cycles(
                anchors, tool, span, _QUOTA_CYCLE_HISTORY, now):
            planned.append((tool, start, end, used, current))

    codex_spans = [(s, e) for tool, s, e, _u, _c in planned if tool == "codex"]
    keys = dict(_QUOTA_TOOLS)
    events = {}
    cycles = []
    for tool, start, end, used, current in planned:
        if tool not in events:
            # Grok 没有带时间戳的事件缓存,只能靠账本的 hours。
            events[tool] = (_quota_claude_events() if tool == "claude"
                            else _quota_codex_events(codex_spans) if tool == "codex"
                            else None)
        tokens, per_device, approx = _quota_window_tokens(
            devices, tool, keys[tool], start, end, events[tool])
        cycles.append({
            "tool": tool,
            "start": start,
            "end": end,
            "used_pct": used,
            "tokens": tokens,
            "devices": per_device,
            "approx": approx,
            "current": current,
        })

    merged = {}
    for _name, _tools, _snapshot, daily in devices:
        for day, value in daily.items():
            agg = merged.setdefault(day, {key: 0 for _t, key in _QUOTA_TOOLS})
            for _tool, key in _QUOTA_TOOLS:
                agg[key] += value.get(key, 0)

    return {
        "daily": [dict(d=day, **value) for day, value in sorted(merged.items())],
        "cycles": cycles,
        "devices": [name for name, _tools, _snapshot, _daily in devices],
        "missing": missing,
        "now": now,
    }


def quota_detail():
    print(json.dumps(build_quota_detail(), ensure_ascii=False))


def build_dashboard(period="all"):
    cache = _load_dashboard_cache()
    result = build_daily_costs(period, refresh=False, _cache=cache)
    result["wrapped"] = build_wrapped(period, refresh=False, _cache=cache)
    return result


def dashboard():
    print(json.dumps(build_dashboard(_arg_period()), ensure_ascii=False))



register_daily_cost_providers(load_scan_cache=_load_scan_cache, load_ledger=_load_ledger, compute=compute)
register_projects_providers(load_scan_cache=_load_scan_cache, compute=compute)

def _default_snapshot_generator():
    usage_data, latest_cache = compute(return_cache=True)
    meta = _load_json(PRICING_FILE, {}).get("_meta", {})
    usage_data["_pricing"] = {"updated_at": meta.get("updated_at", ""), "count": meta.get("count", 0)}
    daily_data = build_daily_costs(_arg_period(), refresh=False, _cache=latest_cache)
    projects_data = get_projects(refresh=False, _cache=latest_cache)
    return usage_data, daily_data, projects_data

register_snapshot_generator(_default_snapshot_generator)


def snapshot():
    """输出原子一致性快照 JSON。"""
    force = "--force" in sys.argv or "--refresh" in sys.argv
    payload = get_canonical_snapshot(force=force)
    print(json.dumps(payload, ensure_ascii=False))


from core.diagnostics import doctor


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
