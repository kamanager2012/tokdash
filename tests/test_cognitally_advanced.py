import unittest
import os
import sys
import subprocess
import json
import tempfile
import time
import shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")


class TestCognitallyAdvanced(unittest.TestCase):
    def run_cli(self, *args, timeout=20):
        cmd = [sys.executable, SCRIPT_PATH, *args]
        return subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, timeout=timeout)

    def test_single_flight_snapshot_caching(self):
        """测试原子快照在 TTL 内的跨进程单飞复用与代数一致性。"""
        # 第一轮调用: 写入或刷新快照
        res1 = self.run_cli("--snapshot", "--force")
        self.assertEqual(res1.returncode, 0, f"--snapshot --force failed: {res1.stderr}")
        data1 = json.loads(res1.stdout)
        gen1 = data1["generation"]

        # 第二轮调用(未带 --force，处于 5s 窗口内): 必须快速单飞复用
        res2 = self.run_cli("--snapshot")
        self.assertEqual(res2.returncode, 0, f"--snapshot failed: {res2.stderr}")
        data2 = json.loads(res2.stdout)
        gen2 = data2["generation"]

        self.assertEqual(gen1, gen2, "Snapshots within TTL must share the exact same generation digest")
        self.assertEqual(data1["snapshot_id"], data2["snapshot_id"])

    def test_export_json_contract(self):
        """测试 --export json 输出契约、结构版本与元数据完整性。"""
        res = self.run_cli("--export", "json")
        self.assertEqual(res.returncode, 0, f"--export json failed: {res.stderr}")
        data = json.loads(res.stdout)

        self.assertEqual(data.get("schema_version"), "1.0.0")
        self.assertEqual(data.get("generator"), "cognitally")
        self.assertIn("snapshot_id", data)
        self.assertIn("generation", data)
        self.assertIn("exported_at", data)
        self.assertIn("usage", data)
        self.assertIn("daily_costs", data)
        self.assertIn("projects", data)

    def test_export_csv_contract(self):
        """测试 --export csv 输出契约、列名与行数据。"""
        res = self.run_cli("--export", "csv")
        self.assertEqual(res.returncode, 0, f"--export csv failed: {res.stderr}")
        lines = res.stdout.strip().splitlines()
        self.assertGreater(len(lines), 0, "CSV export must contain at least headers")

        header = lines[0]
        expected_columns = [
            "snapshot_generation", "agent", "period", "model", "input_tokens",
            "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "cost_usd", "pricing_provenance", "pricing_source", "cost_kind"
        ]
        for col in expected_columns:
            self.assertIn(col, header, f"CSV header missing required column: {col}")

    def test_export_to_file(self):
        """测试 --export --out <path> 写入文件功能。"""
        tmp_dir = tempfile.mkdtemp(prefix="cognitally-export-")
        try:
            target_file = os.path.join(tmp_dir, "export.json")
            res = self.run_cli("--export", "json", "--out", target_file)
            self.assertEqual(res.returncode, 0, f"--export --out failed: {res.stderr}")
            self.assertTrue(os.path.isfile(target_file), "Export file must be created")
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data.get("generator"), "cognitally")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_mcp_server_stdio_protocol(self):
        """验证标准只读 MCP Stdio 服务对 initialize、tools/list 及全部 6 个只读工具的协议支持。"""
        proc = subprocess.Popen(
            [sys.executable, SCRIPT_PATH, "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=ROOT_DIR
        )

        def send_msg(req):
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            return json.loads(line)

        try:
            # 1. initialize
            init_res = send_msg({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"}
            })
            self.assertEqual(init_res["result"]["serverInfo"]["name"], "cognitally")
            self.assertEqual(init_res["result"]["protocolVersion"], "2024-11-05")

            # 2. tools/list
            tools_res = send_msg({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            })
            tools = [t["name"] for t in tools_res["result"]["tools"]]
            required_tools = [
                "get_usage", "get_cost", "get_quota",
                "get_projects", "get_models", "get_accounting_status"
            ]
            for rt in required_tools:
                self.assertIn(rt, tools, f"MCP server missing tool: {rt}")

            # 3. tools/call: get_usage
            usage_res = send_msg({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_usage", "arguments": {"period": "today"}}
            })
            self.assertFalse(usage_res["result"].get("isError", False))
            u_data = json.loads(usage_res["result"]["content"][0]["text"])
            self.assertIn("agents", u_data)
            self.assertIn("generation", u_data)

            # 4. tools/call: get_cost
            cost_res = send_msg({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_cost", "arguments": {"period": "today"}}
            })
            self.assertFalse(cost_res["result"].get("isError", False))
            c_data = json.loads(cost_res["result"]["content"][0]["text"])
            self.assertIn("total_cost_usd", c_data)
            self.assertIn("agent_costs", c_data)

            # 5. tools/call: get_quota
            quota_res = send_msg({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_quota", "arguments": {}}
            })
            self.assertFalse(quota_res["result"].get("isError", False))
            q_data = json.loads(quota_res["result"]["content"][0]["text"])
            self.assertIn("quotas", q_data)

            # 6. tools/call: get_projects
            proj_res = send_msg({
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "get_projects", "arguments": {"limit": 10}}
            })
            self.assertFalse(proj_res["result"].get("isError", False))
            p_data = json.loads(proj_res["result"]["content"][0]["text"])
            self.assertIn("projects", p_data)

            # 7. tools/call: get_models
            models_res = send_msg({
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "get_models", "arguments": {"period": "today"}}
            })
            self.assertFalse(models_res["result"].get("isError", False))
            m_data = json.loads(models_res["result"]["content"][0]["text"])
            self.assertIn("models", m_data)

            # 8. tools/call: get_accounting_status
            status_res = send_msg({
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "get_accounting_status", "arguments": {}}
            })
            self.assertFalse(status_res["result"].get("isError", False))
            s_data = json.loads(status_res["result"]["content"][0]["text"])
            self.assertIn("doctor_report", s_data)
            self.assertIn("pricing_metadata", s_data)

        finally:
            if proc.stdin:
                proc.stdin.close()
            if proc.stdout:
                proc.stdout.close()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
