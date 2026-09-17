# 工具轻量接线指南 (TOOL-ADAPTERS v1)

> 核心设计哲学：**一份规则主来源，多工具极薄适配，绝不维护多套复制粘贴的正文**。
> 官方核对基准：OpenAI AGENTS.md / Skills、Anthropic CLAUDE.md / Subagents、Cursor Rules、Antigravity (AGY) Customizations。

---

## 1. OpenAI Codex / ChatGPT CLI

Codex 遵循层级发现机制，支持项目根目录与子目录的指令加载。

### 接线方式
1. **项目全局入口**：在项目根目录保留 `AGENTS.md`，作为公共约束总入口。
2. **角色注入方式**：
   - 任务发起时在 prompt 中引用具体角色路径：
     ```text
     请先阅读 .agents/rules/shared-rules.md 与 .agents/roles/implementer.md，执行以下任务：...
     ```
3. **程序性知识打包**：将复杂且复用度高的调试、对账过程放置于 `.agents/skills/<skill-name>/SKILL.md`，Codex 自动发现并按需渐进加载。

---

## 2. Anthropic Claude Code

Claude Code 默认读取 `CLAUDE.md`，不支持原生跨项目通配递归加载。

### 接线方式
在项目根目录创建极薄的 `CLAUDE.md`：
```markdown
# Project Guidelines
@.agents/rules/shared-rules.md
@.agents/templates/PROJECT.md
```
使用 `@` 语法直接导入公共规则，不重复定义正文。

### 子 Agent 配置
如需使用 Claude Code 原生子 Agent，在 `.claude/agents/` 创建软链接或薄配置文件指向 `.agents/roles/*.md`，保持正文唯一。

---

## 3. Cursor IDE / Composer

Cursor 支持 `.cursor/rules/*.mdc`。

### 接线方式（已落地）

仓库内文件：**`.cursor/rules/tokdash-workflow.mdc`**（`alwaysApply: true`）。  
入口：**`AGENTS.md`** → `PRODUCTION-GATES.md` → 角色 `roles/*.md`。

Reload Window 后，Composer 应先读硬门禁再认领角色。若 hooks 未加载，仍须手动 Read 角色全文（文本只读≠物理隔离）。

---

## 4. Google Antigravity (AGY) / Gemini CLI

Antigravity 具备最强的原生子 Agent 工具 ACL（访问控制列表）隔离能力。

### 接线方式
在 `.agents/agents/<role-name>/agent.md` 中配置原生 Subagent，**在底层物理切断工具权限**：
- `orchestrator`（主控）：配置纯只读工具集，**绝无 `write_to_file` 或 `replace_file_content`**。
- `implementer`（实现）：配置完整读写及局部执行工具。
- `auditor`（审计）：配置只读工具 + `run_command`（仅用于测试命令），**严禁配置代码写入工具**。

---

## 5. 跨工具通用启动提示词 (Universal Bootstrap Prompt)

在任何 CLI 或网页端开启新会话时，直接粘贴此标准模板即可激活角色：

```text
项目：<项目名称，如 TokDash>
角色：<Orchestrator / Implementer / Auditor / 专家之一>
任务：<任务 ID 与简短目标>

【启动指令】：
1. 请先完整阅读 .agents/rules/shared-rules.md 与对应的角色规范（.agents/roles/<role>.md）。
2. 核对当前 Git Commit 基线，不修改已存在的未提交修改。
3. 严格在本次授权范围内执行，满足验收条件后立即收口交付，禁止触发无限审计。
```
