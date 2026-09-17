import os
import sys
import glob
import json
import urllib.request
from datetime import datetime

from core.config import (
    PRICING_FILE,
    OVERRIDES_FILE,
    CLAUDE_DIR,
    WORKBUDDY_DIR,
    WORKBUDDY_AI_DIR,
    _SCAN_CACHE_FILE,
    _load_json,
)
from core.pricing.catalog import (
    _DEFAULT_PRICES,
    _PRICING_DB,
    _OVERRIDES,
    _OV_MODELS,
    _OV_ALIASES,
    _FAMILY,
    _normalize,
    _resolve_id,
    _raw_price,
)
from core.storage import _remove_codex_event_cache_dir
from core.collectors.gemini import _gemini_session_files, _load_gemini_usage_file
from core.collectors.misc import _pi_session_dirs, _pi_model_id


def update_prices():
    url = "https://raw.githubusercontent.com/tokei-app/pricing/main/prices.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Tokei/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            print("❌ 远程数据格式错误: 期望顶层对象")
            return
        os.makedirs(os.path.dirname(PRICING_FILE), exist_ok=True)
        with open(PRICING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _PRICING_DB.clear()
        _PRICING_DB.update(data)
        print(f"✅ 已更新价格表 → {PRICING_FILE}")
        print(f"   版本: {data.get('_meta', {}).get('version', '?')}, 共 {len(data) - 1} 个模型/规则")
        if os.path.exists(_SCAN_CACHE_FILE):
            os.remove(_SCAN_CACHE_FILE)
            _remove_codex_event_cache_dir()
            print("   已清除扫描缓存, 下次刷新将以新价格全量重算")
    except Exception as e:
        print(f"❌ 更新失败: {e}", file=sys.stderr)


def _scan_local_models():
    models = set()
    for f in glob.glob(os.path.join(CLAUDE_DIR, "**", "*.jsonl"), recursive=True):
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"model"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                        m = o.get("message", {}).get("model")
                        if m:
                            models.add(str(m))
                    except Exception:
                        pass
        except OSError:
            pass

    for f in _gemini_session_files():
        try:
            parsed = _load_gemini_usage_file(f)
            for day in parsed.get("days", {}).values():
                for model in day.get("models", {}).keys():
                    models.add(str(model))
        except OSError:
            pass

    for session_dir in _pi_session_dirs():
        for f in glob.glob(os.path.join(session_dir, "*.jsonl")):
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if '"model"' not in line:
                            continue
                        try:
                            item = json.loads(line)
                            m = _pi_model_id(item)
                            if m:
                                models.add(str(m))
                        except Exception:
                            pass
            except OSError:
                pass

    for root in (WORKBUDDY_DIR, WORKBUDDY_AI_DIR):
        for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        if '"providerData"' not in line and '"requestModel"' not in line:
                            continue
                        try:
                            item = json.loads(line)
                            provider = item.get("providerData") or (item.get("message") or {}).get("providerData") or {}
                            model = (provider.get("requestModelName") or provider.get("requestModelId")
                                     or provider.get("model"))
                            if model:
                                models.add(str(model))
                        except Exception:
                            pass
            except OSError:
                pass
    return models


def _is_exact_match(model: str):
    s = (model or "").strip()
    if s in _OV_MODELS:
        return True
    if s in _DEFAULT_PRICES:
        return True
    norm = _normalize(s)
    if norm in _PRICING_DB:
        return True
    for pat in _PRICING_DB.keys():
        if "*" in pat and _normalize(pat.strip("*")) in norm:
            return True
    return False


def _estimate_from_sibling(model: str, family: str, ref_dict: dict):
    m = _normalize(model)
    low = m.lower()
    candidates = []
    for cid, p in ref_dict.items():
        if cid.startswith("_"):
            continue
        c_fam = _FAMILY.get(_resolve_id(cid))
        if c_fam != family:
            continue
        c_low = cid.lower()
        family_match = ("claude" in low and "claude" in c_low) or \
                       ("gpt" in low and "gpt" in c_low) or \
                       ("gemini" in low and "gemini" in c_low) or \
                       ("deepseek" in low and "deepseek" in c_low) or \
                       ("grok" in low and "grok" in c_low) or \
                       ("qwen" in low and "qwen" in c_low)
        if not family_match:
            continue
        kw_score = 0
        for kw in ("opus", "sonnet", "haiku", "flash", "pro", "mini", "max",
                   "plus", "preview", "thinking", "reasoning", "fast"):
            if (kw in low) == (kw in c_low):
                kw_score += 1
        candidates.append((kw_score, cid, p))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: -x[0])
    best_score, best_cid, best_p = candidates[0]
    return best_cid, best_p


def update_unknown(apply: bool = False):
    scanned = _scan_local_models()
    if not scanned:
        print("ℹ️ 本地日志中未发现任何模型记录")
        return
    unknown = []
    for m in sorted(scanned):
        if not _is_exact_match(m):
            unknown.append(m)
    if not unknown:
        print(f"✅ 全部本地模型 ({len(scanned)} 个) 均已精准匹配价格表, 无未知模型")
        return
    print(f"🔍 扫描到 {len(scanned)} 个模型, 其中 {len(unknown)} 个无精确匹配:")
    print("-" * 60)
    added = {}
    for m in unknown:
        fam = _FAMILY.get(m)
        if not fam:
            norm = _normalize(m).lower()
            for k, f in (("claude", "anthropic"), ("gpt", "openai"), ("o1", "openai"),
                        ("o3", "openai"), ("o4", "openai"), ("gemini", "google"),
                        ("deepseek", "deepseek"), ("grok", "xai"), ("qwen", "alibaba"),
                        ("kimi", "moonshot"), ("glm", "zhipu"), ("minimax", "minimax")):
                if k in norm:
                    fam = f
                    break
        ref = _PRICING_DB if _PRICING_DB else _DEFAULT_PRICES
        best_cid, best_p = _estimate_from_sibling(m, fam, ref) if fam else (None, None)
        if best_cid and best_p:
            est = {"in": best_p.get("in", 0), "out": best_p.get("out", 0),
                   "cache_read": best_p.get("cache_read", 0), "cache_write": best_p.get("cache_write", 0)}
            added[m] = est
            print(f"  ? {m}")
            print(f"    → 推测参考: {best_cid} (family: {fam})")
            print(f"    → 建议定价: in=${est['in']}, out=${est['out']}, "
                  f"cr=${est['cache_read']}, cw=${est['cache_write']}")
        else:
            print(f"  ? {m} (无法推测, 无同族参考)")
    print("-" * 60)
    if not apply:
        print(f"\n💡 以上为预览。如需写入 overrides.json, 请运行:\n   ./usage.30s.py --update-unknown --apply")
        return
    if not added:
        print("\n⚠️ 没有可推测的模型价格, 未修改 overrides.json")
        return
    ovr = _load_json(OVERRIDES_FILE, {})
    ovr_models = ovr.setdefault("models", {})
    count = 0
    for m, p in added.items():
        if m not in ovr_models:
            ovr_models[m] = p
            count += 1
    if count > 0:
        ovr["_meta"] = {"updated": datetime.now().isoformat(), "comment": "Auto-estimated by --update-unknown"}
        os.makedirs(os.path.dirname(OVERRIDES_FILE), exist_ok=True)
        with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(ovr, f, indent=2, ensure_ascii=False)
        print(f"\n✅ 已将 {count} 个新模型推测价格写入 {OVERRIDES_FILE}")
        if os.path.exists(_SCAN_CACHE_FILE):
            os.remove(_SCAN_CACHE_FILE)
            _remove_codex_event_cache_dir()
            print("   已清除扫描缓存, 下次刷新将以新价格重算")
    else:
        print(f"\nℹ️ 候选模型均已存在于 {OVERRIDES_FILE}, 未做更改")
