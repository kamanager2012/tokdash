import unittest
import os
import sys
import subprocess
import json

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import statusline_engine
import budget_guard


class TestStatuslineAndGuardrails(unittest.TestCase):
    def run_cli(self, *args, timeout=20):
        cmd = [sys.executable, SCRIPT_PATH, *args]
        test_env = {
            **os.environ,
            "TOKEI_CODEX_LIVE_QUOTA": "0",
            "TOKEI_GROK_LIVE_QUOTA": "0",
        }
        return subprocess.run(cmd, cwd=ROOT_DIR, env=test_env, capture_output=True, text=True, timeout=timeout)

    def test_statusline_engine_formatting(self):
        """验证 statusline 格式化引擎的多模式输出与单行零换行契约。"""
        synthetic_snapshot = {
            "generation": "abcdef0123456789",
            "usage": {
                "claude": {
                    "ranges": {
                        "today": {
                            "in": 1000, "out": 200, "cr": 100, "cw": 50, "reason": 50, "cost": 0.05
                        }
                    }
                },
                "codex": {
                    "ranges": {
                        "today": {
                            "in": 5000, "out": 1000, "cr": 0, "cw": 0, "reason": 0, "cost": 0.15
                        }
                    },
                    "limits": {"pct": 85}
                }
            }
        }

        # 1. Default format
        res_default = statusline_engine.render_statusline(synthetic_snapshot, period="today", format_type="default")
        self.assertNotIn("\n", res_default, "Statusline must contain zero newlines")
        self.assertIn("$0.20", res_default)
        self.assertIn("7.4k tok", res_default)
        self.assertIn("2 agents", res_default)
        self.assertIn("codex:85%", res_default)

        # 2. Compact format
        res_compact = statusline_engine.render_statusline(synthetic_snapshot, period="today", format_type="compact")
        self.assertEqual(res_compact, "$0.20 (7.4k)")

        # 3. Cost format
        res_cost = statusline_engine.render_statusline(synthetic_snapshot, period="today", format_type="cost")
        self.assertEqual(res_cost, "$0.20")

        # 4. Tokens format
        res_tokens = statusline_engine.render_statusline(synthetic_snapshot, period="today", format_type="tokens")
        self.assertEqual(res_tokens, "7.4k")

        # 5. JSON format
        res_json = statusline_engine.render_statusline(synthetic_snapshot, period="today", format_type="json")
        data = json.loads(res_json)
        self.assertEqual(data["cost_usd"], 0.20)
        self.assertEqual(data["tokens"], 7400)
        self.assertEqual(data["active_agents_count"], 2)

    def test_statusline_cli_contract(self):
        """测试 CLI 子命令 --statusline 的单行输出契约与毫秒级返回。"""
        res = self.run_cli("--statusline")
        self.assertEqual(res.returncode, 0, f"--statusline failed: {res.stderr}")
        lines = res.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, "--statusline output must be strictly 1 line")
        self.assertTrue(lines[0].startswith("⚡ $"), "Statusline should include cost prefix")

        # JSON format CLI test
        res_json = self.run_cli("--statusline", "--json")
        self.assertEqual(res_json.returncode, 0)
        payload = json.loads(res_json.stdout.strip())
        self.assertIn("cost_usd", payload)
        self.assertIn("tokens", payload)

    def test_budget_guard_logic(self):
        """测试主动预算守护者阈值逻辑与超额判定。"""
        synthetic_snapshot = {
            "generation": "abcdef0123456789",
            "usage": {
                "claude": {
                    "ranges": {
                        "today": {"cost": 5.50}
                    }
                }
            }
        }

        # 1. Below limit
        rep1 = budget_guard.check_budget(synthetic_snapshot, period="today", threshold_usd=10.0, send_notification=False)
        self.assertFalse(rep1["exceeded"])
        self.assertEqual(rep1["current_cost_usd"], 5.50)

        # 2. Exceeded limit
        rep2 = budget_guard.check_budget(synthetic_snapshot, period="today", threshold_usd=5.0, send_notification=False)
        self.assertTrue(rep2["exceeded"])
        self.assertEqual(rep2["threshold_usd"], 5.0)

    def test_budget_check_cli_contract(self):
        """测试 CLI 子命令 --budget-check 与 --budget-limit 的行为契约。"""
        # Exceeded threshold
        res_exceeded = self.run_cli("--budget-check", "--budget-limit", "0.01")
        self.assertEqual(res_exceeded.returncode, 0)
        self.assertIn("EXCEEDED", res_exceeded.stdout)

        # High threshold
        res_ok = self.run_cli("--budget-check", "--budget-limit", "999999.0")
        self.assertEqual(res_ok.returncode, 0)
        self.assertIn("WITHIN BUDGET", res_ok.stdout)

        # JSON output
        res_json = self.run_cli("--budget-check", "--budget-limit", "10.0", "--json")
        self.assertEqual(res_json.returncode, 0)
        rep = json.loads(res_json.stdout.strip())
        self.assertIn("exceeded", rep)
        self.assertIn("current_cost_usd", rep)


if __name__ == "__main__":
    unittest.main()
