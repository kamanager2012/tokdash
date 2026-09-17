import os
import glob
import json
import math
import threading
import urllib.request
import urllib.parse
from datetime import datetime, date, timezone

from core.config import (
    GROK_DIR,
    GROK_LOG,
    GROK_AUTH,
    GROK_QUOTA_CACHE,
    _USER_DIR,
    RANGE_KEYS,
    classify_date,
    parse_ts,
    _load_json,
    _atomic_write_json,
    token_total,
)
from core.pricing import (
    _raw_price,
    _pricing_id,
)
from core.storage import (
    _SCAN_CACHE_VERSION,
    ledger_reconcile,
    ledger_touch,
    _add_token_usage,
    _add_model_usage,
)
from core.collectors.claude import _iso_to_epoch
from core.collectors.codex import _codex_complete_offset, _codex_offset_guard

# 会话目录提供项目、模型和运行指标；新版 unified.jsonl 额外记录逐次推理 token。
# 旧版 inference_done 没有 token 字段，只用于上下文快照，不能计入总用量。
def _grok_file_signature(paths):
    parts = []
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


def _load_grok_session(summary_path, signature, mtime_ns):
    try:
        with open(summary_path, "r", encoding="utf-8", errors="ignore") as fh:
            summary = json.load(fh)
    except Exception:
        return None
    dt = parse_ts(summary.get("updated_at") or summary.get("created_at") or "")
    if dt is None:
        return None
    dt = dt.astimezone()
    session_dir = os.path.dirname(summary_path)

    signals_path = os.path.join(session_dir, "signals.json")
    try:
        with open(signals_path, "r", encoding="utf-8", errors="ignore") as fh:
            signals = json.load(fh)
    except Exception:
        signals = {}

    max_total = 0
    updates_path = os.path.join(session_dir, "updates.jsonl")
    try:
        with open(updates_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if "totalTokens" not in line:
                    continue
                try:
                    update = json.loads(line)
                except Exception:
                    continue
                total = (((update.get("params") or {}).get("_meta") or {}).get("totalTokens"))
                if isinstance(total, (int, float)) and total > max_total:
                    max_total = int(total)
    except OSError:
        pass

    event_turns = event_tools = event_duration = 0
    event_tool_errors = event_turn_errors = event_cancellations = 0
    events_path = os.path.join(session_dir, "events.jsonl")
    try:
        with open(events_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                event_type = event.get("type")
                if event_type == "turn_started":
                    event_turns += 1
                elif event_type == "tool_completed":
                    event_tools += 1
                    event_duration += int(event.get("duration_ms") or 0)
                    if event.get("outcome") not in (None, "success"):
                        event_tool_errors += 1
                elif event_type == "turn_ended":
                    if event.get("outcome") == "cancelled":
                        event_cancellations += 1
                    elif event.get("outcome") == "error":
                        event_turn_errors += 1
    except OSError:
        pass

    turns = int(signals.get("turnCount") or event_turns or 0)
    tools = int(signals.get("toolCallCount") or event_tools or 0)
    duration = int(signals.get("sessionDurationSeconds") or 0)
    ctx_used = int(signals.get("contextTokensUsed") or max_total or 0)
    ctx_window = int(signals.get("contextWindowTokens") or 0)
    signal_errors = int(signals.get("errorCount") or 0) + int(signals.get("toolFailureCount") or 0)
    errors = max(signal_errors, event_turn_errors, event_tool_errors)
    cancellations = max(int(signals.get("cancellationCount") or 0), event_cancellations)
    latency_count = int(signals.get("latencySampleCount") or turns or 0)
    group_dir = os.path.dirname(os.path.dirname(summary_path))
    from urllib.parse import unquote
    project = unquote(os.path.basename(group_dir))
    cwd_file = os.path.join(group_dir, ".cwd")
    try:
        with open(cwd_file, "r", encoding="utf-8", errors="ignore") as fh:
            project = fh.read().strip() or project
    except OSError:
        pass
    return {
        "sig": signature,
        "mtime": mtime_ns,
        "date": dt.date().isoformat(),
        "hour": dt.hour,
        "sid": (summary.get("info") or {}).get("id") or summary_path,
        "model": summary.get("current_model_id") or "unknown",
        "project": project if os.path.isabs(project) else "",
        "tokens": ctx_used or max_total,
        "turns": turns,
        "tools": tools,
        "duration": duration,
        "ctx_used": ctx_used,
        "ctx_window": ctx_window,
        "errors": errors,
        "cancellations": cancellations,
        "ttft_sum": int(signals.get("avgTimeToFirstTokenMs") or 0) * latency_count,
        "response_sum": int(signals.get("avgResponseTimeMs") or 0) * latency_count,
        "latency_count": latency_count,
    }


def _grok_usage_record(obj):
    if obj.get("msg") != "shell.turn.inference_done":
        return None
    ctx = obj.get("ctx") or {}
    if not isinstance(ctx, dict):
        return None
    token_keys = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens", "reasoning_tokens")
    if not any(key in ctx for key in token_keys):
        return None
    sid = str(obj.get("sid") or "")
    ts = str(obj.get("ts") or "")
    if not sid or parse_ts(ts) is None:
        return None
    try:
        prompt = max(int(ctx.get("prompt_tokens") or 0), 0)
        cached = max(int(ctx.get("cached_prompt_tokens") or 0), 0)
        completion = max(int(ctx.get("completion_tokens") or 0), 0)
        reasoning = max(int(ctx.get("reasoning_tokens") or 0), 0)
        loop_index = int(ctx.get("loop_index") or 0)
        attempts = int(ctx.get("attempts") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    cached = min(cached, prompt)
    reasoning = min(reasoning, completion)
    record_id = f"{sid}:{ts}:{loop_index}:{attempts}:{prompt}:{cached}:{completion}:{reasoning}"
    return {"id": record_id, "ts": ts, "sid": sid,
            "in": prompt - cached, "cr": cached,
            "out": completion - reasoning, "reason": reasoning}


def _grok_usage_cost(record, model):
    price_id = _pricing_id(model)
    if not price_id:
        return 0.0
    price = _raw_price(price_id)
    return (
        int(record.get("in", 0) or 0) * price["in"]
        + int(record.get("cr", 0) or 0) * price["cache_read"]
        + (int(record.get("out", 0) or 0) + int(record.get("reason", 0) or 0))
        * price["out"]
    ) / 1_000_000


def _load_grok_usage_records(cache):
    old = cache.get("grok_usage", {})
    if not isinstance(old, dict):
        old = {}
    try:
        stat = os.stat(GROK_LOG)
    except OSError:
        if cache.pop("grok_usage", None) is not None:
            cache["_dirty"] = True
        return []

    signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    complete_offset = _codex_complete_offset(GROK_LOG, stat.st_size)
    file_id = f"{stat.st_dev}:{stat.st_ino}"
    if (old.get("sig") == signature
            and int(old.get("parsed_size", 0) or 0) == complete_offset):
        return list(old.get("records") or [])

    append_from = None
    if isinstance(old, dict):
        old_offset = int(old.get("parsed_size", 0) or 0)
        if (old.get("file_id") == file_id and old_offset <= complete_offset
                and old.get("parsed_guard") == _codex_offset_guard(GROK_LOG, old_offset)):
            append_from = old_offset

    cached_records = old.get("records") or []
    if not isinstance(cached_records, list):
        cached_records = []
    records = [record for record in cached_records if isinstance(record, dict)] \
        if append_from is not None else []
    seen = {record.get("id") for record in records if record.get("id")}
    parse_start = append_from or 0
    try:
        with open(GROK_LOG, "rb") as fh:
            fh.seek(parse_start)
            while fh.tell() < complete_offset:
                raw = fh.readline()
                if not raw or fh.tell() > complete_offset:
                    break
                try:
                    obj = json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception:
                    continue
                record = _grok_usage_record(obj)
                if record and record["id"] not in seen:
                    records.append(record)
                    seen.add(record["id"])
    except OSError:
        return records

    updated = {
        "sig": signature,
        "file_id": file_id,
        "parsed_size": complete_offset,
        "parsed_guard": _codex_offset_guard(GROK_LOG, complete_offset),
        "records": records,
    }
    if updated != old:
        cache["grok_usage"] = updated
        cache["_dirty"] = True
    return records


def _grok_usage_days(records, sessions, latest_model):
    days = {}
    for record in records:
        dt = parse_ts(record.get("ts") or "")
        if dt is None:
            continue
        local_dt = dt.astimezone()
        day_key = local_dt.date().isoformat()
        day = days.setdefault(day_key, {
            "in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0, "cost": 0.0,
            "tokens": 0, "calls": 0, "sessions": set(), "hours": [0] * 24,
            "models": {}, "projects": {},
        })
        sid = record.get("sid") or ""
        meta = sessions.get(sid) or {}
        model = meta.get("model") or latest_model or "grok"
        amount = token_total(record)
        cost = _grok_usage_cost(record, model)
        _add_token_usage(day, record.get("in", 0), record.get("out", 0),
                         record.get("cr", 0), 0, record.get("reason", 0), cost, model)
        day["tokens"] += amount
        day["calls"] += 1
        if sid:
            day["sessions"].add(sid)
        day["hours"][local_dt.hour] += amount

        project = meta.get("project") or ""
        if project:
            project_day = day["projects"].setdefault(
                project, {"tokens": 0, "cost": 0.0, "sessions": set(), "models": {}})
            project_day["tokens"] += amount
            project_day["cost"] += cost
            if sid:
                project_day["sessions"].add(sid)
            project_day["models"][model] = project_day["models"].get(model, 0) + amount
    return days


# ---------- Grok 额度 (默认只读本地日志;实时 API 需显式开启) ----------
# 本地: ~/.grok/logs/unified.jsonl 中 `billing: fetched credits config`
# 可选: GET https://cli-chat-proxy.grok.com/v1/billing?format=credits
# 开关: ~/.tokei/config.json 的 grok_live_quota_enabled, 或 TOKEI_GROK_LIVE_QUOTA=1
_GROK_QUOTA_TTL = 30
_GROK_QUOTA_FALLBACK_TTL = 300
_GROK_QUOTA_LOG_SCAN_BYTES = 2 * 1024 * 1024
_GROK_BILLING_MAX_RESPONSE_BYTES = 1024 * 1024
_GROK_BILLING_MSG = "billing: fetched credits config"
_GROK_LIVE_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def _tokei_config():
    cfg = _load_json(os.path.join(_USER_DIR, "config.json"), {})
    return cfg if isinstance(cfg, dict) else {}


def _grok_live_quota_enabled():
    """实时额度默认关闭;仅用户显式开启或环境变量强制时才联网。"""
    env = os.environ.get("TOKEI_GROK_LIVE_QUOTA")
    if env == "0":
        return False
    if env == "1":
        return True
    return bool(_tokei_config().get("grok_live_quota_enabled"))


def _grok_auth_token():
    auth = _load_json(GROK_AUTH, {})
    if not isinstance(auth, dict):
        return None
    for entry in auth.values():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def _normalize_grok_billing(config, *, plan=None, source=None, updated=None,
                            now_epoch=None):
    if not isinstance(config, dict):
        return None
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    end = period.get("end") or config.get("billingPeriodEnd")
    reset = _iso_to_epoch(end) if end else None
    pct_raw = config.get("creditUsagePercent")
    if "creditUsagePercent" not in config:
        # Grok 的 protobuf JSON 会省略 0 值；仅完整的统一账单周期可安全视为 0% 已用。
        has_period = bool(period.get("start") and reset is not None)
        if config.get("isUnifiedBillingUser") is not True or not has_period:
            return None
        pct_raw = 0.0
    elif pct_raw is None:
        return None
    try:
        pct = float(pct_raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pct):
        return None
    pct = min(100.0, max(0.0, pct))
    products = []
    for item in config.get("productUsage") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("product") or item.get("name")
        if not name:
            continue
        usage_pct = item.get("usagePercent")
        try:
            normalized_pct = float(usage_pct) if usage_pct is not None else None
            if normalized_pct is not None:
                normalized_pct = (min(100.0, max(0.0, normalized_pct))
                                  if math.isfinite(normalized_pct) else None)
            products.append({
                "name": str(name),
                "pct": normalized_pct,
            })
        except (TypeError, ValueError):
            products.append({"name": str(name), "pct": None})
    now = int(now_epoch if now_epoch is not None else datetime.now().timestamp())
    stale = bool(reset is not None and reset <= now)
    if stale:
        # 周期已过重置点,本地快照不再代表当前额度。
        pct = 0.0
        for item in products:
            if item.get("pct") is not None:
                item["pct"] = 0.0
    period_type = period.get("type") or ""
    if "WEEKLY" in str(period_type).upper():
        window = "week"
    elif "MONTH" in str(period_type).upper():
        window = "month"
    else:
        window = "week"
    plan_name = plan
    if not plan_name:
        plan_name = config.get("subscriptionTier") or config.get("plan")
    return {
        "pct": pct,
        "reset": None if stale else reset,
        "plan": plan_name,
        "products": products,
        "window": window,
        "source": source,
        "updated": int(updated) if updated is not None else now,
        "stale": stale,
    }


def _scan_grok_billing_from_log(path=None, max_bytes=_GROK_QUOTA_LOG_SCAN_BYTES):
    """从 unified.jsonl 尾部读取最近一次 billing: fetched credits config。"""
    log_path = path or GROK_LOG
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return None
    if size <= 0:
        return None
    start = max(0, size - max_bytes)
    latest = None
    latest_ts = None
    try:
        with open(log_path, "rb") as fh:
            fh.seek(start)
            if start:
                fh.readline()  # 丢掉半行
            for raw in fh:
                if _GROK_BILLING_MSG.encode("utf-8") not in raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception:
                    continue
                if obj.get("msg") != _GROK_BILLING_MSG:
                    continue
                ctx = obj.get("ctx") or {}
                if not isinstance(ctx, dict):
                    continue
                config = ctx.get("config")
                if not isinstance(config, dict):
                    continue
                ts = obj.get("ts")
                if latest_ts is None or (isinstance(ts, str) and ts >= latest_ts):
                    latest_ts = ts if isinstance(ts, str) else latest_ts
                    latest = {
                        "config": config,
                        "plan": ctx.get("subscriptionTier") or ctx.get("plan"),
                        "ts": ts,
                    }
    except OSError:
        return None
    if not latest:
        return None
    updated = _iso_to_epoch(latest.get("ts"))
    return _normalize_grok_billing(
        latest["config"], plan=latest.get("plan"), source="log", updated=updated)


def _cached_grok_quota(max_age):
    cached = _load_json(GROK_QUOTA_CACHE, {})
    if not isinstance(cached, dict):
        return None
    quota = cached.get("quota")
    fetched_at = cached.get("fetched_at")
    if not isinstance(quota, dict) or fetched_at is None:
        return None
    try:
        age = datetime.now().timestamp() - float(fetched_at)
    except (TypeError, ValueError):
        return None
    if age > max_age:
        return None
    out = dict(quota)
    out.setdefault("source", cached.get("source") or out.get("source") or "cache")
    return out


def _save_grok_quota_cache(quota):
    if not isinstance(quota, dict) or quota.get("pct") is None:
        return
    try:
        os.makedirs(os.path.dirname(GROK_QUOTA_CACHE) or _USER_DIR, exist_ok=True)
        _atomic_write_json(GROK_QUOTA_CACHE, {
            "fetched_at": datetime.now().timestamp(),
            "source": quota.get("source"),
            "quota": quota,
        })
        try:
            os.chmod(GROK_QUOTA_CACHE, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def fetch_grok_live_quota():
    """仅在用户开启时请求 Grok billing API;失败回退到短缓存。"""
    if not _grok_live_quota_enabled():
        return None
    cached = _cached_grok_quota(_GROK_QUOTA_TTL)
    if cached and cached.get("source") == "live":
        return cached
    token = _grok_auth_token()
    if not token:
        return _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL)
    try:
        import urllib.request
        req = urllib.request.Request(
            _GROK_LIVE_BILLING_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "Tokei",
            },
        )
        # urllib copies regular headers to redirects. Keep the credential in the
        # initial-request-only header set and reject any redirected response.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=3) as res:
            final_url = res.geturl() if hasattr(res, "geturl") else _GROK_LIVE_BILLING_URL
            if final_url != _GROK_LIVE_BILLING_URL:
                return _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL)
            payload = res.read(_GROK_BILLING_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _GROK_BILLING_MAX_RESPONSE_BYTES:
                return _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL)
            data = json.loads(payload)
        config = data.get("config") if isinstance(data, dict) else None
        plan = None
        if isinstance(data, dict):
            plan = data.get("subscriptionTier") or data.get("plan")
        quota = _normalize_grok_billing(config, plan=plan, source="live")
        if not quota:
            return _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL)
        _save_grok_quota_cache(quota)
        return quota
    except Exception:
        return _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL)


def scan_grok_quota():
    """默认优先本地日志;仅显式开启时才走实时 API。"""
    log_quota = _scan_grok_billing_from_log()
    if log_quota and not log_quota.get("stale"):
        _save_grok_quota_cache(log_quota)

    if _grok_live_quota_enabled():
        live = fetch_grok_live_quota()
        if live and live.get("pct") is not None:
            return live

    if log_quota and log_quota.get("pct") is not None:
        return log_quota

    cached = _cached_grok_quota(_GROK_QUOTA_FALLBACK_TTL * 12)  # 本地缓存放宽到约 1 小时
    if cached and cached.get("pct") is not None:
        out = dict(cached)
        out["source"] = "cache"
        # 过期周期仍标 stale
        reset = out.get("reset")
        now = int(datetime.now().timestamp())
        if reset is not None and int(reset) <= now:
            out["stale"] = True
            out["pct"] = 0.0
            out["reset"] = None
        return out
    return {}


# ---------- 千问办公额度 (官方桌面端本机 MCP；需显式开启) ----------
# 千问办公负责登录、token 刷新和服务端额度请求。Tokei 只调用它监听在
# 127.0.0.1 的只读 qwenwork.usage 资源，不读取 auth-v2.dat 或浏览器 Cookie。
# ---------- CodexBar-compatible provider quotas ----------
# Remote providers are opt-in. Antigravity is the exception: it only probes an
# already-running loopback language server and never starts the app/CLI.
_PROVIDER_QUOTA_TTL = 300
_PROVIDER_QUOTA_FALLBACK_TTL = 3600
_PROVIDER_QUOTA_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_PROVIDER_QUOTA_CACHE_LOCK = threading.Lock()
_PROVIDER_QUOTA_ENV = {
    "cursor": "TOKEI_CURSOR_QUOTA",
    "grok_bot": "TOKEI_GROK_BOT_QUOTA",
    "zed": "TOKEI_ZED_QUOTA",
    "sub2api": "TOKEI_SUB2API_QUOTA",
    "zai": "TOKEI_ZAI_QUOTA",
    "antigravity": "TOKEI_ANTIGRAVITY_QUOTA",
}




def scan_grok(bounds, cache=None):
    ledger_touch("grok")
    cache = cache if cache is not None else {"v": _SCAN_CACHE_VERSION}
    file_cache = cache.setdefault("grok", {})
    B = {k: {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
             "cost": 0.0, "models": {}, "usage_sessions": set(), "usage_calls": 0,
             "sessions": set(), "turns": 0, "tools": 0,
             "duration": 0, "ctx_used": 0, "ctx_window": 0, "errors": 0,
             "cancellations": 0, "ttft_sum": 0, "response_sum": 0, "latency_count": 0}
         for k in RANGE_KEYS}
    latest_mtime = -1
    latest_model = None
    summary_paths = (sorted(glob.glob(os.path.join(GROK_DIR, "*", "*", "summary.json")))
                     if os.path.isdir(GROK_DIR) else [])
    stale = set(file_cache)
    for summary_path in summary_paths:
        stale.discard(summary_path)
        session_dir = os.path.dirname(summary_path)
        related = [
            summary_path,
            os.path.join(session_dir, "signals.json"),
            os.path.join(session_dir, "updates.jsonl"),
            os.path.join(session_dir, "events.jsonl"),
        ]
        signature = _grok_file_signature(related)
        try:
            mtime_ns = os.stat(summary_path).st_mtime_ns
        except OSError:
            continue
        existing = file_cache.get(summary_path)
        if isinstance(existing, dict) and existing.get("sig") == signature:
            continue
        parsed = _load_grok_session(summary_path, signature, mtime_ns)
        if parsed is None:
            if summary_path in file_cache:
                file_cache.pop(summary_path, None)
                cache["_dirty"] = True
            continue
        file_cache[summary_path] = parsed
        cache["_dirty"] = True

    for summary_path in stale:
        file_cache.pop(summary_path, None)
        cache["_dirty"] = True

    sessions = {}
    for path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        sid = entry.get("sid") or path
        current = sessions.get(sid)
        if current is None or int(entry.get("mtime", 0)) > int(current.get("mtime", 0)):
            sessions[sid] = entry

    metrics = ("tokens", "turns", "tools", "duration", "ctx_used", "ctx_window",
               "errors", "cancellations", "ttft_sum", "response_sum", "latency_count")
    for sid, entry in sessions.items():
        day_key = entry.get("date")
        try:
            day_date = date.fromisoformat(day_key)
        except (TypeError, ValueError):
            continue
        mtime = int(entry.get("mtime", 0) or 0)
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_model = entry.get("model") or "unknown"
        for range_key in classify_date(day_date, bounds):
            bucket = B[range_key]
            bucket["sessions"].add(sid)
            for field in metrics:
                bucket[field] += int(entry.get(field, 0) or 0)

    usage_days = _grok_usage_days(_load_grok_usage_records(cache), sessions, latest_model)
    # 会话数只能来自现存日志(被清日志无从归属)
    for day_key, day in usage_days.items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            bucket = B[range_key]
            bucket["usage_sessions"].update(day.get("sessions", set()))
            bucket["sessions"].update(day.get("sessions", set()))

    # sessions(set)/projects(含嵌套 set)不进账本,只保留 JSON 兼容字段
    grok_live_days = {
        day_key: {k: v for k, v in day.items() if k not in ("sessions", "projects")}
        for day_key, day in usage_days.items()}
    for day_key, day in ledger_reconcile("grok", grok_live_days).items():
        try:
            day_date = date.fromisoformat(day_key)
        except ValueError:
            continue
        for range_key in classify_date(day_date, bounds):
            bucket = B[range_key]
            _add_token_usage(bucket, day.get("in", 0), day.get("out", 0),
                             day.get("cr", 0), 0, day.get("reason", 0), day.get("cost", 0))
            for model, usage in (day.get("models") or {}).items():
                _add_model_usage(bucket["models"], model, usage.get("in", 0),
                                 usage.get("out", 0), usage.get("cr", 0), 0,
                                 usage.get("reason", 0), usage.get("cost", 0))
            bucket["usage_calls"] += int(day.get("calls", 0) or 0)
    return {"ranges": B, "model": latest_model, "days": usage_days}

