import unittest
import os
import sys
import importlib.util

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

spec = importlib.util.spec_from_file_location("usage_30s", SCRIPT_PATH)
usage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage_module)

class TestPricingEngine(unittest.TestCase):
    def test_pricing_file_exists(self):
        pricing_path = os.path.join(ROOT_DIR, "pricing.json")
        self.assertTrue(os.path.isfile(pricing_path), "pricing.json must exist in root dir")

    def test_known_models_resolve_pricing(self):
        price_for = getattr(usage_module, "price_for")
        
        # Test Claude Sonnet pricing
        claude_price = price_for("claude-3-5-sonnet-20241022")
        self.assertGreater(claude_price["in"], 0, "Claude Sonnet input price must be > 0")
        self.assertGreater(claude_price["out"], 0, "Claude Sonnet output price must be > 0")
        self.assertIn("cache_read", claude_price)
        self.assertIn("write5m", claude_price)

        # Test GPT-4o / GPT-5.5 fallback
        gpt_price = price_for("openai/gpt-5.5")
        self.assertGreater(gpt_price["in"], 0)
        self.assertGreater(gpt_price["out"], 0)

        # Test DeepSeek pricing
        ds_price = price_for("deepseek/deepseek-v4-pro")
        self.assertGreater(ds_price["in"], 0)
        self.assertGreater(ds_price["out"], 0)

    def test_synthetic_models_zero_cost(self):
        _raw_price = getattr(usage_module, "_raw_price")
        synthetic_price = _raw_price("<synthetic>")
        self.assertEqual(synthetic_price["in"], 0.0)
        self.assertEqual(synthetic_price["out"], 0.0)
        self.assertEqual(synthetic_price["cache_read"], 0.0)

    def test_pricing_overrides_exist_and_valid(self):
        overrides_path = os.path.join(ROOT_DIR, "pricing_overrides.json")
        self.assertTrue(os.path.isfile(overrides_path), "pricing_overrides.json must exist")

    def test_unknown_models_do_not_fallback_to_opus(self):
        _resolve_id = getattr(usage_module, "_resolve_id")
        price_for = getattr(usage_module, "price_for")
        unknown_id = _resolve_id("completely-unknown-custom-fine-tuned-model-xyz")
        self.assertIsNone(unknown_id, "Unknown models must resolve to None, not Claude Opus")
        
        price = price_for("completely-unknown-custom-fine-tuned-model-xyz")
        self.assertEqual(price["in"], 0.0)
        self.assertEqual(price["out"], 0.0)
        self.assertEqual(price["cache_read"], 0.0)
        self.assertEqual(price["provenance"], "unknown")

    def test_pricing_provenance_classification(self):
        resolve_pricing_entry = getattr(usage_module, "resolve_pricing_entry")
        
        # 1. Exact catalog match
        cid, prov = resolve_pricing_entry("anthropic/claude-sonnet-4.6")
        self.assertEqual(prov, "exact_catalog")
        self.assertEqual(cid, "anthropic/claude-sonnet-4.6")

        # 2. Exact alias (format normalization alias)
        cid, prov = resolve_pricing_entry("grok-4.6-build")
        self.assertEqual(prov, "exact_alias")
        self.assertEqual(cid, "x-ai/grok-4.6")

        # 3. Price equivalent mapping (inter-generational/replacement model)
        cid, prov = resolve_pricing_entry("claude-3-5-sonnet-20241022")
        self.assertEqual(prov, "price_equivalent")
        self.assertEqual(cid, "anthropic/claude-sonnet-4.6")

        # 4. Manual representative proxy
        cid, prov = resolve_pricing_entry("Composer 2.5")
        self.assertEqual(prov, "manual_proxy")
        self.assertEqual(cid, "openai/gpt-5.5")

        # 5. Family proxy match (heuristic keyword fallback)
        cid, prov = resolve_pricing_entry("claude-sonnet-experimental-v9")
        self.assertEqual(prov, "family_proxy")
        self.assertIn("sonnet", cid)

        # 6. Completely unknown
        cid, prov = resolve_pricing_entry("completely-unrecognized-vendor-model-999")
        self.assertEqual(prov, "unknown")
        self.assertIsNone(cid)

    def test_cost_kind_mapping_and_fail_closed_aliases(self):
        _alias_target_and_prov = getattr(usage_module, "_alias_target_and_prov")
        _COST_KIND_BY_PROVENANCE = getattr(usage_module, "_COST_KIND_BY_PROVENANCE")

        # 1. Bare string alias must fail-closed to manual_proxy, NEVER exact_alias
        target, prov = _alias_target_and_prov("unverified-vendor/model-v1")
        self.assertEqual(prov, "manual_proxy", "Bare string alias must fail closed to manual_proxy")

        # 2. Unknown provenance in structured alias must fail-closed to manual_proxy
        target, prov = _alias_target_and_prov({"target": "test/model", "provenance": "invalid_bogus_prov"})
        self.assertEqual(prov, "manual_proxy", "Invalid provenance must fail closed to manual_proxy")

        # 3. Verify all valid provenances map to a valid, non-unknown cost_kind (except unknown itself)
        for p in ("exact_catalog", "exact_alias", "price_equivalent", "manual_proxy", "family_proxy", "authoritative"):
            ck = _COST_KIND_BY_PROVENANCE.get(p)
            self.assertIsNotNone(ck)
            self.assertNotEqual(ck, "unknown", f"{p} must not map to unknown cost_kind")
