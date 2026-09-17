---
task_id: "TASK-20260917-FIX-MENUBAR-INVOCATION"
version: "1.0"
status: "APPROVED"
role: "auditor"
handoff_round: 1
base_commit: "b6ae593"
candidate_commit: "a9daf17"
target_project: "tokdash"
scope_files:
    - "usage.30s.py"
    - "tests/test_cli_contracts.py"
    - ".agents/PRODUCTION-GATES.md"
    - "AGENTS.md"
---

# 任务工单：修复 usage.30s.py 默认无参调用 KeyError 并补全回归测试

> 依据：`.agents/templates/TASK.md` 生产级规范，针对默认无参菜单渲染时抛出的 `KeyError: 'qwencode'` 致命缺陷进行定向修复与测试补全。

---

## 1. 任务定义 (Task Specification)

- **任务编号**：`TASK-20260917-FIX-MENUBAR-INVOCATION`
- **基线版本**：`b6ae593`
- **问题根因**：
  在 `usage.30s.py` 的默认主入口 `main()`（BitBar / SwiftBar 菜单格式渲染）中，历史裁剪残留代码直接硬编码访问 `d["qwencode"]["ranges"]["today"]`。由于 `qwencode` 扫描器已被 `test_product_boundary.py` 正式裁剪剔除，导致任何用户直接执行 `python3 usage.30s.py` 时必崩（`KeyError: 'qwencode'`）。
- **交付目标**：
  1. 彻底清除 `usage.30s.py` 中的 `qwencode` 残留块，补全 `zcode`（GLM Code）展示块。
  2. 对 `main()` 中所有 Agent 块采用防御性 `.get()` 读取，彻底杜绝字典取值未捕获异常。
  3. 在 `tests/test_cli_contracts.py` 中新增 `test_cli_default_menubar_contract`，将默认无参调用纳入自动化回归保障。
  4. G-1 测试基准计数从 43 同步递增为 44 项。
- **验收条款**：
  - `[AC-1]`：`python3 usage.30s.py`（无参）必须正常退出（退出码 0），正确打印菜单标头与工具详情，无异常。
  - `[AC-2]`：`usage.30s.py` 内部所有 Agent 详情渲染逻辑具备防御性，无非标库引入（G-2）。
  - `[AC-3]`：`tests/test_cli_contracts.py` 新增对默认无参调用的回归测试。
  - `[AC-4]`：`python3 -m unittest discover -s tests -v` 必须 44/44 全绿通过（G-1）。

---

## 2. 实现交接报告 (Implementer Handoff)

- **候选提交**：`a9daf17`
- **变更摘要**：
  - `usage.30s.py`：清理 `qwencode` 历史残留死代码，接入 `zcode`，对全部 Agent 块使用链式 `.get()` 安全访问。
  - `tests/test_cli_contracts.py`：新增 `test_cli_default_menubar_contract`，断言退出码 0、包含标头分段符 `---`、包含活跃 Agent 块且 stderr 无任何 Traceback / KeyError。
  - `PRODUCTION-GATES.md` & `AGENTS.md`：门禁 G-1 基准计数同步提升至 **44** 项。
- **AC 逐项对账**：
  - `[AC-1 PASS]`：`python3 usage.30s.py` 实测退出码 0，菜单各 Agent 渲染正常。
  - `[AC-2 PASS]`：维持纯标准库，未引入外部依赖。
  - `[AC-3 PASS]`：`test_cli_contracts.py` 已包含 `test_cli_default_menubar_contract`。
  - `[AC-4 PASS]`：`python3 -m unittest discover -s tests -v` ➔ Ran 44 tests in 5.102s, OK。

---

## 3. 独立审计核验 (Auditor Verdict)

> **审计裁决：【通过 (PASS)】 | 适用版本：`a9daf17`**

- **复现与核查命令**：
  1. `python3 usage.30s.py` ➔ 退出码 0，输出正常，无 KeyError 异常。
  2. `python3 -m unittest discover -s tests -v` ➔ 44 tests passed (退出码 0)。
  3. `git diff b6ae593..a9daf17` ➔ 范围严守 `scope_files`，无越界代码修改。
- **结论**：AC 满足，无 BLOCKER 阻断项，准予关单合并。
