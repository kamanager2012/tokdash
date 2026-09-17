# TokDash (Cognitally Observatory) — AI Agent Workflow Entrance

> Multi-Agent Workflow **Production v2** (2026-09-17)  
> 原则：**不同职责 = 不同提示词** + **共用硬门禁** + **真实权限/交接/可核实证据**。

---

## 0. 开工引导（任意角色必先完成）

1. 确认工作目录：`/home/jamesoldman/tokdash`，执行 `git status -sb` 与 `git log -1 --oneline`。
2. **必读**（按序，不得跳过）：
   - [.agents/PRODUCTION-GATES.md](.agents/PRODUCTION-GATES.md) — 硬门禁 SSOT
   - [.agents/rules/shared-rules.md](.agents/rules/shared-rules.md) — 公共纪律
   - [.agents/templates/PROJECT.md](.agents/templates/PROJECT.md) — 项目事实映射
3. 认领角色后 **全文 Read** 对应 `roles/*.md`（或 AGY 子代理 `agents/*/agent.md`）。
4. 响应 **首行** 打角色标签：`[主控]` / `[实现]` / `[审计]` / `[架构]` / …（见各角色文件）。

**反例**：未读 `PRODUCTION-GATES.md` 就宣称「测试过了」；把 `pytest` 通过当作 14 Agent 真机对账签收。

---

## 1. 公共规则与约束

- [.agents/rules/shared-rules.md](.agents/rules/shared-rules.md)
- [.agents/templates/PROJECT.md](.agents/templates/PROJECT.md)
- [.agents/PRODUCTION-GATES.md](.agents/PRODUCTION-GATES.md)

---

## 2. 角色分工与提示词（生产级 Runbook）

| 角色 | 文件 | 权限摘要 |
|------|------|----------|
| **主控协调** | [.agents/roles/orchestrator.md](.agents/roles/orchestrator.md) | 只读；出 TASK、调度 |
| **功能实现** | [.agents/roles/implementer.md](.agents/roles/implementer.md) | 白名单内写码 + 定向测试 |
| **独立审计** | [.agents/roles/auditor.md](.agents/roles/auditor.md) | 只读 + 复现命令；禁写源码 |
| **架构** | [.agents/roles/architect.md](.agents/roles/architect.md) | 按需；RFC 只读 |
| **产品** | [.agents/roles/product.md](.agents/roles/product.md) | 按需；规格只读 |
| **研究** | [.agents/roles/researcher.md](.agents/roles/researcher.md) | 按需；调研只读 |
| **安全** | [.agents/roles/security.md](.agents/roles/security.md) | 按需；安全只读 |
| **发布** | [.agents/roles/release.md](.agents/roles/release.md) | 需显式授权才 push/部署 |

Antigravity 子代理薄壳（正文仍以 `roles/` 为准）：

- [.agents/agents/orchestrator/agent.md](.agents/agents/orchestrator/agent.md)
- [.agents/agents/implementer/agent.md](.agents/agents/implementer/agent.md)
- [.agents/agents/auditor/agent.md](.agents/agents/auditor/agent.md)

少样本与 TASK 校验：

- [.agents/templates/TASK.md](.agents/templates/TASK.md)
- [.agents/templates/TASK_EXAMPLES.md](.agents/templates/TASK_EXAMPLES.md)
- `python3 .agents/scripts/validate_task.py <报告.md> --repo .`

---

## 3. 单次任务契约

- 模板：[.agents/templates/TASK.md](.agents/templates/TASK.md)
- 迁移阶段：[.agents/ADOPTION.md](.agents/ADOPTION.md)
- 工具接线：[.agents/adapters/TOOL-ADAPTERS.md](.agents/adapters/TOOL-ADAPTERS.md)

---

## 4. 关键硬门禁（摘要 → 详情见 PRODUCTION-GATES）

1. **G-1**：44 项测试 — `python3 -m unittest discover -s tests -v` 退出码 0。
2. **G-2**：采集/计费核心仅标准库，无未授权 pip。
3. **G-3**：`usage.30s.py` Facade 与 CLI/IPC 契约不破。
4. **G-4**：14 Agent 产品边界；MCP 只读。
5. **G-5**：无凭据进库；Electron 安全基线。
6. **G-6**：独立审计、两轮熔断、PASS≠发布。
7. **防无限审计**：AC 满足且无 BLOCKER → **立即关单**；其余 → `[SUGGESTION]`。

---

## 5. Cursor / Claude 接线

- Cursor：`.cursor/rules/tokdash-workflow.mdc`（`alwaysApply` 路由到本文件与角色）
- Claude Code：根目录 `CLAUDE.md`（`@` 导入公共规则）

禁止 Cursor **Fast** 模式（任意 Cursor 提供的 fast 型号或 fast 开关均不得使用）。
