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
        self.price_for = getattr(usage_module, "price_for")

    def test_claude_standard_fixture_accounting(self):
        fixture_path = os.path.join(FIXTURES_DIR, "claude", "session_standard.jsonl")
        self.assertTrue(os.path.isfile(fixture_path), "Claude fixture file must exist")

        total_in = 0
        total_out = 0
        total_cr = 0
        total_cw = 0
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
                total_cost += u["cost"]

        # Assert exactly 2 assistant turns parsed (user turns ignored)
        self.assertEqual(parsed_turns, 2)
        # Turn 1: in=1500, out=300, cr=5000, cw=2000
        # Turn 2: in=1800, out=500, cr=8500, cw=0
        self.assertEqual(total_in, 3300)
        self.assertEqual(total_out, 800)
        self.assertEqual(total_cr, 13500)
        self.assertEqual(total_cw, 2000)

        # Cache hit calculation: cr / (in + cr)
        full_prompt = total_in + total_cr
        cache_hit_rate = (total_cr / full_prompt) * 100
        self.assertAlmostEqual(cache_hit_rate, 80.357, places=2)

        # Cost must be greater than zero and match pricing contract
        sonnet_price = self.price_for("claude-3-5-sonnet-20241022")
        expected_cost = (
            total_in / 1e6 * sonnet_price["in"] +
            total_out / 1e6 * sonnet_price["out"] +
            total_cr / 1e6 * sonnet_price["cache_read"] +
            total_cw / 1e6 * sonnet_price["write5m"]
        )
        self.assertAlmostEqual(total_cost, expected_cost, places=5)

    def test_codex_fixture_snapshot_deduplication(self):
        fixture_path = os.path.join(FIXTURES_DIR, "codex", "rollout_standard.jsonl")
        self.assertTrue(os.path.isfile(fixture_path), "Codex fixture file must exist")

        records = []
        prev_total_key = None
        deduped_turns = 0
        skipped_duplicates = 0

        with open(fixture_path, "r", encoding="utf-8") as f:
            for line in f:
                if '"turn_complete"' not in line:
                    continue
                o = json.loads(line)
                info = (o.get("payload") or {}).get("info") or {}
                last = info.get("last_token_usage") or {}
                total = info.get("total_token_usage") or {}
                
                total_key = (
                    total.get("input_tokens", 0) or 0,
                    total.get("cached_input_tokens", 0) or 0,
                    total.get("output_tokens", 0) or 0,
                    total.get("reasoning_output_tokens", 0) or 0
                )
                if total_key == prev_total_key:
                    skipped_duplicates += 1
                    continue
                
                prev_total_key = total_key
                deduped_turns += 1
                records.append(last)

        # There are 3 turn_complete lines in fixture, turn 2 is an exact duplicate of turn 1
        self.assertEqual(deduped_turns, 2, "Must parse exactly 2 unique turns")
        self.assertEqual(skipped_duplicates, 1, "Must skip exactly 1 duplicate cumulative snapshot")

        # Turn 1: in=3000, cached=1000, out=400, reason=150
        # Turn 2: in=4200, cached=3000, out=600, reason=200
        # Verify reasoning tokens: out already includes reasoning in OpenAI schema
        turn1_out = records[0]["output_tokens"]
        turn1_reason = records[0]["reasoning_output_tokens"]
        self.assertGreaterEqual(turn1_out, turn1_reason, "output_tokens must encapsulate reasoning_tokens")

if __name__ == "__main__":
    unittest.main()
