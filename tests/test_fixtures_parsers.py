import unittest
import os
import sys
import json
import importlib.util

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

spec = importlib.util.spec_from_file_location("usage_30s", SCRIPT_PATH)
usage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage_module)

class TestFixtureParsers(unittest.TestCase):
    def setUp(self):
        self._claude_usage = getattr(usage_module, "_claude_usage")
        self._iter_codex_usage_records = getattr(usage_module, "_iter_codex_usage_records")
        self.price_for = getattr(usage_module, "price_for")

    def test_claude_standard_fixture_accounting(self):
        fixture_path = os.path.join(FIXTURES_DIR, "claude", "session_standard.jsonl")
        self.assertTrue(os.path.isfile(fixture_path), "Claude fixture file must exist")

        total_in = 0
        total_out = 0
        total_cr = 0
        total_cw = 0
        total_cw1h = 0
        total_cost = 0.0
        parsed_turns = 0

        with open(fixture_path, "r", encoding="utf-8") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                u = self._claude_usage(line, want_dt=True)
                if not u:
                    continue
                parsed_turns += 1
                total_in += u["in"]
                total_out += u["out"]
                total_cr += u["cr"]
                total_cw += u["cw"]
                total_cw1h += u.get("cw1h", 0)
                total_cost += u["cost"]

        # Assert exactly 3 assistant turns parsed (user turns ignored)
        self.assertEqual(parsed_turns, 3)
        # Turn 1: in=1500, out=300, cr=5000, cw=2000 (5m)
        # Turn 2: in=1800, out=500, cr=8500, cw=0
        # Turn 3: in=2000, out=400, cr=10000, cw1h=3000 (1h)
        self.assertEqual(total_in, 1500 + 1800 + 2000)
        self.assertEqual(total_out, 300 + 500 + 400)
        self.assertEqual(total_cr, 5000 + 8500 + 10000)
        self.assertEqual(total_cw, 2000 + 3000)

        # Cache hit calculation: cr / (in + cr)
        full_prompt = total_in + total_cr
        cache_hit_rate = (total_cr / full_prompt) * 100
        self.assertAlmostEqual(cache_hit_rate, (23500 / (5300 + 23500)) * 100, places=2)

        # Cost must match pricing contract including 5m and 1h write pricing
        sonnet_price = self.price_for("claude-3-5-sonnet-20241022")
        expected_cost = (
            total_in / 1e6 * sonnet_price["in"] +
            total_out / 1e6 * sonnet_price["out"] +
            total_cr / 1e6 * sonnet_price["cache_read"] +
            2000 / 1e6 * sonnet_price["write5m"] +
            3000 / 1e6 * sonnet_price["write1h"]
        )
        self.assertAlmostEqual(total_cost, expected_cost, places=5)

    def test_codex_fixture_passes_through_production_iter_parser_and_dedupe(self):
        fixture_path = os.path.join(FIXTURES_DIR, "codex", "rollout_standard.jsonl")
        self.assertTrue(os.path.isfile(fixture_path), "Codex fixture file must exist")

        models_detected = []
        token_records = []

        # 1. CALL REAL PRODUCTION STREAM PARSER ENGINE
        for record_kind, record in self._iter_codex_usage_records(fixture_path):
            if record_kind == "model":
                models_detected.append(record)
            elif record_kind == "token":
                token_records.append(json.loads(record.decode("utf-8")))

        self.assertIn("openai/gpt-5.5", models_detected)
        self.assertEqual(len(token_records), 3, "Production parser must yield exactly 3 token events")

        # 2. RUN FULL PRODUCTION SCAN_CODEX AGGREGATION & DEDUPE PIPELINE
        scan_codex = getattr(usage_module, "scan_codex")
        range_bounds = getattr(usage_module, "range_bounds")
        _LEDGER_CACHE = getattr(usage_module, "_LEDGER_CACHE")
        
        bounds = range_bounds()
        isolated_cache = {}
        
        # Isolate ledger from local machine history
        old_ledger_file = getattr(usage_module, "_LEDGER_FILE")
        old_ledger_data = _LEDGER_CACHE["data"]
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                setattr(usage_module, "_LEDGER_FILE", os.path.join(tmpdir, "isolated_ledger.json"))
                _LEDGER_CACHE["data"] = {"v": 1, "tools": {}}
                result = scan_codex(bounds, isolated_cache, rollout_files=[fixture_path])
            finally:
                setattr(usage_module, "_LEDGER_FILE", old_ledger_file)
                _LEDGER_CACHE["data"] = old_ledger_data

        all_range = result["ranges"]["all"]

        # Production deduplication must discard the duplicated cumulative snapshot
        # Turn 1: in=3000, cached=1000, out=400, reason=150
        # Turn 2: duplicate snapshot (dropped)
        # Turn 3: in=4200, cached=3000, out=600, reason=200
        # Expected: in=7200, cached=4000, out=1000, reason=350
        self.assertEqual(all_range["in"], 3000 + 4200)
        self.assertEqual(all_range["cached"], 1000 + 3000)
        self.assertEqual(all_range["out"], 400 + 600)
        self.assertEqual(all_range["reason"], 150 + 200)

        # Output tokens encapsulates reasoning output tokens
        self.assertGreaterEqual(all_range["out"], all_range["reason"])
        
        # Verify event cache was actually created in isolated_cache
        self.assertIn("codex", isolated_cache)

if __name__ == "__main__":
    unittest.main()
