---
task_id: "TASK-20260917-OCR-DELEGATE-HARNESS"
version: "1.0"
status: "APPROVED"
role: "auditor"
handoff_round: 1
base_commit: "664e783"
candidate_commit: "e4f9906"
target_project: "tokdash"
scope_files:
    - ".agents/scripts/run_ocr_audit.py"
    - "tests/test_ocr_harness.py"
    - ".agents/roles/auditor.md"
    - ".agents/PRODUCTION-GATES.md"
    - "AGENTS.md"
---

# 任务工单：实现阿里 Open Code Review (OCR) 委托审计 Harness

> 依据：`.agents/templates/TASK.md` 生产级规范，将本机阿里 `open-code-review v1.12.4` CLI 深度接入 TokDash 审计工作流，实现一键变更分域、规则匹配与自动化门禁复核。

---

## 1. 任务定义 (Task Specification)

- **任务编号**：`TASK-20260917-OCR-DELEGATE-HARNESS`
- **基线版本**：`664e783`
- **问题背景**：
  TokDash 提示词体系与阿里 Open Code Review (OCR) 审计应用在红线理念上高度同构，但此前缺乏工程自动化连接，Auditor 需手动走读或零散执行命令。本机已预装 `open-code-review v1.12.4`（`/home/jamesoldman/.local/bin/ocr`），具备强大的变更分域与智能规则匹配能力。
- **交付目标**：
  1. 编写 `.agents/scripts/run_ocr_audit.py`：自动调度 `ocr delegate preview` 与 `ocr delegate rule`，抓取变更代码及命中的专业审查规则，联动运行 G-1 单元测试和任务契约校验。
  2. 编写 `tests/test_ocr_harness.py`：将 OCR 适配器与解析逻辑纳入自动化回归保护。
  3. 更新 `.agents/roles/auditor.md`：增加 OCR Delegate 审计标准执行流程与探针建议。
  4. 同步更新 `PRODUCTION-GATES.md` 与 `AGENTS.md` 中的测试基准计数。
- **验收条款**：
  - `[AC-1]`：`.agents/scripts/run_ocr_audit.py` 能够成功识别 `ocr` CLI，并正确执行 `--from` 和 `--to` 区间代码分域审查。
  - `[AC-2]`：支持 `--json` 与人类可读 Markdown 两种输出模式，并能在工作区受污染或测试失败时准确阻断。
  - `[AC-3]`：`tests/test_ocr_harness.py` 包含针对 OCR 二进制探测、preview 输出解析以及端到端执行的单元测试。
  - `[AC-4]`：`python3 -m unittest discover -s tests -v` 新增测试后全部通过（退出码 0），G-1 门禁基准同步更新。

---

## 2. 实现交接报告 (Implementer Handoff)

- **候选提交**：`e4f9906`
- **变更摘要**：
  - 新增脚本：`.agents/scripts/run_ocr_audit.py`（支持自动探测二进制、`delegate preview` 分域解析、`delegate rule` 规则提取、Git 状态检查、G-1 测试执行与 `--task` 契约校验）。
  - 新增测试：`tests/test_ocr_harness.py`（3 项单元测试：二进制探测、preview Markdown 解析、端到端 CLI 执行）。
  - 文档与门禁更新：`.agents/roles/auditor.md` 增加 OCR 委托审计 SOP 与微型探针建议；`PRODUCTION-GATES.md` 与 `AGENTS.md` 将 G-1 基准由 44 提升至 **47** 项。
- **AC 逐项对账**：
  - `[AC-1 PASS]`：`python3 .agents/scripts/run_ocr_audit.py --from 664e783 --to e4f9906` 运行通过，正确识别 2 个审查目标与 3 个排除项。
  - `[AC-2 PASS]`：`--json` 输出格式符合预期，包含 `preview`, `rule_resolution`, `g1_tests`, `working_tree_clean` 等核心账本。
  - `[AC-3 PASS]`：`tests/test_ocr_harness.py` 全部 3 项用例 100% 通过。
  - `[AC-4 PASS]`：`python3 -m unittest discover -s tests -v` ➔ Ran 47 tests, OK。

---

## 3. 独立审计核验 (Auditor Verdict)

> **审计裁决：【通过 PASS】 | 适用版本：`e4f9906`**

- **核验依据**：
  1. 自动化 OCR Harness 自举复核：`python3 .agents/scripts/run_ocr_audit.py --from 664e783 --to e4f9906` ➔ **Overall Status: PASS**。
  2. 权威 G-1 测试复跑：`python3 -m unittest discover -s tests -v` ➔ **Ran 47 tests in 4.64s, OK**。
  3. 任务契约校验：`python3 .agents/scripts/validate_task.py .agents/tasks/TASK-20260917-OCR-DELEGATE-HARNESS.md --repo .` ➔ **PASSED**。
  4. 代码范围约束：`git diff 664e783..e4f9906` 严格限制在声明的 `scope_files` 列表内，未引入任何非标依赖（G-2 达标）。
- **结论**：AC-1 ~ AC-4 全部满足，无 BLOCKER 阻断项，准予关单合并。
