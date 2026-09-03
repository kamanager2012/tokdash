<div align="center">

# ⏱️ TokDash

**Linux / Ubuntu 桌面 AI Token 与费用监控器**

*实时查看 AI 编程 Agent 的 Token 吞吐、提示词缓存与费用消耗。*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%2F%20Linux-orange.svg)](https://ubuntu.com/)
[![Electron](https://img.shields.io/badge/Electron-33.x-47848F.svg)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.x-38B2AC.svg)](https://tailwindcss.com/)

[English](README.md) | **简体中文**

</div>

---

## 📖 项目简介

**TokDash** 是一款面向 Linux 桌面的 AI 编程工具用量仪表盘与系统托盘应用，适合同时使用多个自主编程 Agent 的开发者。它通过低开销、被动、非侵入式地解析本机已有日志，集中展示 **Token 用量**、**提示词缓存读取**、**预估美元费用**以及**按项目归属的消耗情况**。

TokDash 的产品灵感来自 [cclank/tokei](https://github.com/cclank/tokei)（macOS 菜单栏应用），但针对 Linux 桌面进行了重新实现，提供深色/浅色主题、无边框窗口和系统托盘常驻等能力。

---

## ✨ 主要功能

- 🔒 **本地优先与透明隐私**：仅被动读取本机已有的会话记录以及 SQLite/JSONL 缓存文件。提示词、代码和上下文日志不会被上传到第三方遥测服务器；启用官方额度查询时，只会访问你已授权的服务商接口。
- ⚡ **完整 Token 指标拆分**：区分 **Prompt 输入**、**Completion 输出**与 **Cache Read**，避免缓存 Token 被重复计算。
- 💰 **可配置费用估算**：使用 OpenRouter 模型价格目录（`pricing.json`），并结合本地可自定义费率覆写（`pricing_overrides.json`），支持私有端点、折扣以及显式的定价来源标记。
- 📈 **近两周每日费用趋势**：通过交互式柱状图查看每日费用，并在悬停时展示各工具费用明细。
- 🤖 **多 Agent 套餐额度与窗口**：实时展示 Antigravity（Google AI Pro）、Codex Plus/Pro、Cursor Ultra 与 Grok 的额度使用和重置倒计时。
- 📂 **工作区与项目追踪**：按代码仓库聚合 Token、费用与会话数量，并检测本机项目正在监听的开发端口。
- 🌓 **现代桌面 UI 与系统托盘**：支持无边框深色/浅色界面、Ubuntu 原生系统托盘、最小化到托盘和快捷键切换。

---

## 🛠️ 支持的 AI 编程工具

TokDash 以只读方式被动解析各工具标准的本机会话日志，不作为网络拦截代理。

| Agent / 工具 | 检测位置 | 追踪指标 |
| :--- | :--- | :--- |
| **Codex CLI** | `~/.codex/` 会话 | Token、Reasoning、Cache Read、费用 |
| **Claude Code** | `~/.claude/projects/` JSONL 日志 | 输入、输出、Cache Read/Write、轮次 |
| **Antigravity / Gemini CLI** | 本地进程与会话存储 | Google AI Pro 5 小时窗口与套餐额度 |
| **Grok Build** | `~/.tokei/` / `~/.cc-switch/` | API Token、实时额度与会话 |
| **Kimi Code** | `~/.kimi-code/` 协议日志 | Agent 轮次 Token、模型与费用 |
| **OpenCode** | `~/.opencode/` | DeepSeek / 本地 LLM Token 遥测 |
| **Cursor Composer** | `~/.config/Cursor/` 授权数据 | 月度套餐消耗、按量费用、已用百分比 |
| **Pi Coding Agent** | `~/.pi/` Agent 运行记录 | 工具调用、输入/输出 Token |
| **Hermes Agent** | `~/.hermes/` 运行目录 | 会话数、Token 吞吐 |
| **DeepSeek Harness** | `~/.dsh/` community 会话 | JSONL 会话指标与模型路由 |

---

## 🚀 快速开始

### 环境要求

- **Ubuntu / Debian Linux**（20.04+）
- **Node.js** >= 18.0.0
- **Python** >= 3.10
- 推荐使用 **pnpm**（也可使用 `npm`）

### 安装与启动

```bash
# 1. 克隆仓库
git clone https://github.com/kamanager2012/tokdash.git
cd tokdash

# 2. 运行自动安装脚本
#    （安装依赖、构建前端并创建桌面启动器）
chmod +x install.sh
./install.sh

# 3. 启动 TokDash
./start.sh
```

> **桌面启动器**：执行 `install.sh` 后，可在 Ubuntu 中按下 `Super`（Windows 键），搜索 **TokDash**，直接从应用菜单启动。

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
