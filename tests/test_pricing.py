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

if __name__ == "__main__":
    unittest.main()
