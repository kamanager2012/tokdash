import unittest
import os
import sys
import subprocess
import json

import importlib.util
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

spec = importlib.util.spec_from_file_location("usage_module", SCRIPT_PATH)
usage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage_module)

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
            # Mathematical invariant: sum of tool_costs must strictly match total
            tool_sum = round(sum(first["tool_costs"].values()), 2)
            self.assertAlmostEqual(tool_sum, first["total"], places=2,
                                   msg="sum of tool_costs must match daily total")
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
        gen_token = data["generation"]
        self.assertIsInstance(gen_token, str)
        self.assertEqual(len(gen_token), 16)
        int(gen_token, 16)  # Must be valid hex

        # Verify usage contains pricing metadata
        self.assertIn("_pricing", data["usage"])
        # Verify projects is a list
        self.assertIsInstance(data["projects"], list)

        # 3. Test Mutation Sensitivity of the Canonical Snapshot Digest
        _canonical_snapshot_digest = getattr(usage_module, "_canonical_snapshot_digest")
        base_digest = _canonical_snapshot_digest(data["usage"], data["daily_costs"], data["projects"])
        self.assertEqual(gen_token, base_digest, "Snapshot generation must match canonical state digest")

        # Mutating input tokens in any tool must change the digest
        import copy
        mutated_usage = copy.deepcopy(data["usage"])
        for tool_key in sorted(mutated_usage.keys()):
            if not tool_key.startswith("_") and "ranges" in mutated_usage[tool_key]:
                r_all = mutated_usage[tool_key]["ranges"].get("all")
                if r_all:
                    r_all["in"] = r_all.get("in", 0) + 100
                    break
        mutated_digest = _canonical_snapshot_digest(mutated_usage, data["daily_costs"], data["projects"])
        self.assertNotEqual(base_digest, mutated_digest, "Modifying tool usage tokens must mutate state digest")

        # Mutating project cost must change the digest
        mutated_projects = copy.deepcopy(data["projects"])
        mutated_projects.append({"path": "/tmp/mutated-test-project", "cost": 9.99, "tokens": 5000})
        project_mutated_digest = _canonical_snapshot_digest(data["usage"], data["daily_costs"], mutated_projects)
        self.assertNotEqual(base_digest, project_mutated_digest, "Modifying projects must mutate state digest")

        # 4. Unconditional Deterministic Hard-Gate: Mutating model pricing provenance
        synthetic_usage = {
            "claude": {
                "ranges": {
                    "all": {
                        "in": 1000, "out": 200, "cr": 0, "cw": 0, "reason": 0, "cost": 0.05, "sessions": 1,
                        "models": [
                            {"name": "claude-sonnet-4.6", "cost": 0.05, "pricing_provenance": "exact_catalog", "pricing_source": "anthropic/claude-sonnet-4.6"}
                        ]
                    }
                }
            }
        }
        synthetic_daily = {"daily": [{"date": "2026-09-03", "total": 0.05, "tokens": 1200, "tool_costs": {"claude": 0.05}}]}
        synthetic_projects = [{"path": "/workspace/demo", "cost": 0.05, "tokens": 1200}]

        digest_original = _canonical_snapshot_digest(synthetic_usage, synthetic_daily, synthetic_projects)

        mutated_model_usage = copy.deepcopy(synthetic_usage)
        mutated_model_usage["claude"]["ranges"]["all"]["models"][0]["pricing_provenance"] = "manual_proxy"
        digest_mutated = _canonical_snapshot_digest(mutated_model_usage, synthetic_daily, synthetic_projects)

        self.assertNotEqual(digest_original, digest_mutated,
                           "Mutating model pricing_provenance MUST unconditionally mutate accounting state digest")

if __name__ == "__main__":
    unittest.main()
