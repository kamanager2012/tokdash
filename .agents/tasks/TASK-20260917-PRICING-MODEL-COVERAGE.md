---
task_id: "TASK-20260917-PRICING-MODEL-COVERAGE"
version: "1.0"
status: "APPROVED"
role: "auditor"
handoff_round: 1
base_commit: "18940eb"
candidate_commit: "c7523da"
target_project: "tokdash"
scope_files:
    - "core/pricing/catalog.py"
    - "pricing_overrides.json"
    - "tests/test_pricing.py"
    - ".agents/PRODUCTION-GATES.md"
    - "AGENTS.md"
---

# 任务工单与交接报告：扩展计费模型覆盖与主流 Agent 模型精准解析

> 依据：`.agents/templates/TASK.md` 生产级规范，解决真实 Agent 日志中 16 款模型因大小写、厂商前缀缺失或本土模型未映射而归入 `unknown` 零成本的问题。

---

## 1. 任务定义（主控角色发起）

| 字段 | 填报内容与规范 |
| :--- | :--- |
| **任务 ID / 版本** | `TASK-20260917-PRICING-MODEL-COVERAGE` / `v1.0` |
| **目标项目与仓库** | TokDash (`/home/jamesoldman/tokdash`)，分支 `main` |
| **当前负责角色** | `Implementer` -> `Auditor` |
| **文件责任范围** | `core/pricing/catalog.py`, `pricing_overrides.json`, `tests/test_pricing.py`, `.agents/PRODUCTION-GATES.md`, `AGENTS.md` |
| **基线提交 (Base Ref)** | `18940eb` |
| **用户目标与授权来源** | 用户明确选择路线 1（计费模型准确度与新模型覆盖），针对真实日志 16 款未决模型治理 |
| **生效规则与事实入口** | `shared-rules.md`, `PRODUCTION-GATES.md` (G-1, G-2, G-4) |

### 本次交付目标与边界
- **用户可见的新增/修复能力**：
  1. 真实日志中原 16 款 `unknown` 零成本模型降至 ≤3 款，解决如 `Hy4 Preview` 大小写不匹配、`Minimax M3`、`Kimi K3`、`Step Explore`、`Muse Spark`、`Ox Alpha` 及中文 `合成` 标签未决问题。
  2. 计费核心保持 fail-closed 语义，纯未知模型依然安全返回 `unknown` 且 `$0.00`。
- **本次必须完成的工作 (Scope IN)**：
  1. 在 `core/pricing/catalog.py` 中增强 `_normalize()` 厂商智能前缀映射与合成标签处理。
  2. 在 `catalog.py` 中增加大小写不敏感的别名查表 `_OV_ALIASES_LOWER`。
  3. 在 `pricing_overrides.json` 中配置 `stealth/ox-alpha` 官方免计费条目与本土别名。
  4. 扩增 `tests/test_pricing.py` 回归契约。
  5. 递增 G-1 测试计数至 45 项，保持全绿。
- **本次明确不作的工作 (Scope OUT)**：
  - 严禁向 TokDash 引入任何代码审计、代码评审或 OCR 等非 Token 计量业务代码。
- **客观验收条件 (Acceptance Criteria)**：
  - `[AC-1]`：`Minimax M3`、`Kimi K3`、`Hy4 Preview`、`Muse Spark 1.2 Contributor`、`Ox Alpha`、`Step Explore`、`合成` 等不再落入 `unknown`，均精准解析到对应合法目录或代理。
  - `[AC-2]`：未知模型依然严格保持 fail-closed 语义，不得假冒 Opus 或产生非零脏成本。
  - `[AC-3]`：`test_pricing.py` 新增对本土与新兴 Agent 模型解析的回归测试。
  - `[AC-4]`：`python3 -m unittest discover -s tests -v` 必须 45/45 全绿通过（G-1）。

---

## 2. 实现交接报告（实现角色交付）

| 字段 | 实际落地情况与证据 |
| :--- | :--- |
| **候选提交 (Candidate Ref)** | `c7523da` |
| **工作树状态** | 干净（除本任务工单外无脏修改） |
| **实际变更文件清单** | `core/pricing/catalog.py`, `pricing_overrides.json`, `tests/test_pricing.py`, `.agents/PRODUCTION-GATES.md`, `AGENTS.md` |

### 逐项验收对账表
| 验收编号 | 期望条件 | 实际状态 | 证明依据 |
| :--- | :--- | :--- | :--- |
| `AC-1` | 13 款主流模型精准解析 | PASS | `tests/test_pricing.py:test_expanded_vendor_and_agent_model_resolution` 逐项断言全部通过；实测未决模型由 16 款降至 3 款 |
| `AC-2` | 未知模型 fail-closed | PASS | `tests/test_pricing.py:test_unknown_models_do_not_fallback_to_opus` 保持通过 |
| `AC-3` | 回归测试套件覆盖 | PASS | `test_expanded_vendor_and_agent_model_resolution` 覆盖 Minimax, Kimi, Hy4, Ox, Muse, Step, 合成 |
| `AC-4` | G-1 硬门禁 45 项退出码 0 | PASS | `python3 -m unittest discover -s tests -v` (Ran 45 tests in 4.575s, OK) |

### 运行环境与实测数据
- **实际执行的命令及退出码**：
  - `python3 -m unittest discover -s tests -v` -> 退出码 0 (45 tests passed)
  - `pnpm run typecheck` -> 退出码 0 (tsc --noEmit)
  - `python3 .agents/scripts/validate_task.py .agents/tasks/TASK-20260917-PRICING-MODEL-COVERAGE.md --repo . --strict` -> 退出码 0
- **未提交 / 未推送声明**：代码已提交至本地 commit `c7523da`，尚未 push 远端，等待审计签收与人类发布授权。

---

## 3. 独立审计核验结论（审计角色判定）

| 审查项 | 审计结论 |
| :--- | :--- |
| **适用候选版本** | `c7523da` |
| **综合裁定** | `[满足本次验收关闭]` |

### 审计核验详情
1. **G-1 测试硬门禁**：执行 `python3 -m unittest discover -s tests -v`，45 项测试全部通过，无 skipped/failed。
2. **G-2 标准库依赖约束**：计费模块核心仅使用标准库 (`json`, `os`, `re`, `typing`)，无外来 pip 依赖侵入。
3. **G-3 契约兼容**：未修改 `usage.30s.py` 顶层公共接口，CLI 和 BitBar 行为一致。
4. **G-4 边界核准**：完全聚焦于 14 款 Agent 模型的计费归一化与价格解析，无任何代码审计等非域代码。
5. **AC 达成**：AC-1 至 AC-4 全部对账通过，无 BLOCKER 项。符合防无限审计原则，直接签收关闭工单。
