# TokDash / Cognitally — 生产级硬门禁 (Hard Gates SSOT)

> **唯一事实来源**：所有角色提示词引用本文件编号，不在各角色内重复改写数值。
> 最后对齐：`main` 工作流 v1 · 2026-09-17

---

## G-1 测试基准

| 项 | 规范 |
|----|------|
| **权威命令** | `python3 -m unittest discover -s tests -v`（退出码 0） |
| **等价命令** | `pnpm test` / `pytest -q`（须与 unittest 同 43 项，不得少测） |
| **当前计数** | **43** 项（变更后若增减须同步改 `AGENTS.md` 与本节） |
| **前端** | 触及 `src/` 或 `electron/` 时额外：`pnpm run typecheck`；发布级另需 `pnpm build`（仅 Release 角色或 AC 明确要求） |

**反例**：`43 passed` **不等于**「14 款 Agent 真机对账已签收」；后者需 `cognitally --doctor` 或工单 AC 单独约定。

---

## G-2 Python 依赖

- 采集、计费、存储、MCP、CLI 核心路径：**仅 CPython 标准库**。
- **禁止**向上述路径引入未授权的 pip 包（`requirements.txt` 新增、运行时 `import` 第三方库）。
- Node 依赖仅限 `package.json` 已声明的前端/Electron 栈。

---

## G-3 外观层与兼容

- 根目录 **`usage.30s.py`** 为 BitBar / Electron IPC / 历史 CLI 的 **Facade**。
- 不得删除既有公共导出或破坏 `tests/test_cli_contracts.py` 等契约测试所覆盖的符号与 CLI 行为。
- 品牌：**`cognitally`** 为主 CLI 名；**`tokdash`** 为兼容别名（`install.sh` 已 symlink）。

---

## G-4 产品边界

- **14 款**第一级 AI 编程 Agent 矩阵（见 `ARCHITECTURE.md` §3.1）；已裁剪的 scanner **不得**以新名字回流。
- MCP（`cognitally --mcp`）：**只读**工具集，零业务写入、零路由幻觉。

---

## G-5 安全与隐私

- 禁止将 API Key、session token、`.env` 内容提交进 Git。
- 配额 API 仅使用用户本机环境变量或文档约定的本地 secrets 路径。
- Electron：`contextIsolation: true`，`nodeIntegration: false`（不得为省事改回）。

---

## G-6 工作流与签收

- **实现者不得自批**；审计锚定 **Candidate Commit SHA**。
- **handoff_round ≤ 2**；超限熔断 → 人类决策（见 `auditor.md`）。
- 审计 **PASS** ≠ **发布授权**（见 `release.md`）。
- 非阻断意见一律 **`[SUGGESTION]`**，不得升格为 BLOCKER。

---

## G-7 仓库与远程（协作）

- 工作区 SSOT：`/home/jamesoldman/tokdash`（见 `PROJECT.md`）。
- `package.json` 的 `repository` 与 `git remote` 可能指向不同 GitHub 名（`cognitally` vs `tokdash`）；**发布前**须人决单一 canonical 远程，不在 Agent 流程中擅自 `push --force`。

---

## 行为检查表（审计 / 主控关单前勾选）

| # | 检查项 | 证据类型 |
|---|--------|----------|
| 1 | 变更在 TASK `scope_files` 或主控书面扩范围内 | diff / TASK |
| 2 | G-1 测试已跑且退出码 0 | 命令输出 |
| 3 | 未违反 G-2 / G-3 / G-4 | diff + grep |
| 4 | 无 G-5 凭据泄露 | diff |
| 5 | AC 逐条有 `[PASS]` 或审计等价认定 | TASK 对账表 |
| 6 | 无未授权 `git push` / 部署声称 | 实现报告声明 |
