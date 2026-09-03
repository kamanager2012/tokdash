import unittest
import os
import sys
import tempfile
import json
import importlib.util

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(ROOT_DIR, "usage.30s.py")

spec = importlib.util.spec_from_file_location("usage_module", SCRIPT_PATH)
usage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage_module)


class TestCorruptedLogRecovery(unittest.TestCase):
    """验证各类采集器面对未完成写入、半截断行、空字节乱码以及损坏日志时的自愈与容错能力。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_codex_truncated_trailing_record_recovery(self):
        """测试 Codex 文件末尾包含并发未写完的半截断残破行时的自愈与截断保护。"""
        _codex_complete_offset = getattr(usage_module, "_codex_complete_offset")
        _iter_codex_usage_records = getattr(usage_module, "_iter_codex_usage_records")

        file_path = os.path.join(self.tmp_dir.name, "rollout-truncated.jsonl")

        # 写入 1 条完整有效记录 + 1 条半截断且未加换行符的残破记录
        valid_event = {
            "type": "event_msg",
            "timestamp": "2026-09-03T10:00:00.000Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 150, "reasoning_output_tokens": 50},
                    "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 150, "reasoning_output_tokens": 50}
                }
            }
        }
        valid_line = json.dumps(valid_event) + "\n"
        truncated_line = '{"type": "event_msg", "timestamp": "2026-09-03T10:05:00.000Z", "payload": {"type": "token_count", "info": {"last_token'

        with open(file_path, "wb") as f:
            f.write(valid_line.encode("utf-8"))
            f.write(truncated_line.encode("utf-8"))

        file_size = os.path.getsize(file_path)
        valid_size = len(valid_line.encode("utf-8"))

        # 1. 验证 complete_offset 准确避开残破行，停留在最后一个换行符
        offset = _codex_complete_offset(file_path, file_size)
        self.assertEqual(offset, valid_size, "complete_offset must point exactly after the last complete newline")

        # 2. 验证 stream parser 在 end_offset 处安全截断，产出且仅产出 1 条有效 token 记录
        token_records = []
        for kind, record in _iter_codex_usage_records(file_path, end_offset=offset):
            if kind == "token":
                token_records.append(json.loads(record.decode("utf-8")))

        self.assertEqual(len(token_records), 1, "Must only yield 1 complete record")
        self.assertEqual(token_records[0]["payload"]["info"]["last_token_usage"]["input_tokens"], 1000)

        # 3. 验证即使没有指定 end_offset，EOF 残破行也会被内部校验丢弃，不导致异常抛出
        raw_token_records = []
        for kind, record in _iter_codex_usage_records(file_path):
            if kind == "token":
                raw_token_records.append(json.loads(record.decode("utf-8")))
        self.assertEqual(len(raw_token_records), 1, "Trailing malformed record must be dropped without exception")

    def test_codex_corrupted_middle_lines_resilience(self):
        """测试 JSONL 文件中间混入脏字节、空字节和非 JSON 乱码时的跳过能力。"""
        _iter_codex_usage_records = getattr(usage_module, "_iter_codex_usage_records")
        file_path = os.path.join(self.tmp_dir.name, "rollout-dirty.jsonl")

        event1 = {
            "type": "event_msg",
            "timestamp": "2026-09-03T10:00:00.000Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 500, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0},
                    "total_token_usage": {"input_tokens": 500, "cached_input_tokens": 0, "output_tokens": 100, "reasoning_output_tokens": 0}
                }
            }
        }
        event2 = {
            "type": "event_msg",
            "timestamp": "2026-09-03T10:10:00.000Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 800, "cached_input_tokens": 100, "output_tokens": 200, "reasoning_output_tokens": 0},
                    "total_token_usage": {"input_tokens": 1300, "cached_input_tokens": 100, "output_tokens": 300, "reasoning_output_tokens": 0}
                }
            }
        }

        with open(file_path, "wb") as f:
            f.write(json.dumps(event1).encode("utf-8") + b"\n")
            f.write(b"\x00\x00\x00\xff\xfe corrupted binary line\n")
            f.write(b"\n")  # Empty line
            f.write(b"{malformed json without closing quote: true\n")
            f.write(json.dumps(event2).encode("utf-8") + b"\n")

        records = []
        for kind, record in _iter_codex_usage_records(file_path):
            if kind == "token":
                records.append(json.loads(record.decode("utf-8")))

        self.assertEqual(len(records), 2, "Parser must cleanly recover and parse the 2 valid events around dirty lines")
        self.assertEqual(records[0]["payload"]["info"]["last_token_usage"]["input_tokens"], 500)
        self.assertEqual(records[1]["payload"]["info"]["last_token_usage"]["input_tokens"], 800)

    def test_claude_broken_lines_skip_and_accounting(self):
        """测试 Claude 扫描器遇到中间行截断和非法结构时的平稳恢复。"""
        _claude_usage = getattr(usage_module, "_claude_usage")

        # 1. 正常 assistant usage
        valid_line = json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-03T12:00:00.000Z",
            "message": {
                "model": "claude-3-5-sonnet-20241022",
                "usage": {
                    "input_tokens": 2000,
                    "output_tokens": 300,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 0
                }
            }
        })
        u = _claude_usage(valid_line, want_dt=True)
        self.assertIsNotNone(u)
        self.assertEqual(u["in"], 2000)
        self.assertEqual(u["out"], 300)

        # 2. 截断 JSON 行
        broken_line = '{"type": "assistant", "timestamp": "2026-09-03T12:05:00.000Z", "message": {"model": "clau'
        self.assertIsNone(_claude_usage(broken_line, want_dt=True))

        # 3. 乱码二进制行
        garbage_line = "\x00\x00\x00\x1f\x8b\x08 not a json string"
        self.assertIsNone(_claude_usage(garbage_line, want_dt=True))

        # 4. 缺少 usage 字段的普通事件
        empty_usage_line = json.dumps({"type": "assistant", "message": {"text": "hello"}})
        self.assertIsNone(_claude_usage(empty_usage_line, want_dt=True))

    def test_opencode_cache_protection_on_concurrent_read_error(self):
        """验证 OpenCode 扫描器在单文件临时处于写入损坏状态时，旧缓存条目不被作为 stale 误删除。"""
        scan_opencode = getattr(usage_module, "scan_opencode")
        range_bounds = getattr(usage_module, "range_bounds")

        opencode_dir = os.path.join(self.tmp_dir.name, "opencode_storage")
        sess_dir = os.path.join(opencode_dir, "ses_test_001")
        os.makedirs(sess_dir, exist_ok=True)
        msg_file = os.path.join(sess_dir, "msg_001.json")

        # 写入初始合法消息 (满足 _opencode_message_day)
        valid_msg = {
            "id": "msg_001",
            "role": "assistant",
            "time": {"created": 1788480000000},
            "tokens": {"input": 1500, "output": 250, "reasoning": 50}
        }
        with open(msg_file, "w", encoding="utf-8") as f:
            json.dump(valid_msg, f)

        old_opencode_env = os.environ.get("TOKDASH_OPENCODE_DIR")
        os.environ["TOKDASH_OPENCODE_DIR"] = opencode_dir
        try:
            cache = {}
            bounds = range_bounds()
            res1 = scan_opencode(bounds, cache)
            self.assertIn("opencode", cache)
            self.assertIn(msg_file, cache["opencode"])

            # 模拟文件被外部编辑器并发写入损坏（变为残破无法解析的内容）
            with open(msg_file, "w", encoding="utf-8") as f:
                f.write('{"id": "msg_001", "tokens": {')

            # 再次运行扫描
            res2 = scan_opencode(bounds, cache)

            # 关键断言：旧缓存条目绝不能被当成 stale 文件 pop 掉！
            self.assertIn(msg_file, cache["opencode"],
                          "Cache entry must NOT be purged from cache when transient read/parse error occurs")
        finally:
            if old_opencode_env is not None:
                os.environ["TOKDASH_OPENCODE_DIR"] = old_opencode_env
            else:
                os.environ.pop("TOKDASH_OPENCODE_DIR", None)


if __name__ == "__main__":
    unittest.main()
