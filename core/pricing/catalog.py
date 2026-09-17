"""Model Pricing Engine and Catalog Resolution for Cognitally.

Implements multi-tier fail-closed model pricing resolution:
1. Exact catalog match (OpenRouter canonical DB or local override)
2. Exact alias (format normalization)
3. Price equivalent (inter-generational/replacement model)
4. Manual proxy (user-configured representative proxy)
5. Family proxy (keyword heuristics fallback)
6. Authoritative log
7. Unknown (fail-closed, strictly $0.00 rate)
"""

import re
from core.config import PRICING_FILE, OVERRIDES_FILE, _load_json

# 内置兜底: pricing.json 缺失时仍能离线工作(口径与 OpenRouter 一致)。
_DEFAULT_PRICES = {
    "anthropic/claude-opus-4.8":     {"in": 5.0,   "out": 25.0, "cache_read": 0.5,    "cache_write": 6.25},
    "anthropic/claude-sonnet-4.6":   {"in": 3.0,   "out": 15.0, "cache_read": 0.3,    "cache_write": 3.75},
    "anthropic/claude-haiku-4.5":    {"in": 1.0,   "out": 5.0,  "cache_read": 0.1,    "cache_write": 1.25},
    "openai/gpt-5.5":                {"in": 5.0,   "out": 30.0, "cache_read": 0.5,    "cache_write": 0.0},
    "qwen/qwen3.7-max":              {"in": 1.25,  "out": 3.75, "cache_read": 0.25,   "cache_write": 1.5625},
    "deepseek/deepseek-v4-pro":      {"in": 0.435, "out": 0.87, "cache_read": 0.0036, "cache_write": 0.0},
    "google/gemini-3.5-flash":       {"in": 1.5,   "out": 9.0,  "cache_read": 0.15,   "cache_write": 0.0833},
    "google/gemini-3.1-pro-preview": {"in": 2.0,   "out": 12.0, "cache_read": 0.2,    "cache_write": 0.375},
    "x-ai/grok-4.5":                 {"in": 2.0,   "out": 6.0,  "cache_read": 0.3,    "cache_write": 0.0},
    "tencent/hy3":                   {"in": 0.14,  "out": 0.58, "cache_read": 0.035,  "cache_write": 0.0},
    "tencent/hy3-preview":           {"in": 0.063, "out": 0.21, "cache_read": 0.021,  "cache_write": 0.0},
}

# DeepSeek Harness 的 deepseek-official 路由按官方直连价计算，不能套用
# OpenRouter 同名模型的渠道价。单位均为 USD / 1M tokens。
_DEEPSEEK_OFFICIAL_PRICES = {
    "deepseek-v4-pro": {
        "in": 0.435, "out": 0.87, "cache_read": 0.003625, "cache_write": 0.0,
    },
    "deepseek-v4-flash": {
        "in": 0.14, "out": 0.28, "cache_read": 0.0028, "cache_write": 0.0,
    },
}

# 家族关键字 → 代表性 canonical id(精确匹配失败时回退)。
_FAMILY = [
    ("opus",     "anthropic/claude-opus-4.8"),
    ("sonnet",   "anthropic/claude-sonnet-4.6"),
    ("haiku",    "anthropic/claude-haiku-4.5"),
    ("gpt-5",    "openai/gpt-5.5"),
    ("qwen",     "qwen/qwen3.7-max"),
    ("deepseek", "deepseek/deepseek-v4-pro"),
    ("glm",      "z-ai/glm-5.2"),
    ("mimo",     "xiaomi/mimo-v2.5-pro"),
    ("hy3",      "tencent/hy3"),
]

VALID_PROVENANCES = frozenset({
    "exact_catalog",
    "exact_alias",
    "price_equivalent",
    "manual_proxy",
    "family_proxy",
    "authoritative",
    "unknown",
})

_COST_KIND_BY_PROVENANCE = {
    "exact_catalog": "estimated_exact",
    "exact_alias": "estimated_exact",
    "price_equivalent": "estimated_price_equivalent",
    "manual_proxy": "estimated_manual_proxy",
    "family_proxy": "estimated_family_proxy",
    "authoritative": "authoritative_log",
    "unknown": "unknown",
}

_PRICING_DB = {}
_OVERRIDES = {}
_OV_MODELS = {}
_OV_ALIASES = {}


def reload_pricing():
    """Reload pricing databases and overrides from configuration files."""
    global _PRICING_DB, _OVERRIDES, _OV_MODELS, _OV_ALIASES
    _PRICING_DB.clear()
    _PRICING_DB.update(_load_json(PRICING_FILE, {}).get("models", {}))
    _OVERRIDES.clear()
    _OVERRIDES.update(_load_json(OVERRIDES_FILE, {}))
    _OV_MODELS.clear()
    _OV_MODELS.update(_OVERRIDES.get("models", {}))
    _OV_ALIASES.clear()
    _OV_ALIASES.update(_OVERRIDES.get("aliases", {}))


# Initial load
reload_pricing()


def _deepseek_official_price(model):
    normalized = _normalize(model) or ""
    model_id = normalized.rsplit("/", 1)[-1]
    price = _DEEPSEEK_OFFICIAL_PRICES.get(model_id)
    return dict(price) if price else None


def _normalize(model: str):
    """本地 model 名 → OpenRouter canonical id。免费档去 :free 按基础价;preview 后缀保留。"""
    m = (model or "").strip().lower()
    if not m or m == "<synthetic>":
        return None
    m = re.sub(r"\s+", "-", m)
    m = re.sub(r"[:\-]free$", "", m)                  # 免费档按基础价
    if "/" in m:
        return m                                      # 已是 OpenRouter 格式
    if m.startswith("claude"):
        m = re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", m)    # claude-opus-4-8 → claude-opus-4.8
        return "anthropic/" + m
    if re.match(r"(gpt|o\d|chatgpt)", m):
        return "openai/" + m
    if m.startswith("gemini"):
        return "google/" + m
    if m.startswith("grok"):
        return "x-ai/" + m
    if m.startswith("qwen"):
        return "qwen/" + m
    if m.startswith("deepseek"):
        return "deepseek/" + m
    if m.startswith("glm"):
        return "z-ai/" + m
    if m.startswith("mimo"):
        return "xiaomi/" + m
    if m == "hy3":
        return "tencent/hy3"
    if m in ("hy3-preview", "hy3 preview"):
        return "tencent/hy3-preview"
    return m


def _alias_target_and_prov(entry):
    """解析别名条目，强制要求结构化及合法 provenance 声明。
    非结构化 bare-string 绝不给予 exact_alias 特权，严格 fail-closed 降级为 manual_proxy。
    """
    if isinstance(entry, dict):
        target = entry.get("target")
        prov = entry.get("provenance")
        if prov in VALID_PROVENANCES:
            return target, prov
        return target, "manual_proxy"
    if isinstance(entry, str):
        # 兼容兜底：未显式结构化并附带证据的裸别名，一律视为人工代理，禁止默认 exact
        return entry, "manual_proxy"
    return None, "unknown"


def resolve_pricing_entry(model: str):
    """返回 (canonical_id, provenance)。
    provenance 严格分级:
      - 'exact_catalog': 目录数据库或单价覆写中的原生 canonical ID
      - 'exact_alias': 同名/同版本规范格式别名
      - 'price_equivalent': 跨代/替代费率等价映射
      - 'manual_proxy': 人工配置的代表模型代理
      - 'family_proxy': 家族关键字粗分代理
      - 'authoritative': 本地账本权威记录
      - 'unknown': 未知
    """
    s = (model or "").strip()
    if not s or s.lower() == "<synthetic>":
        return None, "unknown"

    if s in _OV_ALIASES:
        target, prov = _alias_target_and_prov(_OV_ALIASES[s])
        return target, prov

    norm = _normalize(model)
    if norm and (norm in _OV_MODELS or norm in _PRICING_DB or norm in _DEFAULT_PRICES):
        return norm, "exact_catalog"

    low = s.lower()
    if "gemini" in low:
        target = "google/gemini-3.1-pro-preview" if "pro" in low else "google/gemini-3.5-flash"
        return target, "family_proxy"
    for kw, rep in _FAMILY:
        if kw in low:
            return rep, "family_proxy"
    return None, "unknown"


def _resolve_id(model: str):
    """解析到 canonical id; 未知模型返回 None(由调用方标记 0/未知)，严禁假冒 Opus。"""
    cid, _ = resolve_pricing_entry(model)
    return cid


def _raw_price(model: str):
    """统一查价 → {in,out,cache_read,cache_write,write1h?,provenance}。<synthetic>→全 0。"""
    cid, prov = resolve_pricing_entry(model)
    if cid is None:
        return {"in": 0.0, "out": 0.0, "cache_read": 0.0, "cache_write": 0.0, "provenance": "unknown"}
    p = dict(_DEFAULT_PRICES.get(cid, {}))            # 内置兜底打底
    p.update(_PRICING_DB.get(cid, {}))                # OpenRouter 基准
    p.update(_OV_MODELS.get(cid, {}))                 # 本地覆盖优先
    out = {"in": p.get("in", 0.0), "out": p.get("out", 0.0),
           "cache_read": p.get("cache_read", 0.0), "cache_write": p.get("cache_write", 0.0),
           "provenance": prov}
    if "write1h" in p:
        out["write1h"] = p["write1h"]
    elif cid.startswith("anthropic/"):                # Anthropic 1h 写 = 2×输入价
        out["write1h"] = out["in"] * 2
    return out


def price_for(model: str):
    """Claude 成本用:补 write5m/write1h 两档(write5m = OpenRouter cache_write)。"""
    p = _raw_price(model)
    return {"in": p["in"], "out": p["out"], "cache_read": p["cache_read"],
            "write5m": p["cache_write"], "write1h": p.get("write1h", p["cache_write"]),
            "provenance": p["provenance"]}


def gemini_price(model: str):
    """Gemini 成本用:in/out/cache_read 取统一查价(OpenRouter 已分版本,比正则更准)。"""
    return _raw_price(model)


def _known_id_or_raw(model: str):
    """Return a canonical priced ID when known, preserving unknown model names."""
    s = (model or "").strip()
    if not s or s.lower() == "<synthetic>":
        return None
    if s in _OV_ALIASES:
        target, _ = _alias_target_and_prov(_OV_ALIASES[s])
        return target
    norm = _normalize(s)
    if norm and (norm in _OV_MODELS or norm in _PRICING_DB or norm in _DEFAULT_PRICES):
        return norm
    low = s.lower()
    if "gemini" in low:
        return "google/gemini-3.1-pro-preview" if "pro" in low else "google/gemini-3.5-flash"
    for keyword, representative in _FAMILY:
        if keyword in low:
            return representative
    return s


def _model_identity_id(model: str):
    """Resolve only exact catalog identities; never guess an unknown model family."""
    s = (model or "").strip()
    if not s or s.lower() == "<synthetic>":
        return None
    if s in _OV_ALIASES:
        target, _ = _alias_target_and_prov(_OV_ALIASES[s])
        return target
    norm = _normalize(s)
    if norm and (norm in _OV_MODELS or norm in _PRICING_DB or norm in _DEFAULT_PRICES):
        return norm
    for model_id, entry in _PRICING_DB.items():
        if not isinstance(entry, dict):
            continue
        slug = entry.get("canonical_slug")
        if isinstance(slug, str) and _normalize(slug) == norm:
            return model_id
    return s


def _exact_pricing_id(model: str):
    """Return a catalog pricing ID without family-based fallback."""
    if model and (model in _OV_MODELS or model in _PRICING_DB or model in _DEFAULT_PRICES):
        return model
    return None


def _pricing_id(model: str):
    canonical = _known_id_or_raw(model)
    if canonical and (canonical in _OV_MODELS or canonical in _PRICING_DB or canonical in _DEFAULT_PRICES):
        return canonical
    normalized = _normalize(model)
    if normalized == "z-ai/glm-5.2" and "z-ai/glm-5.1" in _PRICING_DB:
        return "z-ai/glm-5.1"
    return None


def _has_known_price(model: str):
    return _pricing_id(model) is not None


def nice_model(m: str) -> str:
    """claude-opus-4-7 → Opus 4.7;<synthetic> → 合成;其它去前缀/-free 后美化。"""
    if not m or m == "<synthetic>":
        return "合成"
    if m == "unknown":
        return "未知"
    s = m.lower()
    for key, disp in (("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")):
        if key in s:
            mt = re.search(r"(\d+)-(\d+)", s)
            return f"{disp} {mt.group(1)}.{mt.group(2)}" if mt else disp
    if "gpt" in s:
        mt = re.search(r"gpt[- ]?(\d+(?:\.\d+)?)", s)
        version = mt.group(1) if mt else ""
        variant_labels = []
        for token, label in (("sol", "Sol"), ("luna", "Luna"), ("terra", "Terra"),
                             ("mini", "Mini"), ("pro", "Pro")):
            if re.search(rf"(?:^|[-_/ ]){token}(?:$|[-_/ ])", s):
                variant_labels.append(label)
        suffix = f" {' '.join(variant_labels)}" if variant_labels else ""
        return f"GPT-{version}{suffix}" if version else "GPT"
    if "mimo" in s:
        name = m.split("/")[-1]
        version = re.sub(r"^mimo[- ]?v?", "", name, flags=re.I).strip()
        parts = [part for part in version.split("-") if part]
        if not parts:
            return "MiMo"
        head = "MiMo-V" + parts[0] if parts[0][0].isdigit() else "MiMo-" + parts[0]
        return "-".join([head] + [part.capitalize() for part in parts[1:]])
    name = re.sub(r"[-:](free|preview|latest)$", "", m.split("/")[-1]).replace("-", " ")
    return " ".join(w[:1].upper() + w[1:] if w[:1].isalpha() else w
                    for w in name.split())
