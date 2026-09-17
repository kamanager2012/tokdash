# 多 Agent 工作流黄金少样本 (Few-Shot Golden Examples)

> 目的：为各类 AI 编程 Agent（如 Codex、Claude Code、DeepSeek、Cursor、Antigravity）提供高保真示范，统一输出口径与证据标准。

---

## 示例一：标准通过并成功关单 (Standard PASS)

### 1. 任务定义 (TASK-20260917-P0-SNAPSHOT)
```yaml
---
task_id: "TASK-20260917-P0-SNAPSHOT"
version: "1.0"
status: "SUBMITTED"
role: "implementer"
handoff_round: 1
base_commit: "3e42004"
candidate_commit: "950d6a1"
target_project: "tokdash"
scope_files:
    - "core/config.py"
---
```
#### 目标与验收条款
- **用户可见效果**：`--statusline` 首次冷启动耗时降低，高频读取命中缓存。
- **AC-1**：`_SNAPSHOT_TTL` 提升至 25.0s。
- **AC-2**：G-1 全量 41 项 unittest 100% 通过（权威命令见 `PRODUCTION-GATES.md`）。
- **AC-3**：无任何未授权 pip 依赖引入。

---

### 2. 实现交接报告 (Implementer Handoff)
- **候选提交**：`950d6a1b02...`
- **实际变更**：`core/config.py` 修改 1 行（L255）。
- **AC 逐项对账**：
  - `[AC-1 PASS]`：`grep _SNAPSHOT_TTL core/config.py` 输出 `_SNAPSHOT_TTL = 25.0`。
  - `[AC-2 PASS]`：运行 `python3 -m unittest discover -s tests -v` 输出 `Ran 41 tests` / OK，退出码 0。
  - `[AC-3 PASS]`：未修改 `package.json` 或添加外部库，仅修改常量。
- **状态声明**：本地已 Commit，未 Push，未 Deploy。
- **非阻断建议 (`SUGGESTION`)**：后续可考虑对不同 CLI 路由支持独立的 TTL 覆盖。

---

### 3. 独立审计裁决 (Auditor Verdict)
> **审计裁决：【通过 (PASS)】 | 适用版本：`950d6a1b02...`**

- **核实依据**：
  1. 检视 `git diff 3e42004..950d6a1`，仅改动 `_SNAPSHOT_TTL = 25.0`，爆炸半径 0.1%，符合预期。
  2. 独立运行 `pytest`，确认 41 个测试用例全部 PASSED。
  3. 未发现任何 BLOCKER 级硬违规。
- **建议处理**：实现者提出的路由级 TTL 建议已记录至备忘池，不阻碍本任务交付。
- **结论**：**关闭当前工单，允许合入**。

---

## 示例二：标准退回与定向修复闭环 (Standard REJECT -> FIX)

### 1. 独立审计退回报告 (Auditor Blocker Finding)
```yaml
---
task_id: "TASK-20260917-P1-GLM-PARSE"
status: "REJECTED"
role: "auditor"
handoff_round: 1
candidate_commit: "a1b2c3d4"
---
```
> **审计裁决：【退回 (FAIL)】 | 适用版本：`a1b2c3d4`**

#### 阻断项清单 (BLOCKER)
- **违反条款**：`AC-2`（必须向后兼容旧版无 model 字段的 JSON 记录）
- **代码位置**：`core/collectors/glm.py:58-62`
- **触发条件**：当历史日志条目中缺少 `model` 键时。
- **实际影响**：抛出未捕获的 `KeyError: 'model'`，导致 `--doctor` 扫描异常退出。
- **复现依据**：
  ```bash
  python3 -c "from core.collectors.glm import scan_glm; scan_glm(cache={}, rollout_files=['tests/fixtures/corrupt_glm.jsonl'])"
  # 产生 KeyError: 'model' at core/collectors/glm.py:60
  ```

---

### 2. 实现者定向修复交接 (Implementer Round 2)
```yaml
---
task_id: "TASK-20260917-P1-GLM-PARSE"
status: "SUBMITTED"
role: "implementer"
handoff_round: 2
candidate_commit: "e5f6a7b8"
---
```
- **修复措施**：在 `core/collectors/glm.py:60` 改为 `record.get("model", "glm-4")` 安全防御获取。
- **复现测试**：重跑上述复现命令，成功返回字典，退出码 0。
- **回归测试**：`pytest` 41 个测试全部通过。

---

### 3. 独立审计第 2 轮复核 (Auditor Final Pass)
> **审计裁决：【通过 (PASS)】 | 适用版本：`e5f6a7b8`**

- **核实依据**：确认 `KeyError` 缺陷已被 `record.get` 消除，41/41 测试全绿。
- **结论**：**关闭当前工单**。
