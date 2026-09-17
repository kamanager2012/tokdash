"""Core configuration, filesystem paths, time ranges, and common utilities for Cognitally."""

import os
import re
import sys
import json
import tempfile
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------- Base Paths & OS Profiles ----------
HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")

# Repo root directory
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = ROOT_DIR

_CONFIG_BASE = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
_COGNITALLY_DIR = os.path.join(_CONFIG_BASE, "cognitally")
_LEGACY_TOKDASH_DIR = os.path.join(_CONFIG_BASE, "tokdash")
_LEGACY_TOKEI_DIR = os.path.join(HOME, ".tokei")

_USER_DIR = (
    os.environ.get("COGNITALLY_DIR")
    or os.environ.get("TOKDASH_DIR")
    or os.environ.get("TOKEI_DIR")
    or _COGNITALLY_DIR
)


def _expand_path(path):
    if not path:
        return None
    value = os.fspath(path).strip()
    return os.path.abspath(os.path.expandvars(os.path.expanduser(value))) if value else None


def _path_candidates(env_name, *defaults):
    values = []
    configured = os.environ.get(env_name, "")
    if configured:
        values.extend(configured.split(os.pathsep))
    values.extend(defaults)
    result = []
    seen = set()
    for value in values:
        path = _expand_path(value)
        if not path:
            continue
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _first_existing_file(paths):
    return next((path for path in paths if os.path.isfile(path)), None)


def _existing_dirs(paths):
    result = []
    seen = set()
    for path in paths:
        if not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        key = os.path.normcase(real)
        if key not in seen:
            seen.add(key)
            result.append(real)
    return result


def _writable_path(name):
    """优先用 ~/.config/cognitally/ 下的可写副本, 兼容 legacy ~/.config/tokdash 与 ~/.tokei, 没有则用脚本同目录(开发模式)。"""
    user = os.path.join(_USER_DIR, name)
    if os.path.isfile(user):
        return user
    legacy_td = os.path.join(_LEGACY_TOKDASH_DIR, name)
    if os.path.isfile(legacy_td):
        return legacy_td
    legacy_tk = os.path.join(_LEGACY_TOKEI_DIR, name)
    if os.path.isfile(legacy_tk):
        return legacy_tk
    base = os.path.join(BASE_DIR, name)
    if os.path.isfile(base):
        if ".app/" in BASE_DIR:
            os.makedirs(_USER_DIR, exist_ok=True)
            import shutil
            shutil.copy2(base, user)
            return user
        return base
    return os.path.join(_USER_DIR, name)


def _load_json(path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_json(path, data):
    target_dir = os.path.dirname(path)
    if target_dir:
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=target_dir or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


# ---------- Agent Local Storage Directories ----------
CLAUDE_DIR = os.path.join(HOME, ".claude", "projects")
CODEX_DIR = os.path.join(HOME, ".codex", "sessions")
CODEX_ARCHIVED_DIR = os.path.join(HOME, ".codex", "archived_sessions")
CODEX_AUTH = os.path.join(HOME, ".codex", "auth.json")
CODEX_CONFIG = os.path.join(HOME, ".codex", "config.toml")
GEMINI_DIR = os.path.join(HOME, ".gemini", "tmp")
ANTIGRAVITY_DIR = os.path.join(HOME, ".gemini", "antigravity-cli", "conversations")
GEMINI_DIRS = _path_candidates(
    "TOKEI_GEMINI_DIR", GEMINI_DIR,
    ANTIGRAVITY_DIR,
    os.path.join(HOME, ".gemini", "antigravity", "conversations"),
    os.path.join(HOME, ".gemini", "antigravity-ide", "conversations"),
    os.path.join(HOME, ".gemini", "gemini-cli", "conversations"),
    *([os.environ["TOKEI_ANTIGRAVITY_DIR"]] if "TOKEI_ANTIGRAVITY_DIR" in os.environ else [])
)

GROK_HOME = os.path.abspath(os.path.expanduser(
    os.environ.get("GROK_HOME", os.path.join(HOME, ".grok"))))
GROK_DIR = os.path.join(GROK_HOME, "sessions")
GROK_LOG = os.path.join(GROK_HOME, "logs", "unified.jsonl")
GROK_AUTH = os.path.join(GROK_HOME, "auth.json")

WORKBUDDY_DIR = os.path.join(HOME, ".workbuddy", "projects")
WORKBUDDY_AI_DIR = os.path.join(HOME, ".workbuddy-ai", "projects")
CODEBUDDY_DIR = os.path.join(HOME, ".codebuddy", "projects")

GROK_BOT_DIRS = _path_candidates(
    "TOKEI_GROK_BOT_DIR",
    os.path.join(HOME, "Library", "Application Support", "Grok Bot", "sand-client-persistence"),
    os.path.join(APPDATA, "Grok Bot", "sand-client-persistence"),
    os.path.join(HOME, ".config", "Grok Bot", "sand-client-persistence")
)
GROK_BOT_SECRET_PATHS = _path_candidates(
    "TOKEI_GROK_BOT_SECRETS",
    os.path.join(HOME, "Library", "Application Support", "Grok Bot", "sand-secrets.json"),
    os.path.join(APPDATA, "Grok Bot", "sand-secrets.json"),
    os.path.join(HOME, ".config", "Grok Bot", "sand-secrets.json")
)
GROK_BOT_AUTH_MARKER = _expand_path(os.environ.get(
    "TOKEI_GROK_BOT_AUTH_MARKER",
    os.path.join(HOME, ".tokei", "grok_bot_keychain_authorized")
))

DEEPSEEK_HARNESS_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "TOKEI_DSH_DECOMPRESSED_DIR", os.path.join(HOME, ".tokei", "cache", "dsh-sessions")
)))

QODER_IDE_DB = os.path.join(HOME, "Library", "Application Support", "Qoder",
                            "SharedClientCache", "cache", "db", "local.db")
QODER_IDE_DB_PATHS = _path_candidates(
    "TOKEI_QODER_IDE_DB", QODER_IDE_DB,
    os.path.join(HOME, ".config", "Qoder", "SharedClientCache", "cache", "db", "local.db"),
    os.path.join(APPDATA, "Qoder", "SharedClientCache", "cache", "db", "local.db"),
    os.path.join(LOCALAPPDATA, "Qoder", "SharedClientCache", "cache", "db", "local.db")
)


def _qoder_ide_db_path():
    return _first_existing_file(
        _path_candidates("TOKEI_QODER_IDE_DB", QODER_IDE_DB, *QODER_IDE_DB_PATHS)
    )


HERMES_DB = os.path.join(HOME, ".hermes", "state.db")
OPENCODE_DATA_DIR = os.path.expanduser(os.environ.get(
    "OPENCODE_DATA_DIR", os.path.join(HOME, ".local", "share", "opencode")
))
OPENCODE_DIR = os.path.join(OPENCODE_DATA_DIR, "storage", "message")
OPENCODE_DB = os.path.join(OPENCODE_DATA_DIR, "opencode.db")
OPENCODE_DATA_DIRS = _path_candidates(
    "TOKEI_OPENCODE_DATA_DIR", OPENCODE_DATA_DIR,
    os.path.join(APPDATA, "opencode"), os.path.join(LOCALAPPDATA, "opencode")
)

ZCODE_DB = os.path.abspath(os.path.expanduser(os.environ.get(
    "TOKEI_ZCODE_DB", os.path.join(HOME, ".zcode", "cli", "db", "db.sqlite")
)))

PI_AGENT_DIR = os.path.expanduser(os.environ.get("PI_CODING_AGENT_DIR", os.path.join(HOME, ".pi", "agent")))
PI_SESSION_DIR = os.path.expanduser(os.environ.get("PI_CODING_AGENT_SESSION_DIR", os.path.join(PI_AGENT_DIR, "sessions")))
OMP_SESSION_DIR = os.path.expanduser(os.environ.get(
    "OMP_CODING_AGENT_SESSION_DIR", os.path.join(HOME, ".omp", "agent", "sessions")
))

_KIMI_CODE_DEFAULT_DIR = os.path.join(HOME, ".kimi-code")
_KIMI_CODE_LEGACY_DIR = os.path.join(HOME, ".kimi")
KIMI_CODE_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("TOKEI_KIMI_DIR") or os.environ.get("KIMI_CODE_HOME")
    or os.environ.get("KIMI_SHARE_DIR") or _KIMI_CODE_DEFAULT_DIR
))

# ---------- Pricing & Quota Cache Paths ----------
PRICING_FILE = _writable_path("pricing.json")
OVERRIDES_FILE = _writable_path("pricing_overrides.json")
CODEX_QUOTA_CACHE = _writable_path("codex_quota_cache.json")
CODEX_RESET_CARDS_CACHE = _writable_path("codex_reset_cards_cache.json")
CLAUDE_QUOTA_CACHE = _writable_path("claude_quota_cache.json")
GROK_QUOTA_CACHE = _writable_path("grok_quota_cache.json")
PROVIDER_QUOTA_CACHE = _writable_path("provider_quota_cache.json")
ANTIGRAVITY_SCAN_CACHE = _writable_path("antigravity_scan_cache.json")

# ---------- Incremental Scan & Snapshot Caches ----------
_LEGACY_SCAN_CACHE_FILE = os.path.join(
    tempfile.gettempdir(), "_tokei_scan_cache.json"
)
_PREV_TOKEI_CACHE_FILE = os.path.join(
    HOME, ".tokei", "cache", "scan_cache.json"
)
_SCAN_CACHE_DIR = (
    os.environ.get("COGNITALLY_CACHE_DIR")
    or os.environ.get("TOKDASH_CACHE_DIR")
    or os.environ.get("TOKEI_CACHE_DIR")
    or _COGNITALLY_DIR
)
_DEFAULT_SCAN_CACHE_FILE = os.path.join(_SCAN_CACHE_DIR, "scan_cache.json")
_SCAN_CACHE_FILE = _DEFAULT_SCAN_CACHE_FILE

_GEMINI_DAYS_CACHE_KEY = "_gemini_dashboard_days"
_GROK_DAYS_CACHE_KEY = "_grok_dashboard_days"
_CURSOR_PROVIDER_DAYS_CACHE_KEY = "_cursor_provider_days"
_ZAI_PROVIDER_DAYS_CACHE_KEY = "_zai_provider_days"
_GROK_BOT_PROVIDER_DAYS_CACHE_KEY = "_grok_bot_provider_days"

_SNAPSHOT_CACHE_FILE = os.path.join(_SCAN_CACHE_DIR, "canonical_snapshot.json")
_SNAPSHOT_LOCK_FILE = os.path.join(_SCAN_CACHE_DIR, "canonical_snapshot.lock")
_SNAPSHOT_TTL = 25.0



def _migrate_legacy_cognitally_state():
    """One-time migration from ~/.config/tokdash and ~/.tokei to ~/.config/cognitally."""
    if _USER_DIR != _COGNITALLY_DIR and _SCAN_CACHE_DIR != _COGNITALLY_DIR:
        return
    marker = os.path.join(_COGNITALLY_DIR, ".migration_done")
    if os.path.exists(marker):
        return
    import shutil
    try:
        os.makedirs(_COGNITALLY_DIR, mode=0o700, exist_ok=True)
        files_to_check = [
            ("scan_cache.json", [
                os.path.join(_LEGACY_TOKDASH_DIR, "scan_cache.json"),
                os.path.join(_LEGACY_TOKEI_DIR, "cache", "scan_cache.json"),
                _LEGACY_SCAN_CACHE_FILE
            ]),
            ("ledger.json", [
                os.path.join(_LEGACY_TOKDASH_DIR, "ledger.json"),
                os.path.join(_LEGACY_TOKEI_DIR, "ledger.json")
            ]),
            ("config.json", [
                os.path.join(_LEGACY_TOKDASH_DIR, "config.json"),
                os.path.join(_LEGACY_TOKEI_DIR, "config.json")
            ]),
            ("pricing.json", [
                os.path.join(_LEGACY_TOKDASH_DIR, "pricing.json"),
                os.path.join(_LEGACY_TOKEI_DIR, "pricing.json")
            ]),
            ("pricing_overrides.json", [
                os.path.join(_LEGACY_TOKDASH_DIR, "pricing_overrides.json"),
                os.path.join(_LEGACY_TOKEI_DIR, "pricing_overrides.json")
            ]),
        ]
        for filename, candidates in files_to_check:
            target = os.path.join(_COGNITALLY_DIR, filename)
            if not os.path.exists(target):
                for cand in candidates:
                    if cand and os.path.isfile(cand):
                        try:
                            shutil.copyfile(cand, target)
                            os.chmod(target, 0o600)
                            break
                        except OSError:
                            pass
        with open(marker, "w", encoding="utf-8") as mf:
            mf.write(f"Migrated at {datetime.now().astimezone().isoformat()}\n")
    except Exception:
        pass


# ---------- Range Keys & Token Fields ----------
RANGE_KEYS = ["today", "yesterday", "week", "last_week", "7d", "30d", "month", "year", "all"]
TOKEN_FIELDS = ("in", "out", "cr", "cw", "reason")
_LEDGER_TOKEN_FIELDS = ("in", "out", "cr", "cw", "reason", "thoughts")


# ---------- Date & Range Helpers ----------
def range_bounds():
    """返回今日/昨日/近7天/本周(周一起)/近30天/本月(1号起)/本年(1月1日起)的本地起点。"""
    now = datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    d7 = today - timedelta(days=6)
    d30 = today - timedelta(days=29)
    week = today - timedelta(days=today.weekday())   # 周一 0
    last_week_start = week - timedelta(days=7)       # 上周一
    month = today.replace(day=1)
    year = today.replace(month=1, day=1)
    return {"today": today, "yesterday": yesterday, "7d": d7, "30d": d30, "week": week,
            "last_week": last_week_start, "last_week_end": week, "month": month, "year": year}


def range_boundaries():
    """同步用:明确每个相对时间范围的日期边界,避免设备间按过期 range 误合并。"""
    b = range_bounds()
    next_month = (b["month"].replace(day=28) + timedelta(days=4)).replace(day=1)
    next_year = b["year"].replace(year=b["year"].year + 1)

    def day_s(dt):
        return dt.date().isoformat()

    return {
        "today": {"start": day_s(b["today"]), "end": day_s(b["today"] + timedelta(days=1))},
        "yesterday": {"start": day_s(b["yesterday"]), "end": day_s(b["today"])},
        "7d": {"start": day_s(b["7d"]), "end": day_s(b["today"] + timedelta(days=1))},
        "30d": {"start": day_s(b["30d"]), "end": day_s(b["today"] + timedelta(days=1))},
        "week": {"start": day_s(b["week"]), "end": day_s(b["week"] + timedelta(days=7))},
        "last_week": {"start": day_s(b["last_week"]), "end": day_s(b["week"])},
        "month": {"start": day_s(b["month"]), "end": day_s(next_month)},
        "year": {"start": day_s(b["year"]), "end": day_s(next_year)},
        "all": {"start": None, "end": None},
    }


def classify(dt, b):
    """给定本地化 dt,返回它命中的区间 key 列表(今日同时属本周/本月/本年)。"""
    return classify_date(dt.date(), b)


def classify_date(d, b):
    """给定本地日期,返回它命中的区间 key 列表。"""
    ks = ["all"]
    if d == b["today"].date():
        ks.append("today")
    if d == b["yesterday"].date():
        ks.append("yesterday")
    if "7d" in b and d >= b["7d"].date():
        ks.append("7d")
    if "30d" in b and d >= b["30d"].date():
        ks.append("30d")
    if d >= b["week"].date():
        ks.append("week")
    if b["last_week"].date() <= d < b["last_week_end"].date():
        ks.append("last_week")
    if d >= b["month"].date():
        ks.append("month")
    if d >= b["year"].date():
        ks.append("year")
    return ks


def parse_ts(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def human(n: float) -> str:
    n = float(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(int(n))


def token_total(day):
    return sum(day.get(k, 0) for k in TOKEN_FIELDS)


def _sqlite_ro_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def _sqlite_signature(path):
    parts = []
    # SHM 的 mtime 会被只读 SQLite 连接更新，不能作为数据变化信号。
    for candidate in (path, path + "-wal"):
        try:
            stat = os.stat(candidate)
        except OSError:
            continue
        parts.append(f"{candidate}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts) or None


# ---------- Bucket Generators ----------
def _empty_token_bucket():
    return {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
            "cost": 0.0, "sessions": set(), "models": {}}


def _empty_token_day():
    return {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
            "cost": 0.0, "models": {}, "hours": [0] * 24}


def _empty_token_ranges():
    return {k: _empty_token_bucket() for k in RANGE_KEYS}


def _empty_claude():
    ranges = {k: {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0,
                  "models": {}, "sessions": set()} for k in RANGE_KEYS}
    return {"ranges": ranges, "cur": {"in": 0, "out": 0, "cr": 0, "cw": 0, "name": "-"}}


def _empty_codex():
    ranges = {k: {"in": 0, "cached": 0, "out": 0, "reason": 0,
                  "cost": 0.0, "sessions": set(), "models": {}} for k in RANGE_KEYS}
    return {"ranges": ranges, "limits": None, "plan": None,
            "limits_updated": None, "limits_consumed": None}


def _empty_gemini():
    ranges = {k: {"in": 0, "out": 0, "cached": 0, "thoughts": 0,
                  "cost": 0.0, "models": {}, "sessions": set()} for k in RANGE_KEYS}
    return {"ranges": ranges, "days": {}}


def _empty_grok():
    ranges = {k: {"tokens": 0, "in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
                  "cost": 0.0, "models": {}, "usage_sessions": set(), "usage_calls": 0,
                  "sessions": set(), "turns": 0, "tools": 0,
                  "duration": 0, "ctx_used": 0, "ctx_window": 0, "errors": 0,
                  "cancellations": 0, "ttft_sum": 0, "response_sum": 0, "latency_count": 0}
              for k in RANGE_KEYS}
    return {"ranges": ranges, "model": None, "days": {}}


def _empty_qoder():
    ranges = {k: {"in": 0, "out": 0, "sessions": 0, "calls": 0, "sub_agents": 0,
                  "duration": 0, "turns": 0, "ctx_sum": 0.0, "ctx_count": 0} for k in RANGE_KEYS}
    return {"ranges": ranges, "model": None}


def _empty_hermes():
    ranges = {k: {"in": 0, "out": 0, "cr": 0, "cw": 0, "reason": 0,
                  "cost": 0.0, "sessions": 0, "models": {}} for k in RANGE_KEYS}
    return {"ranges": ranges}


def _empty_opencode():
    return {"ranges": _empty_token_ranges()}


def _empty_pi():
    return _empty_opencode()


def _empty_workbuddy():
    return _empty_opencode()


def _empty_grok_bot():
    ranges = {key: {"sessions": set(), "calls": 0, "turns": 0,
                    "tools": 0, "duration": 0}
              for key in RANGE_KEYS}
    return {"ranges": ranges}


def _empty_deepseek_harness():
    return _empty_opencode()


def _empty_kimicode():
    return _empty_opencode()


def _empty_zcode():
    return _empty_opencode()
