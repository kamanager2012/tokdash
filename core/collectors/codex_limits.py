"""Codex live rate limits, authentication context, and reset cards.

Handles official ChatGPT backend-api usage and rate-limit-reset-credits endpoints,
with low-frequency cached tokens and JWT claim decoding.
"""

import os
import re
import json
import base64
import hashlib
from datetime import datetime

from core.config import (
    CODEX_AUTH,
    CODEX_CONFIG,
    CODEX_QUOTA_CACHE,
    CODEX_RESET_CARDS_CACHE,
    _atomic_write_json,
    _load_json,
    parse_ts,
)
from core.collectors.claude import _iso_to_epoch

_CODEX_QUOTA_TTL = 300
_CODEX_QUOTA_FALLBACK_TTL = 300
_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_USAGE_MAX_RESPONSE_BYTES = 256 * 1024
_CODEX_RESET_CARDS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
_CODEX_RESET_CARDS_REFRESH_INTERVAL = 24 * 3600
_CODEX_RESET_CARDS_RETRY_INTERVAL = 6 * 3600
_CODEX_RESET_CARDS_MAX_RESPONSE_BYTES = 256 * 1024


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


__all__ = [
    "_CODEX_QUOTA_TTL",
    "_CODEX_QUOTA_FALLBACK_TTL",
    "_CODEX_USAGE_URL",
    "_CODEX_USAGE_MAX_RESPONSE_BYTES",
    "_CODEX_RESET_CARDS_URL",
    "_CODEX_RESET_CARDS_REFRESH_INTERVAL",
    "_CODEX_RESET_CARDS_RETRY_INTERVAL",
    "_CODEX_RESET_CARDS_MAX_RESPONSE_BYTES",
    "_window_from_codex_live",
    "_codex_live_to_limits",
    "_codex_limits_have_active_window",
    "_cached_codex_live_limits",
    "_codex_live_snapshot_is_current",
    "_decode_jwt_claims",
    "_codex_config",
    "_codex_is_custom_provider",
    "_codex_auth_context",
    "fetch_codex_live_limits",
    "_normalize_codex_reset_cards",
    "_cached_codex_reset_cards",
    "_codex_reset_cards_next_attempt",
    "_save_codex_reset_cards_state",
    "fetch_codex_reset_cards",
]
