<div align="center">

# 🧠 Cognitally

**Linux / Ubuntu 主权 AI 编程 Agent 可观测性与 Token 成本账本**  
*(原 TokDash 升级演进版 — 原生集成只读 Stdio MCP 服务与跨进程单飞防爆流式引擎)*

*实时查看 AI 编程 Agent 的 Token 吞吐、提示词缓存、套餐额度与只读 MCP 控制面。*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%2F%20Linux-orange.svg)](https://ubuntu.com/)
[![MCP 2024-11-05](https://img.shields.io/badge/MCP-2024--11--05-8A2BE2.svg)](https://modelcontextprotocol.io/)
[![Electron](https://img.shields.io/badge/Electron-33.x-47848F.svg)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.x-38B2AC.svg)](https://tailwindcss.com/)

[English](README.md) | **简体中文**

</div>

---

## 📖 项目简介

**Cognitally**（源自 *Cognitive* [AI认知推理] + *Tally* [严肃核算盘点]）是一款面向 Linux 桌面的专业本地 AI 资源可观测性套件、CLI 命令行工具与标准 **Model Context Protocol (MCP)** 服务端。它通过被动、零泄露、跨进程单飞流式解析本机已有日志，集中度量 **Token 真实吞吐**、**提示词缓存读取**、**严谨定价出处**、**套餐订阅额度**以及**按工作区项目归属的消耗情况**。

内置平滑无缝的一次性自动迁移：老用户 `~/.config/tokdash` 与 `~/.tokei` 历史账本与配置会自动安全迁移至 `~/.config/cognitally/`。

---

## ✨ 主要功能

- 🔌 **只读 Stdio MCP Server (`cognitally --mcp`)**：原生符合 MCP 2024-11-05 规范，支持 Claude Code、Cursor、Codex、OpenCode 等智能体直接挂载只读工具集（`get_usage`、`get_cost`、`get_quota`、`get_projects`、`get_models`、`get_accounting_status`），严格只读、零幻觉推荐。
- ⚡ **跨进程单飞流式引擎**：引入 flock 文件级单飞快照门控，杜绝高并发并发解析导致的内存暴涨（规避传统扫描器 3.3GB~12GB 瞬时内存峰值）。
- 🔒 **本地优先与透明隐私**：绝不上报任何代码或提示词。官方额度查询仅在具备本地合法凭据时直连官方端点。
- 📤 **规范化数据导出 (`cognitally --export json/csv`)**：提供带状态世代摘要校验的结构化 JSON 与细粒度 CSV 导出。

- ⚡ **完整 Token 指标拆分**：区分 **Prompt 输入**、**Completion 输出**与 **Cache Read**，避免缓存 Token 被重复计算。
- 💰 **可配置费用估算**：使用 OpenRouter 模型价格目录（`pricing.json`），并结合本地可自定义费率覆写（`pricing_overrides.json`），支持私有端点、折扣以及显式的定价来源标记。
- 📈 **近两周每日费用趋势**：通过交互式柱状图查看每日费用，并在悬停时展示各工具费用明细。
- 🤖 **多 Agent 套餐额度与窗口**：实时展示 Antigravity（Google AI Pro）、Codex Plus/Pro、Cursor Ultra 与 Grok 的额度使用和重置倒计时。
- 📂 **工作区与项目追踪**：按代码仓库聚合 Token、费用与会话数量，并检测本机项目正在监听的开发端口。
- 🌓 **现代桌面 UI 与系统托盘**：支持无边框深色/浅色界面、Ubuntu 原生系统托盘、最小化到托盘和快捷键切换。

---

## 🛠️ 支持的 AI 编程工具

Cognitally 以只读方式被动解析各工具标准的本机会话日志，不作为网络拦截代理。

| Agent / 工具 | 检测位置 | 追踪指标 |
| :--- | :--- | :--- |
| **Claude Code** | `~/.claude/projects/` JSONL 日志 | 输入、输出、Cache Read/Write、会话轮次与费用 |
| **Codex CLI** | `~/.codex/` 会话 | Token、Reasoning、Cache Read、费用 |
| **Grok Build** | `~/.tokei/` / `~/.cc-switch/` | API Token、实时额度、重置窗口与费用 |
| **Grok Bot** | `~/.grok-bot/` / 本地日志 | 机器人交互轮次、Token 吞吐 |
| **Cursor Composer** | `~/.config/Cursor/` 授权数据 | 月度套餐消耗、按量费用、已用百分比与重置倒计时 |
| **Antigravity / Gemini CLI** | 本地进程与会话存储 | Google AI Pro 5 小时额度窗口与逐步 Token |
| **Kimi Code** | `~/.kimi-code/` 协议日志 | Agent 轮次 Token、模型路由与费用 |
| **DeepSeek Harness** | `~/.dsh/` community 会话 | JSONL 会话指标、模型路由与费用 |
| **OpenCode** | `~/.opencode/` 本地存储与 SQLite | DeepSeek / 本地 LLM Token 遥测与费用 |
| **Hermes Agent** | `~/.hermes/` 运行目录 | 本地账本权威记录、会话数与 Token 吞吐 |
| **Pi Coding Agent** | `~/.pi/` Agent 运行记录 | 工具调用、输入/输出 Token |
| **GLM Code (智谱)** | `~/.zcode/` CLI SQLite 数据库 | 智谱 GLM-5 系列 Token 吞吐与会话明细 |
| **CodeBuddy / WorkBuddy** | `~/.codebuddy/` / `~/.workbuddy/` | 腾讯代码助手会话轮次与 Token 消耗 |
| **Qoder** | `~/.qoder/` 工作区与 SQLite | Qoder IDE / Work / CLI 多端交互与 Token 计量 |

> **设计说明**：Cognitally 重点聚焦以上 14 类生产级主流 AI 编程 Agent，保证核心采集链路的高可靠性与严格平账；精简边缘小众工具。

---

## 🚀 快速开始

### 环境要求

- **Ubuntu / Debian Linux**（20.04+）
- **Node.js** >= 22.12.0
- **Python** >= 3.10
- 推荐使用 **pnpm@9**（Node ≥22.12；也可用 `npm install`）

### 安装与启动

```bash
# 1. 克隆仓库
git clone https://github.com/kamanager2012/tokdash.git
cd tokdash

# 2. 运行自动安装脚本
#    （安装依赖、构建前端并创建 'cognitally' CLI 及桌面启动器）
chmod +x install.sh
./install.sh

# 3. CLI 与桌面端使用
cognitally --doctor       # 运行系统环境与 14 款 Agent 采集器健康体检
cognitally --mcp          # 启动标准 Stdio MCP 服务（供 Claude Code / Cursor / Codex 挂载）
cognitally --export json  # 导出规范化原子快照至 JSON
cognitally --export csv   # 导出细粒度每日模型消耗至 CSV
./start.sh                # 启动无边框 Linux 桌面观测台
```

> **桌面启动器**：执行 `install.sh` 后，可在 Ubuntu 中按下 `Super`（Windows 键），搜索 **Cognitally**，直接从应用菜单启动。

### 开发模式

```bash
# 安装依赖
pnpm install

# 启动 Vite 开发服务器
pnpm dev

# 另开一个终端，让 Electron 连接开发服务器
pnpm start
```

---

## 🤝 参与贡献

欢迎提交代码、Issue 和功能建议。

请先阅读 [贡献指南](CONTRIBUTING.md) 与 [行为准则](CODE_OF_CONDUCT.md)。

---

## 📄 许可证与致谢

- 本项目采用 [MIT License](LICENSE)。
- 特别感谢 [@cclank](https://github.com/cclank) 提供 macOS [tokei](https://github.com/cclank/tokei) 的原始产品思路。
