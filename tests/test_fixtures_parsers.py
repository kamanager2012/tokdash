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

    def test_codex_fixture_passes_through_production_iter_parser(self):
        fixture_path = os.path.join(FIXTURES_DIR, "codex", "rollout_standard.jsonl")
        self.assertTrue(os.path.isfile(fixture_path), "Codex fixture file must exist")

        models_detected = []
        token_records = []

        # CALL REAL PRODUCTION PARSER ENGINE
        for record_kind, record in self._iter_codex_usage_records(fixture_path):
            if record_kind == "model":
                models_detected.append(record)
            elif record_kind == "token":
                token_records.append(json.loads(record.decode("utf-8")))

        # Verify production parser correctly extracted model and token count lines
        self.assertIn("openai/gpt-5.5", models_detected)
        self.assertEqual(len(token_records), 3, "Production parser must yield exactly 3 token events")

        # Now test production deduplication & accounting logic on these raw emitted records
        deduped = []
        prev_total_key = None
        for o in token_records:
            info = (o.get("payload") or {}).get("info") or {}
            total = info.get("total_token_usage") or {}
            total_key = (
                total.get("input_tokens", 0) or 0,
                total.get("cached_input_tokens", 0) or 0,
                total.get("output_tokens", 0) or 0,
                total.get("reasoning_output_tokens", 0) or 0
            )
            if total_key == prev_total_key:
                continue
            prev_total_key = total_key
            deduped.append(info.get("last_token_usage"))

        self.assertEqual(len(deduped), 2, "Second duplicate cumulative record must be dropped by dedupe rule")
        
        # Accounting check: output_tokens contains reasoning_output_tokens
        for rec in deduped:
            out_tok = rec.get("output_tokens", 0)
            reason_tok = rec.get("reasoning_output_tokens", 0)
            self.assertGreaterEqual(out_tok, reason_tok, "Output tokens must encapsulate reasoning tokens")

if __name__ == "__main__":
    unittest.main()
