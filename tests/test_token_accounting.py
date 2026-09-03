import unittest
import os
import sys
import json
from datetime import datetime, timedelta, timezone
import importlib.util

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

spec = importlib.util.spec_from_file_location("usage_30s", SCRIPT_PATH)
usage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage_module)

class TestTokenAccounting(unittest.TestCase):
    def setUp(self):
        self.token_total = getattr(usage_module, "token_total")
        self.range_bounds = getattr(usage_module, "range_bounds")
        self.classify_date = getattr(usage_module, "classify_date")
        self.parse_ts = getattr(usage_module, "parse_ts")
        self._claude_usage = getattr(usage_module, "_claude_usage")

    def test_token_total_sums_all_fields(self):
        sample_day = {
            "in": 1000,
            "out": 500,
            "cr": 2000,
            "cw": 300,
            "reason": 150,
            "cost": 0.05
        }
        total = self.token_total(sample_day)
        # 1000 + 500 + 2000 + 300 + 150 = 3950
        self.assertEqual(total, 3950, "token_total must accurately sum in, out, cr, cw, and reason")

    def test_utc_to_local_timezone_conversion(self):
        # 2026-09-03T16:30:00Z is UTC, which is 2026-09-04T00:30:00 in UTC+8
        utc_str = "2026-09-03T16:30:00Z"
        dt = self.parse_ts(utc_str)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        
        # Converted to local timezone
        local_dt = dt.astimezone()
        self.assertIsNotNone(local_dt.tzinfo)

    def test_range_bounds_and_classification(self):
        bounds = self.range_bounds()
        self.assertIn("today", bounds)
        self.assertIn("yesterday", bounds)
        self.assertIn("7d", bounds)
        self.assertIn("30d", bounds)
        self.assertIn("month", bounds)

        today_date = bounds["today"].date()
        yesterday_date = bounds["yesterday"].date()

        today_classes = self.classify_date(today_date, bounds)
        self.assertIn("today", today_classes)
        self.assertIn("all", today_classes)
        self.assertIn("7d", today_classes)
        self.assertIn("30d", today_classes)
        self.assertNotIn("yesterday", today_classes)

        yesterday_classes = self.classify_date(yesterday_date, bounds)
        self.assertIn("yesterday", yesterday_classes)
        self.assertNotIn("today", yesterday_classes)

    def test_claude_jsonl_parser_accounting(self):
        # Synthetic assistant record with usage
        sample_line = json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-03T12:00:00Z",
            "cwd": "/tmp/project-alpha",
            "message": {
                "id": "msg_test_01",
                "model": "claude-3-5-sonnet-20241022",
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 450,
                    "cache_read_input_tokens": 8000,
                    "cache_creation_input_tokens": 2000
                }
            }
        })
        parsed = self._claude_usage(sample_line, want_dt=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["in"], 1200)
        self.assertEqual(parsed["out"], 450)
        self.assertEqual(parsed["cr"], 8000)
        self.assertEqual(parsed["cw"], 2000)
        self.assertGreater(parsed["cost"], 0)
        self.assertEqual(parsed["cwd"], "/tmp/project-alpha")

    def test_claude_jsonl_ignores_user_and_non_assistant_lines(self):
        user_line = json.dumps({
            "type": "user",
            "timestamp": "2026-09-03T12:00:00Z",
            "message": {"content": "hello"}
        })
        self.assertIsNone(self._claude_usage(user_line))

if __name__ == "__main__":
    unittest.main()
