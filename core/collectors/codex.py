import os
import glob
import json
import math
import re
import hashlib
import base64
import subprocess
import threading
import tempfile as _tempfile
import time as _time
import urllib.request
import urllib.parse
from datetime import datetime, date, timezone

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from core.config import (
    _atomic_write_json,
    CODEX_DIR,
    CODEX_ARCHIVED_DIR,
    CODEX_AUTH,
    CODEX_CONFIG,
    CODEX_QUOTA_CACHE,
    CODEX_RESET_CARDS_CACHE,
    _SCAN_CACHE_FILE,
    RANGE_KEYS,
    TOKEN_FIELDS,
    classify_date,
    parse_ts,
    _load_json,
    _path_candidates,
    _existing_dirs,
)
from core.pricing import (
    _raw_price,
    _known_id_or_raw,
    _has_known_price,
    price_for,
)
from core.storage import (
    _CODEX_EVENT_CACHE_SUFFIX,
    _CODEX_PARSER_VERSION,
    _CODEX_SCAN_CHECKPOINT_INTERVAL,
    _codex_event_cache_dir,
    _remove_codex_event_cache_dir,
    _save_scan_cache,
    ledger_reconcile,
    ledger_touch,
    _add_model_usage,
)
from core.collectors.claude import _iso_to_epoch

# TTL 曾等于 App 的 30s 刷新间隔,缓存每轮刚好过期 —— 等于每次刷新都真打一次官方
# 接口(约 2880 次/天)。额度对应的是周窗口,变化很慢,拉长到 5 分钟没有感知差别。
_CODEX_QUOTA_TTL = 300
_CODEX_QUOTA_FALLBACK_TTL = 300
_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_USAGE_MAX_RESPONSE_BYTES = 256 * 1024
_CODEX_RESET_CARDS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
_CODEX_RESET_CARDS_REFRESH_INTERVAL = 24 * 3600
_CODEX_RESET_CARDS_RETRY_INTERVAL = 6 * 3600
_CODEX_RESET_CARDS_MAX_RESPONSE_BYTES = 256 * 1024


# _atomic_write_json imported from core.config


def _window_from_codex_live(window):
    if not isinstance(window, dict):
        return None
    used = window.get("used_percent")
    reset_at = window.get("reset_at")
    reset_after = window.get("reset_after_seconds")
    if reset_at is None and reset_after is not None:
        reset_at = int(datetime.now().timestamp() + float(reset_after))
    out = {}
    if used is not None:
        out["used_percent"] = float(used)
    if window.get("limit_window_seconds") is not None:
        out["window_minutes"] = int(round(float(window["limit_window_seconds"]) / 60))
    if reset_at is not None:
        out["resets_at"] = int(reset_at)
    return out or None


def _codex_live_to_limits(data):
    rl = (data or {}).get("rate_limit") or {}
    primary = _window_from_codex_live(rl.get("primary_window"))
    secondary = _window_from_codex_live(rl.get("secondary_window"))
    if not primary and not secondary:
        return None
    return {
        "limit_id": "codex",
        "limit_name": None,
        "primary": primary,
        "secondary": secondary,
        "credits": data.get("credits"),
        "plan_type": data.get("plan_type"),
        "rate_limit_reached_type": rl.get("rate_limit_reached_type"),
    }


def _codex_limits_have_active_window(limits, now_epoch=None):
    now = float(now_epoch if now_epoch is not None else datetime.now().timestamp())
    for slot_name in ("primary", "secondary"):
        slot = (limits or {}).get(slot_name) or {}
        reset = slot.get("resets_at")
        try:
            if reset is not None and float(reset) > now:
                return True
        except (TypeError, ValueError, OverflowError):
            continue
    return False


def _cached_codex_live_limits(max_age, allow_active_window=False, account_key=None):
    cached = _load_json(CODEX_QUOTA_CACHE, {})
    fetched_at = cached.get("fetched_at")
    limits = cached.get("limits")
    if not fetched_at or not limits:
        return None
    cached_account_key = cached.get("account_key")
    if account_key and cached_account_key and cached_account_key != account_key:
        return None
    try:
        fetched_at = float(fetched_at)
    except (TypeError, ValueError, OverflowError):
        return None
    age = datetime.now().timestamp() - fetched_at
    if age > max_age and not (
            allow_active_window and _codex_limits_have_active_window(limits)):
        return None
    return limits, cached.get("plan"), fetched_at


def _codex_live_snapshot_is_current(live_updated, local_updated):
    if live_updated is None or not local_updated:
        return True
    local_epoch = _iso_to_epoch(local_updated)
    if local_epoch is None:
        return True
    try:
        return float(live_updated) >= local_epoch
    except (TypeError, ValueError, OverflowError):
        return False


def _decode_jwt_claims(token):
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def _codex_config():
    """→ {"model_provider": str|None, "model_providers": [名字]};读不到返回空。

    tomllib 是 3.11+ 才有的,而没装 Homebrew Python 的机器会落到 /usr/bin/python3
    (macOS 自带 3.9),模块级 import 会让整个脚本崩掉 —— 所以惰性导入 + 最小回退。
    """
    try:
        with open(CODEX_CONFIG, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return {}
    try:
        import tomllib
    except ImportError:
        # 3.9 回退:只认顶层 model_provider = "x" 和 [model_providers.x] 段名。
        text = raw.decode("utf-8", errors="ignore")
        hit = re.search(r'^\s*model_provider\s*=\s*["\']([^"\']+)["\']', text, re.M)
        return {
            "model_provider": hit.group(1) if hit else None,
            "model_providers": re.findall(
                r'^\s*\[\s*model_providers\.([^\]\s.]+)', text, re.M),
        }
    try:
        data = tomllib.loads(raw.decode("utf-8", errors="ignore")) or {}
    except Exception:
        return {}
    provider = data.get("model_provider")
    return {
        "model_provider": provider if isinstance(provider, str) else None,
        "model_providers": list((data.get("model_providers") or {}).keys()),
    }


def _codex_is_custom_provider():
    """True 表示 Codex 已切到非 OpenAI 的 provider(cc Switch 之类)。

    只认显式声明:光有 [model_providers.x] 段、但没把 model_provider 指过去的用户
    仍在用官方额度,误判会把他们的额度卡整块藏掉。
    """
    provider = _codex_config().get("model_provider")
    return bool(provider) and provider != "openai"


def _codex_auth_context(auth):
    if not isinstance(auth, dict):
        return {}
    nested = auth.get("tokens")
    tokens = nested if isinstance(nested, dict) else {}

    def value(*names):
        for source in (tokens, auth):
            for name in names:
                item = source.get(name)
                if isinstance(item, str) and item:
                    return item
        return None

    access_token = value("access_token", "accessToken")
    if not access_token:
        return {}
    id_token = value("id_token", "idToken")
    claims = _decode_jwt_claims(access_token)
    id_claims = _decode_jwt_claims(id_token)
    auth_claim = claims.get("https://api.openai.com/auth")
    id_auth_claim = id_claims.get("https://api.openai.com/auth")
    auth_claim = auth_claim if isinstance(auth_claim, dict) else {}
    id_auth_claim = id_auth_claim if isinstance(id_auth_claim, dict) else {}
    account_id = value("account_id", "accountId")
    account_id = account_id or auth_claim.get("chatgpt_account_id")
    account_id = account_id or id_auth_claim.get("chatgpt_account_id")
    identity = account_id or claims.get("sub") or id_claims.get("sub") or access_token
    return {
        "access_token": access_token,
        "account_id": str(account_id) if account_id else None,
        "account_key": hashlib.sha256(str(identity).encode("utf-8")).hexdigest(),
        "auth_key": hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
    }


def fetch_codex_live_limits():
    if os.environ.get("TOKEI_CODEX_LIVE_QUOTA") == "0":
        return None
    # When the user has switched to a third-party provider (cc Switch, etc.),
    # the official OpenAI quota endpoint is no longer relevant. Skip it and
    # clear any stale cached official quota so the dashboard falls back to
    # showing only token usage/cost.
    if _codex_is_custom_provider():
        try:
            if os.path.exists(CODEX_QUOTA_CACHE):
                os.remove(CODEX_QUOTA_CACHE)
        except Exception:
            pass
        return None
    cached = _cached_codex_live_limits(_CODEX_QUOTA_TTL)
    if cached:
        return cached
    auth = _load_json(CODEX_AUTH, {})
    auth_context = _codex_auth_context(auth)
    access_token = auth_context.get("access_token")
    account_key = auth_context.get("account_key")
    auth_key = auth_context.get("auth_key")
    if not access_token or not account_key:
        return None
    cache_state = _load_json(CODEX_QUOTA_CACHE, {})
    cached = _cached_codex_live_limits(_CODEX_QUOTA_TTL, account_key=account_key)
    if cached:
        return cached
    # 失败退避:网络不可达(如公司代理拦截)时 5 分钟内不再联网重试,
    # 否则每轮 30s 刷新都会白等约 6s 超时
    last_failure = cache_state.get("last_failure_at", 0)
    if cache_state.get("account_key") not in (None, account_key):
        last_failure = 0
    if cache_state.get("account_key") == account_key \
            and cache_state.get("auth_key") not in (None, auth_key):
        last_failure = 0
    try:
        failure_is_recent = (
            bool(last_failure)
            and datetime.now().timestamp() - float(last_failure) < 300)
    except (TypeError, ValueError, OverflowError):
        failure_is_recent = False
    if failure_is_recent:
        return _cached_codex_live_limits(
            _CODEX_QUOTA_FALLBACK_TTL, allow_active_window=True,
            account_key=account_key)
    try:
        import urllib.request
        from urllib.parse import urlparse
        req = urllib.request.Request(_CODEX_USAGE_URL)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "Tokei")
        req.add_unredirected_header("Authorization", f"Bearer {access_token}")
        account_id = auth_context.get("account_id")
        if account_id:
            req.add_unredirected_header("ChatGPT-Account-Id", account_id)
        with urllib.request.urlopen(req, timeout=3) as res:
            final_url = urlparse(res.geturl())
            if final_url.scheme != "https" or final_url.hostname != "chatgpt.com":
                raise ValueError("unexpected Codex usage redirect")
            raw = res.read(_CODEX_USAGE_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _CODEX_USAGE_MAX_RESPONSE_BYTES:
            raise ValueError("Codex usage response is too large")
        data = json.loads(raw)
        limits = _codex_live_to_limits(data)
        if not limits:
            raise ValueError("invalid Codex usage response")
        plan = data.get("plan_type")
        fetched_at = datetime.now().timestamp()
        _atomic_write_json(CODEX_QUOTA_CACHE, {
            "fetched_at": fetched_at,
            "limits": limits,
            "plan": plan,
            "account_key": account_key,
            "auth_key": auth_key,
            "source": "live",
        })
        return limits, plan, fetched_at
    except Exception:
        try:
            state = _load_json(CODEX_QUOTA_CACHE, {})
            if state.get("account_key") not in (None, account_key):
                state = {}
            state["last_failure_at"] = datetime.now().timestamp()
            state["account_key"] = account_key
            state["auth_key"] = auth_key
            _atomic_write_json(CODEX_QUOTA_CACHE, state)
        except Exception:
            pass
        return _cached_codex_live_limits(
            _CODEX_QUOTA_FALLBACK_TTL, allow_active_window=True,
            account_key=account_key)


def _normalize_codex_reset_cards(data, now_epoch):
    if not isinstance(data, dict) or not isinstance(data.get("credits"), list):
        return None
    expires = []
    for credit in data["credits"]:
        if not isinstance(credit, dict) or credit.get("status") != "available":
            continue
        if credit.get("is_supported_by_plan") is False:
            continue
        expires_at = parse_ts(credit.get("expires_at") or "")
        if expires_at is None:
            continue
        epoch = int(expires_at.timestamp())
        if epoch > now_epoch:
            expires.append(epoch)
    ordered = sorted(expires)
    return {
        "count": len(ordered),
        "expires": ordered,
        "updated": int(now_epoch),
    }


def _cached_codex_reset_cards(state, now_epoch):
    cards = state.get("cards") if isinstance(state, dict) else None
    if not isinstance(cards, dict):
        return {}
    expires = []
    for value in cards.get("expires") or []:
        try:
            epoch = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if epoch > now_epoch:
            expires.append(epoch)
    expires.sort()
    return {
        "count": len(expires),
        "expires": expires,
        "updated": cards.get("updated"),
    }


def _codex_reset_cards_next_attempt(cards, now_epoch):
    next_daily = int(now_epoch + _CODEX_RESET_CARDS_REFRESH_INTERVAL)
    expires = []
    for value in cards.get("expires") or []:
        try:
            epoch = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if epoch > now_epoch:
            expires.append(epoch)
    return min(next_daily, min(expires) + 60) if expires else next_daily


def _save_codex_reset_cards_state(state):
    try:
        _atomic_write_json(CODEX_RESET_CARDS_CACHE, state)
        os.chmod(CODEX_RESET_CARDS_CACHE, 0o600)
    except Exception:
        pass


def fetch_codex_reset_cards(now_epoch=None):
    """Return available reset-card expirations with a persistent low-frequency cache."""
    if os.environ.get("TOKEI_CODEX_LIVE_QUOTA") == "0":
        return {}
    # 重置卡是 OpenAI 账号级资产,不随 CLI 当前指向的 provider 变化 —— 临时切到
    # 第三方中转的人手上那几张卡还在,切回来就要用,所以这里不按 provider 屏蔽。
    now_epoch = int(datetime.now().timestamp()) if now_epoch is None else int(now_epoch)
    auth = _load_json(CODEX_AUTH, {})
    auth_context = _codex_auth_context(auth)
    access_token = auth_context.get("access_token")
    account_key = auth_context.get("account_key")
    auth_key = auth_context.get("auth_key")
    if not access_token or not account_key or not auth_key:
        return {}

    state = _load_json(CODEX_RESET_CARDS_CACHE, {})
    if not isinstance(state, dict) or state.get("account_key") != account_key:
        state = {"account_key": account_key, "auth_key": auth_key}
    elif state.get("auth_key") and state.get("auth_key") != auth_key:
        # Codex refreshed or replaced the token after an auth failure. Retry once now.
        state["next_attempt_at"] = 0
        state.pop("last_error", None)
    state["auth_key"] = auth_key
    cached = _cached_codex_reset_cards(state, now_epoch)
    try:
        next_attempt_at = int(state.get("next_attempt_at") or 0)
    except (TypeError, ValueError, OverflowError):
        next_attempt_at = 0
    if now_epoch < next_attempt_at:
        return cached

    try:
        import urllib.request
        from urllib.parse import urlparse
        request = urllib.request.Request(_CODEX_RESET_CARDS_URL)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", "Tokei")
        request.add_unredirected_header("Authorization", f"Bearer {access_token}")
        account_id = auth_context.get("account_id")
        if account_id:
            request.add_unredirected_header("ChatGPT-Account-Id", str(account_id))
        with urllib.request.urlopen(request, timeout=3) as response:
            final_url = urlparse(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "chatgpt.com":
                raise ValueError("unexpected Codex reset-card redirect")
            raw = response.read(_CODEX_RESET_CARDS_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _CODEX_RESET_CARDS_MAX_RESPONSE_BYTES:
            raise ValueError("Codex reset-card response is too large")
        cards = _normalize_codex_reset_cards(json.loads(raw), now_epoch)
        if cards is None:
            raise ValueError("invalid Codex reset-card response")
        state = {
            "account_key": account_key,
            "auth_key": auth_key,
            "fetched_at": now_epoch,
            "last_attempt_at": now_epoch,
            "next_attempt_at": _codex_reset_cards_next_attempt(cards, now_epoch),
            "cards": cards,
        }
        _save_codex_reset_cards_state(state)
        return cards
    except Exception as exc:
        status = getattr(exc, "code", None)
        if status in (401, 403):
            state["last_error"] = "auth"
        elif status in (404, 410):
            state["last_error"] = "unsupported"
        else:
            state["last_error"] = "request"
        state["last_attempt_at"] = now_epoch
        retry_interval = (
            _CODEX_RESET_CARDS_REFRESH_INTERVAL
            if status in (404, 410)
            else _CODEX_RESET_CARDS_RETRY_INTERVAL
        )
        state["next_attempt_at"] = now_epoch + retry_interval
        _save_codex_reset_cards_state(state)
        return cached


def _codex_event_key(event):
    if not isinstance(event, list) or len(event) < 11:
        return None
    total_values = event[2:6]
    if not all(value is not None for value in total_values):
        return None
    return tuple(event[2:10])


# _codex_event_cache_dir imported from core.storage


def _codex_event_cache_path(file_path):
    normalized = os.path.normcase(os.path.realpath(file_path))
    digest = hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
    return os.path.join(_codex_event_cache_dir(), f"{digest}.jsonl")


def _codex_event_cache_ready(file_path, entry):
    if not isinstance(entry, dict) or entry.get("event_count") is None:
        return False
    try:
        expected_size = int(entry.get("event_cache_size", -1))
        return expected_size >= 0 and os.path.getsize(
            _codex_event_cache_path(file_path)) >= expected_size
    except (OSError, TypeError, ValueError):
        return False


def _codex_write_event_cache(file_path, events):
    directory = _codex_event_cache_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    destination = _codex_event_cache_path(file_path)
    fd, tmp = _tempfile.mkstemp(prefix=".codex-events-", suffix=".jsonl", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, destination)
        return os.path.getsize(destination)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _codex_append_event_cache(file_path, events, expected_size):
    destination = _codex_event_cache_path(file_path)
    with open(destination, "r+b") as handle:
        current_size = os.fstat(handle.fileno()).st_size
        if current_size < expected_size:
            raise OSError("Codex event cache is shorter than its committed size")
        handle.truncate(expected_size)
        handle.seek(expected_size)
        for event in events:
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            handle.write(payload.encode("utf-8"))
            handle.write(b"\n")
        return handle.tell()


def _codex_remove_event_cache(file_path):
    try:
        os.remove(_codex_event_cache_path(file_path))
    except OSError:
        pass


def _codex_clear_event_cache(file_cache):
    for file_path in list(file_cache):
        _codex_remove_event_cache(file_path)
    file_cache.clear()


def _iter_codex_cached_events(file_path, start_index=0, limit=None):
    emitted = 0
    with open(_codex_event_cache_path(file_path), "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < start_index:
                continue
            if limit is not None and emitted >= limit:
                break
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                raise OSError("Codex event cache contains invalid JSON")
            if not isinstance(event, list):
                raise OSError("Codex event cache contains an invalid event")
            emitted += 1
            yield event


def _codex_event_metadata(events):
    keys = []
    first_ts = None
    last_ts = None
    for event in events:
        if first_ts is None and event:
            first_ts = str(event[0])
        if event:
            last_ts = str(event[0])
        if len(keys) < 2:
            key = _codex_event_key(event)
            if key is not None:
                keys.append(list(key))
    return {
        "event_count": len(events),
        "first_keys": keys,
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
    }


def _codex_days_from_cached_events(file_path, start_index=0, event_count=None):
    days = {}
    limit = None if event_count is None else max(int(event_count) - start_index, 0)
    for event in _iter_codex_cached_events(
            file_path, start_index=start_index, limit=limit):
        _codex_add_event(days, event)
    return days


def _codex_entry_prefix_key(entry):
    values = entry.get("first_keys") or []
    if len(values) < 2:
        return None
    try:
        return tuple(values[0]), tuple(values[1])
    except TypeError:
        return None


def _codex_cached_prefix_match_count(
        child_path, parent_path, child_count=None, parent_count=None):
    count = 0
    child_events = _iter_codex_cached_events(child_path, limit=child_count)
    parent_events = _iter_codex_cached_events(parent_path, limit=parent_count)
    for child, parent in zip(child_events, parent_events):
        child_key = _codex_event_key(child)
        parent_key = _codex_event_key(parent)
        if child_key is None or child_key != parent_key:
            break
        count += 1
    return count


def _codex_cached_burst_count(file_path, start_index, event_count):
    burst_second = None
    count = 0
    for event in _iter_codex_cached_events(
            file_path, start_index=start_index,
            limit=max(int(event_count) - start_index, 0)):
        if not event:
            break
        event_second = str(event[0])[:19]
        if burst_second is None:
            burst_second = event_second
        elif event_second != burst_second:
            break
        count += 1
    return count if count >= 5 else 0


def _codex_cached_drop_count(file_path, entry, file_cache):
    by_sid = {
        candidate.get("session_id"): (path, candidate)
        for path, candidate in file_cache.items()
        if candidate.get("session_id")
    }
    event_count = int(entry.get("event_count", 0) or 0)
    drop_count = 0
    prefix_open = False

    parent = by_sid.get(entry.get("forked_from_id"))
    if parent and parent[0] != file_path:
        drop_count = _codex_cached_prefix_match_count(
            file_path, parent[0], event_count, parent[1].get("event_count"))
        prefix_open = drop_count > 0 and drop_count == event_count

    prefix_key = _codex_entry_prefix_key(entry)
    if drop_count == 0 and prefix_key is not None and event_count >= 2:
        child_first_ts = str(entry.get("first_event_ts") or "")
        best = 0
        for parent_path, parent_entry in file_cache.items():
            if parent_path == file_path or _codex_entry_prefix_key(parent_entry) != prefix_key:
                continue
            parent_first_ts = str(parent_entry.get("first_event_ts") or "")
            if not parent_first_ts or parent_first_ts >= child_first_ts:
                continue
            best = max(best, _codex_cached_prefix_match_count(
                file_path, parent_path, event_count, parent_entry.get("event_count")))
        if best >= 2:
            drop_count = best
            prefix_open = drop_count == event_count

    burst_count = _codex_cached_burst_count(file_path, drop_count, event_count)
    if burst_count:
        drop_count += burst_count

    if event_count < 2 and not entry.get("forked_from_id"):
        prefix_open = True
    elif entry.get("forked_from_id") and parent is None:
        prefix_open = True
    return min(drop_count, event_count), prefix_open


def _codex_migrate_event_cache(file_cache):
    if not any(isinstance(entry, dict) and "events" in entry for entry in file_cache.values()):
        return False

    canonical = _codex_canonical_file_cache(file_cache)
    drops = _codex_replayed_event_indexes(canonical)
    days_by_file = _codex_deduped_days(canonical)
    prepared = {}
    for file_path, entry in file_cache.items():
        events = entry.get("events") or []
        cache_size = _codex_write_event_cache(file_path, events)
        metadata = _codex_event_metadata(events)
        skipped = drops.get(file_path, set())
        drop_count = 0
        while drop_count in skipped:
            drop_count += 1
        prepared[file_path] = {
            **metadata,
            "event_cache_size": cache_size,
            "drop_count": drop_count,
            "dedupe_open": bool(drop_count and drop_count == len(events)),
            "deduped_days": days_by_file.get(file_path, {}),
            "canonical": file_path in canonical,
        }

    for file_path, entry in file_cache.items():
        entry.update(prepared[file_path])
        entry["days"] = entry["deduped_days"] if entry["canonical"] else {}
        entry.pop("events", None)
    return True


def _codex_add_event(days, event):
    dk = event[1]
    li, lc, lo, lr, cost = event[6:11]
    day = days.setdefault(dk, {"in": 0, "cached": 0, "out": 0,
                               "reason": 0, "cost": 0.0, "models": {}, "hours": [0] * 24})
    day["in"] += li
    day["cached"] += lc
    day["out"] += lo
    day["reason"] += lr
    day["cost"] += cost
    model = event[11] if len(event) > 11 else None
    _add_model_usage(day["models"], model, max(li - lc, 0), lo, lc, 0, lr, cost)
    try:
        hour = datetime.fromisoformat(event[0]).astimezone().hour
        day["hours"][hour] += li + lo
    except (TypeError, ValueError):
        pass


def _codex_prefix_match_count(child_events, parent_events):
    n = 0
    while n < len(child_events) and n < len(parent_events):
        child_key = _codex_event_key(child_events[n])
        parent_key = _codex_event_key(parent_events[n])
        if child_key is None or child_key != parent_key:
            break
        n += 1
    return n


def _codex_replayed_event_indexes(file_cache):
    by_sid = {}
    ordered = []
    for file_path, entry in file_cache.items():
        events = entry.get("events") or []
        if events:
            ordered.append((file_path, entry))
        sid = entry.get("session_id")
        if sid:
            by_sid[sid] = (file_path, entry)

    drops = {}
    for file_path, entry in ordered:
        parent = by_sid.get(entry.get("forked_from_id"))
        if not parent or parent[0] == file_path:
            continue
        n = _codex_prefix_match_count(entry.get("events") or [], parent[1].get("events") or [])
        if n:
            drops.setdefault(file_path, set()).update(range(n))

    # Some Codex replay files do not carry fork metadata. Only use this
    # heuristic for longer matching prefixes; a one-event match can be a real
    # independent session with the same usage numbers.
    prefix_candidates = {}
    for file_path, entry in ordered:
        events = entry.get("events") or []
        if len(events) < 2:
            continue
        first = _codex_event_key(events[0])
        second = _codex_event_key(events[1])
        if first is not None and second is not None:
            prefix_candidates.setdefault((first, second), []).append((file_path, entry))

    for file_path, entry in ordered:
        if drops.get(file_path):
            continue
        child_events = entry.get("events") or []
        if len(child_events) < 2:
            continue
        first = _codex_event_key(child_events[0])
        second = _codex_event_key(child_events[1])
        if first is None or second is None:
            continue
        child_first_ts = child_events[0][0]
        best = 0
        for parent_path, parent_entry in prefix_candidates.get((first, second), []):
            if parent_path == file_path:
                continue
            parent_events = parent_entry.get("events") or []
            if not parent_events or parent_events[0][0] >= child_first_ts:
                continue
            best = max(best, _codex_prefix_match_count(child_events, parent_events))
        if best >= 2:
            drops.setdefault(file_path, set()).update(range(best))

    # 兜底:文件开头同一秒内 ≥5 条 token 事件必是回放转储(真实 API 一秒内
    # 不可能完成 5 次响应)。覆盖从父会话中段(如 compact 后)分叉、
    # 累计值与父文件开头对不上导致前缀匹配失效的场景。
    for file_path, entry in ordered:
        events = entry.get("events") or []
        if len(events) < 5:
            continue
        already = drops.get(file_path, set())
        start = 0
        while start in already:
            start += 1
        if start + 4 >= len(events):
            continue
        first_ev = events[start]
        if not isinstance(first_ev, list) or not first_ev:
            continue
        burst_sec = str(first_ev[0])[:19]
        n = start
        while n < len(events):
            ev = events[n]
            if not isinstance(ev, list) or not ev or str(ev[0])[:19] != burst_sec:
                break
            n += 1
        if n - start >= 5:
            drops.setdefault(file_path, set()).update(range(start, n))
    return drops


def _codex_deduped_days(file_cache):
    """Return per-file daily usage after removing copied rollout prefixes."""
    drops = _codex_replayed_event_indexes(file_cache)
    days_by_file = {}
    for file_path, entry in file_cache.items():
        skip = drops.get(file_path, set())
        for event_index, event in enumerate(entry.get("events", [])):
            if event_index in skip or _codex_event_key(event) is None:
                if event_index in skip:
                    continue
                if not isinstance(event, list) or len(event) < 11:
                    continue
            _codex_add_event(days_by_file.setdefault(file_path, {}), event)
    return days_by_file


_CODEX_MODEL_RECORD_TYPES = {"turn_context", "session_meta"}
_CODEX_USAGE_RECORD_MARKERS = (
    b'"token_count"', b'"turn_context"', b'"session_meta"',
)


def _codex_decode_json_string(raw):
    try:
        if b"\\" not in raw:
            return raw.decode("utf-8")
        return json.loads(b'"' + raw + b'"')
    except Exception:
        return raw.decode("utf-8", errors="ignore")


def _codex_probe_record_header(data):
    """Read selected JSON fields from a bounded record prefix.

    Codex adds top-level metadata fields over time. This structural probe tracks
    object depth instead of depending on serialized key order, while leaving
    large unrelated JSONL records bounded by the caller's prefix limits.
    """
    timestamp = None
    root_type = None
    payload_type = None
    model = None
    pending_keys = {}
    containers = []
    payload_depth = None
    depth = 0
    i = 0
    size = len(data)

    while i < size:
        ch = data[i]
        if ch in b" \t\r\n":
            i += 1
            continue

        if ch == 0x22:  # JSON string
            start = i + 1
            i = start
            while i < size:
                if data[i] == 0x5C:  # escape
                    i += 2
                    continue
                if data[i] == 0x22:
                    break
                i += 1
            if i >= size:
                break

            value = _codex_decode_json_string(bytes(data[start:i]))
            i += 1
            lookahead = i
            while lookahead < size and data[lookahead] in b" \t\r\n":
                lookahead += 1
            if lookahead < size and data[lookahead] == 0x3A:  # colon
                pending_keys[depth] = value
                i = lookahead + 1
                continue

            key = pending_keys.pop(depth, None)
            if depth == 1:
                if key == "timestamp":
                    timestamp = value
                elif key == "type":
                    root_type = value
            elif payload_depth is not None and depth == payload_depth:
                if key == "type":
                    payload_type = value
                elif key == "model":
                    model = value
            i = lookahead
            continue

        if ch in (0x7B, 0x5B):  # object or array open
            parent_depth = depth
            key = pending_keys.pop(parent_depth, None)
            containers.append(ch)
            depth += 1
            if ch == 0x7B and parent_depth == 1 and key == "payload":
                payload_depth = depth
            i += 1
            continue

        if ch in (0x7D, 0x5D):  # object or array close
            pending_keys.pop(depth, None)
            if payload_depth == depth:
                payload_depth = None
            if containers:
                containers.pop()
            depth = max(0, depth - 1)
            i += 1
            continue

        if ch == 0x2C:  # comma
            pending_keys.pop(depth, None)
        i += 1

    return timestamp, root_type, payload_type, model


def _iter_codex_usage_records(path, chunk_size=64 * 1024, header_limit=1024,
                              model_limit=4 * 1024, start_offset=0, end_offset=None):
    """Yield model changes and token records without buffering unrelated large JSONL lines."""
    prefix = bytearray()
    candidate = None
    kind = None

    with open(path, "rb", buffering=0) as fh:
        if start_offset:
            fh.seek(start_offset)
        while True:
            if end_offset is not None:
                remaining = end_offset - fh.tell()
                if remaining <= 0:
                    break
                chunk = fh.read(min(chunk_size, remaining))
            else:
                chunk = fh.read(chunk_size)
            if not chunk:
                break

            start = 0
            while start < len(chunk):
                newline = chunk.find(b"\n", start)
                end = len(chunk) if newline < 0 else newline
                piece = memoryview(chunk)[start:end]

                if kind == "token":
                    candidate.extend(piece)
                elif kind == "model":
                    take = min(len(piece), model_limit - len(prefix))
                    prefix.extend(piece[:take])
                    _, _, _, model = _codex_probe_record_header(prefix)
                    if model:
                        yield "model", model
                        prefix = bytearray()
                        kind = "ignore"
                    elif len(prefix) >= model_limit:
                        prefix = bytearray()
                        kind = "ignore"
                elif kind is None and len(prefix) < header_limit:
                    take = min(len(piece), header_limit - len(prefix))
                    prefix.extend(piece[:take])
                    if any(marker in prefix for marker in _CODEX_USAGE_RECORD_MARKERS):
                        timestamp, root_type, payload_type, model = (
                            _codex_probe_record_header(prefix)
                        )
                    else:
                        timestamp = root_type = payload_type = model = None
                    if (timestamp and root_type == "event_msg"
                            and payload_type == "token_count"):
                        candidate = prefix
                        prefix = bytearray()
                        kind = "token"
                        if take < len(piece):
                            candidate.extend(piece[take:])
                    elif timestamp and root_type in _CODEX_MODEL_RECORD_TYPES:
                        kind = "model"
                        if take < len(piece):
                            extra = min(len(piece) - take, model_limit - len(prefix))
                            prefix.extend(piece[take:take + extra])
                        _, _, _, model = _codex_probe_record_header(prefix)
                        if model:
                            yield "model", model
                            prefix = bytearray()
                            kind = "ignore"
                    elif (root_type is not None
                          and root_type not in _CODEX_MODEL_RECORD_TYPES
                          and (root_type != "event_msg"
                               or (payload_type is not None
                                   and payload_type != "token_count"))):
                        prefix = bytearray()
                        kind = "ignore"
                    elif len(prefix) >= header_limit:
                        prefix = bytearray()
                        kind = "ignore"

                if newline < 0:
                    break

                if candidate is not None:
                    yield "token", bytes(candidate)
                prefix = bytearray()
                candidate = None
                kind = None
                start = newline + 1

    if candidate is not None:
        raw_cand = bytes(candidate)
        try:
            json.loads(raw_cand.decode("utf-8", errors="ignore"))
            yield "token", raw_cand
        except Exception:
            pass


def _codex_complete_offset(path, size, chunk_size=64 * 1024):
    """Return the byte offset after the last complete JSONL record."""
    if size <= 0:
        return 0
    try:
        with open(path, "rb", buffering=0) as fh:
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return size
            position = size
            while position > 0:
                start = max(0, position - chunk_size)
                fh.seek(start)
                data = fh.read(position - start)
                newline = data.rfind(b"\n")
                if newline >= 0:
                    return start + newline + 1
                position = start
    except OSError:
        return 0
    return 0


def _codex_offset_guard(path, offset, guard_size=4096):
    if offset <= 0:
        return ""
    try:
        import hashlib
        with open(path, "rb", buffering=0) as fh:
            start = max(0, offset - guard_size)
            fh.seek(start)
            data = fh.read(offset - start)
        return hashlib.sha256(data).hexdigest()
    except OSError:
        return None


def _iter_codex_token_lines(path, chunk_size=64 * 1024, header_limit=4 * 1024):
    """Compatibility iterator for callers that only need token_count records."""
    for kind, value in _iter_codex_usage_records(path, chunk_size, header_limit):
        if kind == "token":
            yield value


def _codex_session_meta(path, max_lines=20, max_line_bytes=2 * 1024 * 1024):
    try:
        with open(path, "rb", buffering=0) as fh:
            for _ in range(max_lines):
                line = fh.readline(max_line_bytes)
                if not line:
                    break
                if b'"session_meta"' not in line:
                    continue
                try:
                    o = json.loads(line.decode("utf-8", errors="ignore"))
                except Exception:
                    continue
                if o.get("type") != "session_meta":
                    continue
                meta = o.get("payload") or {}
                parent_id = meta.get("forked_from_id") or meta.get("parent_thread_id")
                if not parent_id:
                    source = meta.get("source") or {}
                    subagent = source.get("subagent") if isinstance(source, dict) else None
                    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                    if isinstance(spawn, dict):
                        parent_id = spawn.get("parent_thread_id")
                return meta.get("id") or meta.get("session_id"), parent_id
    except OSError:
        pass
    return None, None


def _codex_rollout_files():
    roots = _existing_dirs(
        _path_candidates("TOKEI_CODEX_DIR", CODEX_DIR) +
        _path_candidates("TOKEI_CODEX_ARCHIVED_DIR", CODEX_ARCHIVED_DIR)
    )
    files = []
    seen = set()
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)):
            real = os.path.realpath(path)
            key = os.path.normcase(real)
            if key not in seen and os.path.isfile(real):
                seen.add(key)
                files.append(real)
    return files


def _codex_canonical_file_cache(file_cache):
    """Choose one complete physical copy for each logical Codex session."""
    canonical = {}
    selected = {}
    for file_path, entry in file_cache.items():
        if not isinstance(entry, dict):
            continue
        session_id = entry.get("session_id")
        logical_id = ("session", str(session_id)) if session_id else (
            "rollout", os.path.basename(file_path))
        events = entry.get("events") or []
        events = events if isinstance(events, list) else []
        event_count = int(entry.get("event_count", len(events)) or 0)
        event_timestamps = [str(event[0]) for event in events
                            if isinstance(event, list) and event]
        last_event_ts = str(entry.get("last_event_ts") or
                            max(event_timestamps, default=""))
        try:
            parsed_size = int(entry.get("parsed_size", 0) or 0)
        except (TypeError, ValueError):
            parsed_size = 0
        score = (event_count, last_event_ts, parsed_size)
        previous = selected.get(logical_id)
        if previous is not None and score <= previous[0]:
            continue
        if previous is not None:
            canonical.pop(previous[1], None)
        selected[logical_id] = (score, file_path)
        canonical[file_path] = entry
    return canonical


def scan_codex(bounds, cache, rollout_files=None):
    ledger_touch("codex")
    fc = cache.setdefault("codex", {})
    if _codex_migrate_event_cache(fc):
        cache["_dirty"] = True
    B = {k: {"in": 0, "cached": 0, "out": 0, "reason": 0, "cost": 0.0,
             "sessions": set(), "models": {}}
         for k in RANGE_KEYS}
    if rollout_files is None:
        rollout_files = _codex_rollout_files()
    if not rollout_files:
        if fc:
            _codex_clear_event_cache(fc)
            cache["_dirty"] = True
        return {"ranges": B, "cur_total": None, "limits": None, "plan": None,
                "limits_updated": None, "limits_consumed": None}

    today_d = bounds["today"].date()
    yest_d = bounds["yesterday"].date()
    week_d = bounds["week"].date()
    lw_start_d = bounds["last_week"].date()
    lw_end_d = bounds["last_week_end"].date()
    month_d = bounds["month"].date()
    year_d = bounds["year"].date()

    cur_file, cur_mtime = None, -1.0
    stale = set(fc.keys())
    dedupe_paths = set()
    active_root = os.path.realpath(CODEX_DIR) if os.path.isdir(CODEX_DIR) else None
    next_checkpoint = _time.monotonic() + _CODEX_SCAN_CHECKPOINT_INTERVAL

    for f in rollout_files:
        stale.discard(f)
        try:
            st = os.stat(f)
        except OSError:
            continue
        mtime, size = st.st_mtime, st.st_size
        try:
            is_active = active_root is not None and os.path.commonpath((f, active_root)) == active_root
        except ValueError:
            is_active = False
        if is_active and mtime > cur_mtime:
            cur_mtime = mtime
            cur_file = f
        sig = f"{st.st_mtime_ns}:{size}"
        entry = fc.get(f)
        event_cache_ready = _codex_event_cache_ready(f, entry)
        legacy_parser_cache = (
            isinstance(entry, dict)
            and entry.get("parser_version") is None
            and entry.get("model_version") == 2
            and event_cache_ready
        )
        legacy_cache_usable = legacy_parser_cache and (
            int(entry.get("event_count", 0) or 0) > 0 or size == 0)
        if legacy_cache_usable and entry.get("sig") == sig:
            entry["parser_version"] = _CODEX_PARSER_VERSION
            entry.pop("model_version", None)
            cache["_dirty"] = True
            continue
        if (not entry or entry.get("sig") != sig
                or entry.get("parser_version") != _CODEX_PARSER_VERSION
                or not event_cache_ready):
            complete_offset = _codex_complete_offset(f, size)
            file_id = f"{st.st_dev}:{st.st_ino}"
            append_from = None
            if (isinstance(entry, dict)
                    and (entry.get("parser_version") == _CODEX_PARSER_VERSION
                         or legacy_cache_usable)):
                old_offset = int(entry.get("parsed_size", 0) or 0)
                if (entry.get("file_id") == file_id and old_offset <= complete_offset
                        and entry.get("parsed_guard") == _codex_offset_guard(f, old_offset)
                        and event_cache_ready):
                    append_from = old_offset

            if append_from is None:
                events = []
                session_id, forked_from_id = _codex_session_meta(f)
                file_limits = None; file_limits_ts = None; file_plan = None
                file_g_limits = None; file_g_ts = None; file_g_plan = None
                file_last_total = None
                prev_total_key = None
                file_model = None
                parse_start = 0
            else:
                events = []
                session_id = entry.get("session_id")
                forked_from_id = entry.get("forked_from_id")
                file_limits = entry.get("limits"); file_limits_ts = entry.get("limits_ts")
                file_plan = entry.get("plan")
                file_g_limits = entry.get("g_limits"); file_g_ts = entry.get("g_ts")
                file_g_plan = entry.get("g_plan")
                file_last_total = entry.get("last_total")
                previous = entry.get("prev_total_key")
                prev_total_key = tuple(previous) if isinstance(previous, (list, tuple)) else None
                file_model = entry.get("active_model")
                parse_start = append_from

            try:
                for record_kind, record in _iter_codex_usage_records(
                        f, start_offset=parse_start, end_offset=complete_offset):
                    if record_kind == "model":
                        file_model = record
                        continue
                    try:
                        o = json.loads(record.decode("utf-8", errors="ignore"))
                    except Exception:
                        continue
                    ts = parse_ts(o.get("timestamp", ""))
                    if not ts:
                        continue
                    info = (o.get("payload") or {}).get("info") or {}
                    last = info.get("last_token_usage") or {}
                    total = info.get("total_token_usage") or {}
                    total_key = None
                    duplicate_total = False
                    if total:
                        total_key = (total.get("input_tokens", 0) or 0,
                                     total.get("cached_input_tokens", 0) or 0,
                                     total.get("output_tokens", 0) or 0,
                                     total.get("reasoning_output_tokens", 0) or 0)
                        duplicate_total = total_key == prev_total_key
                        prev_total_key = total_key
                        file_last_total = total
                    rl = (o.get("payload") or {}).get("rate_limits")
                    if ts and rl:
                        ts_iso = ts.isoformat()
                        if file_g_ts is None or ts_iso > file_g_ts:
                            file_g_ts = ts_iso
                            file_g_limits = rl
                            file_g_plan = rl.get("plan_type")
                        if rl.get("limit_id") == "codex" and (file_limits_ts is None or ts_iso > file_limits_ts):
                            file_limits_ts = ts_iso
                            file_limits = rl
                            file_plan = rl.get("plan_type")
                    # Codex may emit the same cumulative snapshot twice; in that case
                    # last_token_usage is repeated too, so counting it again overstates usage.
                    if ts and last and not duplicate_total:
                        dk = ts.astimezone().date().isoformat()
                        li = last.get("input_tokens", 0) or 0
                        lc = last.get("cached_input_tokens", 0) or 0
                        lo = last.get("output_tokens", 0) or 0
                        lr = last.get("reasoning_output_tokens", 0) or 0
                        # 无模型字段(老版本 CLI 日志/截断会话)标为 unknown,不冒充 gpt-5.5;
                        # 计费仍按 gpt-5.5 保守估算(下行 price_model 兜底)
                        model = _known_id_or_raw(file_model) or "unknown"
                        price_model = model if _has_known_price(model) else "openai/gpt-5.5"
                        cx_base = _raw_price(price_model)
                        hi = li > 272_000
                        p_in = cx_base["in"] * (2 if hi else 1)
                        p_out = cx_base["out"] * (1.5 if hi else 1)
                        p_cr = cx_base["cache_read"] * (2 if hi else 1)
                        cost = (li - lc) / 1e6 * p_in + lc / 1e6 * p_cr + lo / 1e6 * p_out
                        totals = total_key if total_key is not None else (None, None, None, None)
                        # timestamp, local day, cumulative usage, incremental usage, cost
                        events.append([ts.isoformat(), dk, *totals, li, lc, lo, lr, cost, model])
            except OSError:
                continue

            if append_from is None:
                event_cache_size = _codex_write_event_cache(f, events)
                metadata = _codex_event_metadata(events)
                deduped_days = {}
                drop_count = 0
                dedupe_open = True
                was_canonical = False
                dedupe_paths.add(f)
            else:
                event_cache_size = _codex_append_event_cache(
                    f, events, int(entry.get("event_cache_size", 0) or 0))
                metadata = {
                    "event_count": int(entry.get("event_count", 0) or 0) + len(events),
                    "first_keys": entry.get("first_keys") or [],
                    "first_event_ts": entry.get("first_event_ts"),
                    "last_event_ts": (
                        str(events[-1][0]) if events else entry.get("last_event_ts")
                    ),
                }
                if len(metadata["first_keys"]) < 2 and metadata["event_count"]:
                    prefix_events = list(_iter_codex_cached_events(f, limit=2))
                    prefix_metadata = _codex_event_metadata(prefix_events)
                    metadata["first_keys"] = prefix_metadata["first_keys"]
                    metadata["first_event_ts"] = prefix_metadata["first_event_ts"]
                deduped_days = entry.get("deduped_days")
                if not isinstance(deduped_days, dict):
                    deduped_days = dict(entry.get("days") or {})
                drop_count = int(entry.get("drop_count", 0) or 0)
                dedupe_open = bool(entry.get("dedupe_open"))
                was_canonical = bool(entry.get("canonical"))
                if dedupe_open:
                    dedupe_paths.add(f)
                else:
                    for event in events:
                        _codex_add_event(deduped_days, event)

            fc[f] = {
                "sig": sig, "days": entry.get("days", {}) if isinstance(entry, dict) else {},
                "deduped_days": deduped_days,
                "session_id": session_id, "forked_from_id": forked_from_id,
                "limits": file_limits, "limits_ts": file_limits_ts, "plan": file_plan,
                "g_limits": file_g_limits, "g_ts": file_g_ts, "g_plan": file_g_plan,
                "last_total": file_last_total, "prev_total_key": prev_total_key,
                "active_model": file_model, "parser_version": _CODEX_PARSER_VERSION,
                "file_id": file_id, "parsed_size": complete_offset,
                "parsed_guard": _codex_offset_guard(f, complete_offset),
                "event_cache_size": event_cache_size,
                "event_count": metadata["event_count"],
                "first_keys": metadata["first_keys"],
                "first_event_ts": metadata["first_event_ts"],
                "last_event_ts": metadata["last_event_ts"],
                "drop_count": drop_count, "dedupe_open": dedupe_open,
                "canonical": was_canonical,
            }
            cache["_dirty"] = True
            if _time.monotonic() >= next_checkpoint:
                _save_scan_cache(cache)
                next_checkpoint = _time.monotonic() + _CODEX_SCAN_CHECKPOINT_INTERVAL

    for p in stale:
        fc.pop(p, None)
        _codex_remove_event_cache(p)
        cache["_dirty"] = True

    # A session can briefly exist in active and archived directories together.
    # Select the more complete copy before applying fork/replay deduplication.
    canonical_fc = _codex_canonical_file_cache(fc)
    for f, entry in fc.items():
        is_canonical = f in canonical_fc
        if is_canonical and not entry.get("canonical"):
            dedupe_paths.add(f)
        if entry.get("canonical") != is_canonical:
            entry["canonical"] = is_canonical
            cache["_dirty"] = True

    for f in dedupe_paths:
        entry = canonical_fc.get(f)
        if entry is None:
            continue
        try:
            drop_count, dedupe_open = _codex_cached_drop_count(f, entry, canonical_fc)
            deduped_days = _codex_days_from_cached_events(
                f, start_index=drop_count, event_count=entry.get("event_count"))
        except OSError:
            _codex_clear_event_cache(fc)
            cache["_dirty"] = True
            raise
        if (entry.get("drop_count") != drop_count or
                entry.get("dedupe_open") != dedupe_open or
                entry.get("deduped_days") != deduped_days):
            entry["drop_count"] = drop_count
            entry["dedupe_open"] = dedupe_open
            entry["deduped_days"] = deduped_days
            cache["_dirty"] = True

    for f, entry in fc.items():
        days = entry.get("deduped_days", {}) if f in canonical_fc else {}
        if entry.get("days") != days:
            entry["days"] = days
            cache["_dirty"] = True

    # Assembly: per-day → range buckets
    def _codex_range_keys(d):
        return classify_date(d, bounds)

    live_days = {}
    for f, entry in canonical_fc.items():
        for dk, day in entry.get("days", {}).items():
            d = date.fromisoformat(dk)
            agg = live_days.setdefault(
                dk, {"in": 0, "cached": 0, "out": 0, "reason": 0,
                     "cost": 0.0, "models": {}, "hours": [0] * 24})
            agg["in"] += day["in"]; agg["cached"] += day["cached"]
            agg["out"] += day["out"]; agg["reason"] += day["reason"]
            agg["cost"] += day["cost"]
            for model, usage in day.get("models", {}).items():
                _add_model_usage(agg["models"], model, usage.get("in", 0), usage.get("out", 0),
                                 usage.get("cr", 0), usage.get("cw", 0),
                                 usage.get("reason", 0), usage.get("cost", 0))
            for hour, amount in enumerate((day.get("hours") or [])[:24]):
                agg["hours"][hour] += amount
            # 会话数只能来自现存日志(被清日志无从归属)
            for k in _codex_range_keys(d):
                B[k]["sessions"].add(f)

    merged_days = ledger_reconcile("codex", live_days)
    for dk, day in merged_days.items():
        try:
            d = date.fromisoformat(dk)
        except ValueError:
            continue
        for k in _codex_range_keys(d):
            b = B[k]
            b["in"] += day.get("in", 0); b["cached"] += day.get("cached", 0)
            b["out"] += day.get("out", 0); b["reason"] += day.get("reason", 0)
            b["cost"] += day.get("cost", 0.0)
            for model, usage in (day.get("models") or {}).items():
                _add_model_usage(b["models"], model, usage.get("in", 0), usage.get("out", 0),
                                 usage.get("cr", 0), usage.get("cw", 0),
                                 usage.get("reason", 0), usage.get("cost", 0))

    # Find latest limits across all cached files
    latest_limits = None; latest_ts = None; plan_type = None
    g_limits = None; g_ts = None
    for entry in fc.values():
        if entry.get("limits_ts"):
            if latest_ts is None or entry["limits_ts"] > latest_ts:
                latest_ts = entry["limits_ts"]
                latest_limits = entry["limits"]
                plan_type = entry["plan"]
        if entry.get("g_ts"):
            if g_ts is None or entry["g_ts"] > g_ts:
                g_ts = entry["g_ts"]
                g_limits = entry["g_limits"]

    selected_limits_ts = latest_ts
    if latest_limits is None and g_limits is not None:
        latest_limits = g_limits
        plan_type = (g_limits or {}).get("plan_type")
        selected_limits_ts = g_ts

    # 读数时间:live 真正胜出时用抓取时刻,否则用日志里那条记录的时间。
    # live_updated 只在 if live 分支内有定义,先在外面兜底。
    limits_updated = _iso_to_epoch(selected_limits_ts)
    live = fetch_codex_live_limits()
    if live:
        live_limits, live_plan, live_updated = live
        if _codex_live_snapshot_is_current(live_updated, selected_limits_ts):
            latest_limits = live_limits
            plan_type = live_plan or (live_limits or {}).get("plan_type") or plan_type
            limits_updated = int(live_updated)

    # 窗口翻篇后本机又消耗了多少 —— 用来区分「确实回满了」和「读数已经失真」。
    # now_epoch=0 让映射函数只做槽位归类,不触发过期处理。
    slots = _codex_quota_values(latest_limits, now_epoch=0)
    limits_consumed = {
        "p5": _codex_used_since(merged_days, slots["r5"]),
        "pw": _codex_used_since(merged_days, slots["rw"]),
    }

    # For third-party providers the official OpenAI quota is not meaningful,
    # and stale limits from older sessions must not be shown.
    if _codex_is_custom_provider():
        latest_limits = None
        plan_type = None

    cur_total = None
    if cur_file:
        entry = fc.get(cur_file)
        if entry:
            cur_total = entry.get("last_total")

    return {
        "ranges": B,
        "cur_total": cur_total,
        "limits": latest_limits,
        "plan": plan_type,
        "limits_updated": limits_updated,
        "limits_consumed": limits_consumed,
    }


def _codex_used_since(days, since_epoch):
    """since_epoch 之后本机消耗的 codex token;拿不到就返回 None。

    账本里 in 已含 cached,所以口径是 in+out(与 hours 一致)。起始那天按 hours[24]
    从重置小时切起;宁可把重置那个整点全算进来,也不要漏报消耗——漏报会让一份
    已经失真的额度读数被当成"还满着"。
    """
    if not isinstance(days, dict) or not since_epoch:
        return None
    try:
        start = datetime.fromtimestamp(float(since_epoch))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    start_day = start.date().isoformat()
    total = 0
    for dk, day in days.items():
        if not isinstance(day, dict) or dk < start_day:
            continue
        whole_day = int(day.get("in", 0) or 0) + int(day.get("out", 0) or 0)
        if dk > start_day:
            total += whole_day
            continue
        hours = day.get("hours") or []
        # 没有小时分布(老账本条目)就整天算,保守方向是宁多勿少
        total += sum(int(h or 0) for h in hours[start.hour:24]) if hours else whole_day
    return total


def _codex_quota_values(limits, now_epoch=None, consumed=None):
    """Map Codex rate-limit slots by duration; primary/secondary roles can change.

    consumed = {"p5": n, "pw": n}:该窗口 resets_at 之后本机又消耗了多少 token。
    """
    values = {"p5": None, "pw": None, "r5": None, "rw": None,
              "p5_stale": False, "pw_stale": False}
    for slot_name in ("primary", "secondary"):
        slot = (limits or {}).get(slot_name) or {}
        if not slot:
            continue
        minutes = slot.get("window_minutes")
        # Older logs use primary=5h and secondary=7d. Newer plans may expose
        # the 7d window as primary with no secondary, so duration is canonical.
        is_week = minutes == 7 * 24 * 60 or (minutes is None and slot_name == "secondary")
        pct_key, reset_key = ("pw", "rw") if is_week else ("p5", "r5")
        values[pct_key] = slot.get("used_percent")
        values[reset_key] = slot.get("resets_at")

    now_epoch = now_epoch if now_epoch is not None else int(datetime.now().timestamp())
    for pct_key, reset_key in (("p5", "r5"), ("pw", "rw")):
        reset = values[reset_key]
        if not reset or now_epoch <= reset:
            continue
        # 窗口已经翻篇。此后一个 token 都没用 = 确实回满了;用过 = 这份读数已经
        # 失真,标出来让界面说"已过期"。谎报满额比承认不知道危险得多(issue #63)。
        if (consumed or {}).get(pct_key) == 0:
            values[pct_key] = 0.0
            values[reset_key] = None
        elif values[pct_key] is not None:
            values[f"{pct_key}_stale"] = True
    return values

