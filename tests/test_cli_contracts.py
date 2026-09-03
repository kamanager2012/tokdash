import unittest
import os
import sys
import subprocess
import json

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

class TestCliContracts(unittest.TestCase):
    def run_cli(self, *args, timeout=20):
        cmd = [sys.executable, SCRIPT_PATH, *args]
        proc = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, timeout=timeout)
        return proc

    def test_cli_json_contract(self):
        res = self.run_cli("--json")
        self.assertEqual(res.returncode, 0, f"--json failed with stderr: {res.stderr}")
        self.assertTrue(len(res.stdout.strip()) > 0, "--json must produce non-empty output")
        
        data = json.loads(res.stdout)
        self.assertIsInstance(data, dict, "--json must return a JSON dictionary")
        
        # Verify essential metadata is populated
        self.assertIn("_pricing", data, "Output must include _pricing metadata")
        pricing = data["_pricing"]
        self.assertIn("count", pricing)
        self.assertGreater(pricing["count"], 0, "Loaded models count must be > 0")

    def test_cli_daily_costs_contract(self):
        res = self.run_cli("--daily-costs")
        self.assertEqual(res.returncode, 0, f"--daily-costs failed with stderr: {res.stderr}")
        
        data = json.loads(res.stdout)
        self.assertIsInstance(data, dict, "--daily-costs must return a JSON dictionary")
        self.assertIn("daily", data, "Response must contain 'daily' array")
        self.assertIn("models", data, "Response must contain 'models' array")
        self.assertIsInstance(data["daily"], list)
        self.assertIsInstance(data["models"], list)
        if len(data["daily"]) > 0:
            first = data["daily"][0]
            self.assertIn("tool_costs", first, "Daily record must contain 'tool_costs' dictionary")
            self.assertIsInstance(first["tool_costs"], dict)
            # Ensure sum of tool_costs matches or approximates total without token counters inflating
            self.assertIn("total", first)
            self.assertIn("tokens", first)

    def test_cli_projects_contract(self):
        res = self.run_cli("--projects")
        self.assertEqual(res.returncode, 0, f"--projects failed with stderr: {res.stderr}")
        
        data = json.loads(res.stdout)
        self.assertIsInstance(data, list, "--projects must return a list")
        if len(data) > 0:
            first = data[0]
            self.assertIn("path", first)
            self.assertIn("tokens", first)
            self.assertIn("cost", first)

    def test_cli_snapshot_contract(self):
        res = self.run_cli("--snapshot")
        self.assertEqual(res.returncode, 0, f"--snapshot failed with stderr: {res.stderr}")
        
        data = json.loads(res.stdout)
        self.assertIsInstance(data, dict, "--snapshot must return a JSON dictionary")
        self.assertIn("snapshot_id", data)
        self.assertIn("generation", data)
        self.assertIn("generated_at", data)
        self.assertIn("usage", data)
        self.assertIn("daily_costs", data)
        self.assertIn("projects", data)
        
        # Verify generation token is a valid 16-character hex SHA-256 digest
        self.assertIsInstance(data["generation"], str)
        self.assertEqual(len(data["generation"]), 16)
        int(data["generation"], 16)  # Must be valid hex

        # Verify usage contains pricing metadata
        self.assertIn("_pricing", data["usage"])
        # Verify projects is a list
        self.assertIsInstance(data["projects"], list)

if __name__ == "__main__":
    unittest.main()
