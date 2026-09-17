import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS_SCRIPT = os.path.join(ROOT_DIR, ".agents", "scripts", "run_ocr_audit.py")

# Import harness functions directly
sys.path.insert(0, os.path.join(ROOT_DIR, ".agents", "scripts"))
import run_ocr_audit


class TestOcrHarness(unittest.TestCase):
    def test_find_ocr_binary_and_version(self):
        """验证探测系统中的 open-code-review 二进制（若未安装则能安全返回 None）。"""
        ocr_bin = run_ocr_audit.find_ocr_binary()
        if ocr_bin is not None:
            self.assertTrue(os.path.isfile(ocr_bin), f"Binary at {ocr_bin} must exist")
            self.assertTrue(os.access(ocr_bin, os.X_OK), f"Binary at {ocr_bin} must be executable")
            version_str = run_ocr_audit.get_ocr_version(ocr_bin)
            self.assertIn("open-code-review", version_str, f"Unexpected version string: {version_str}")
        else:
            # CI 环境下允许无全局 ocr 安装，需保持非崩溃回退
            self.assertIsNone(ocr_bin)

    def test_parse_ocr_preview_markdown(self):
        """验证对 'ocr delegate preview' 输出的解析能力（含增删行、排除项与待审项）。"""
        sample_output = """
# Files (2 reviewable / 4 total)

- mode: range
- from: abc1234
- to: def5678
- merge_base: 0123456789abcdef
- total_insertions: 42
- total_deletions: 15

~~- `.agents/PRODUCTION-GATES.md` [modified] +3/-3 (excluded: unsupported_ext)~~
~~- `AGENTS.md` [modified] +1/-1 (excluded: unsupported_ext)~~
  - `core/collectors/misc.py` [modified] +30/-10
  - `usage.30s.py` [modified] +12/-5
"""
        parsed = run_ocr_audit.parse_ocr_preview(sample_output)
        self.assertEqual(parsed["total_insertions"], 42)
        self.assertEqual(parsed["total_deletions"], 15)
        self.assertEqual(len(parsed["excluded_files"]), 2)
        self.assertEqual(parsed["excluded_files"][0]["path"], ".agents/PRODUCTION-GATES.md")
        self.assertEqual(parsed["excluded_files"][0]["reason"], "unsupported_ext")

        self.assertEqual(len(parsed["reviewable_files"]), 2)
        self.assertEqual(parsed["reviewable_files"][0]["path"], "core/collectors/misc.py")
        self.assertEqual(parsed["reviewable_files"][0]["status"], "modified")
        self.assertEqual(parsed["reviewable_files"][0]["insertions"], 30)
        self.assertEqual(parsed["reviewable_files"][0]["deletions"], 10)

    def test_run_ocr_audit_cli_invocation(self):
        """验证 run_ocr_audit.py CLI 端到端执行与结构化 JSON 输出契约（兼容开发机与 CI Runner）。"""
        ocr_bin = run_ocr_audit.find_ocr_binary()
        cmd = [
            sys.executable,
            HARNESS_SCRIPT,
            "--from", "HEAD~1",
            "--to", "HEAD",
            "--skip-tests",
            "--json",
            "--repo", ROOT_DIR,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, f"run_ocr_audit failed with: {res.stderr}")

        data = json.loads(res.stdout)
        self.assertIn("status", data)
        if ocr_bin is not None:
            self.assertEqual(data["status"], "PASS")
            self.assertTrue(data["ocr_available"])
            self.assertIn("preview", data)
            self.assertIn("rule_resolution", data)
        else:
            # 在 CI Runner 无 ocr 环境下，输出受控的 BLOCKED 结构而不崩溃
            self.assertEqual(data["status"], "BLOCKED")
            self.assertFalse(data["ocr_available"])
            self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
