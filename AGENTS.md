# TokDash (Cognitally Observatory) — AI Agent Workflow Entrance

> Multi-Agent Workflow Specification v1 (2026-09-17)
> 核心原则：不同职责使用不同提示词，但共用同一套已批准规则；提示词之外必须有真实权限、任务交接和可核实结果。

## 1. 公共规则与约束 (Shared Rules)
所有在此仓库工作的 AI Agent（Codex、Claude Code、Cursor、Antigravity/AGY 等）必须首先遵守：
- [.agents/rules/shared-rules.md](file:///.agents/rules/shared-rules.md)
- [.agents/templates/PROJECT.md](file:///.agents/templates/PROJECT.md)

## 2. 角色分工与提示词 (Roles)
按职责分配角色，严禁角色自批自改：
- **主控协调 (Orchestrator)**：[.agents/roles/orchestrator.md](file:///.agents/roles/orchestrator.md) — 严格只读，需求拆解与工单分配
- **功能实现 (Implementer)**：[.agents/roles/implementer.md](file:///.agents/roles/implementer.md) — 局部修改，定向验证，输出交接报告
- **独立审计 (Auditor)**：[.agents/roles/auditor.md](file:///.agents/roles/auditor.md) — 独立会话核验固定 Commit，执行防无限审计机制
- **按需专家**：[架构](file:///.agents/roles/architect.md) | [产品](file:///.agents/roles/product.md) | [研究](file:///.agents/roles/researcher.md) | [安全](file:///.agents/roles/security.md) | [发布](file:///.agents/roles/release.md)

## 3. 单次任务与交接契约 (Contracts)
每次工单交付均遵循标准契约：
- [.agents/templates/TASK.md](file:///.agents/templates/TASK.md)

## 4. 关键硬门禁 (Hard Gates)
1. **测试基准**：必须保持全部 41 个 pytest 测试通过 (`pytest`)。
2. **零外部重依赖**：仅使用 Python 标准库，严禁向采集与计费核心引入无授权 pip 依赖。
3. **向后兼容性**：`usage.30s.py` 作为外观层必须保持所有公共与内部符号导出，兼容 BitBar、Electron IPC 及 CLI。
4. **防止无限审计**：验收条件全部满足即刻关闭工单，非阻断建议统一归入 `[SUGGESTION]`。
