<div align="center">

# ⏱️ TokDash

**Desktop AI Token & Cost Monitor for Linux / Ubuntu**

*Real-time token throughput, prompt caching, and expenditure tracking for AI coding agents.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%2F%20Linux-orange.svg)](https://ubuntu.com/)
[![Electron](https://img.shields.io/badge/Electron-33.x-47848F.svg)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.x-38B2AC.svg)](https://tailwindcss.com/)

[English](#-overview) | [中文说明](#-中文说明)

</div>

---

## 📖 Overview

**TokDash** is a dedicated Linux desktop dashboard and system tray utility designed for developers using autonomous AI coding tools. It performs low-overhead, passive, and non-invasive local log parsing across installed coding agents to provide visibility into **token volume**, **prompt cache reads**, **estimated USD expenditure**, and **per-project consumption**.

Inspired by [cclank/tokei](https://github.com/cclank/tokei) (macOS menu bar app), TokDash is completely re-engineered as a modern Linux desktop application with dark/light themes, frameless window aesthetics, and live system tray integration.

---

## ✨ Features

- 🔒 **100% Local & Zero-Cloud Privacy**: Reads only local session transcripts and SQLite/JSONL cache files on your disk. No prompts, code, or tokens are ever sent to remote analytics servers.
- ⚡ **Full Token Metrics Breakdown**: Distinguishes **Prompt Input**, **Completion Output**, and **Cache Reads** (saving you from double-counting cached tokens).
- 💰 **Configurable Cost Estimation**: Estimated using OpenRouter's 300+ model pricing catalog (`pricing.json`) combined with customizable local rate overrides (`pricing_overrides.json`) to accommodate private endpoints and discounts.
- 📈 **Two-Week Daily Expense Trend**: Interactive daily bar chart with granular tool breakdown on hover.
- 🤖 **Multi-Agent Quota & Window Limits**: Real-time quota countdowns for Antigravity (Google AI Pro), Codex Plus/Pro, Cursor Ultra, and Grok.
- 📂 **Workspace & Project Tracking**: Aggregates token spend and session counts per code repository and detects local listening ports.
- 🌓 **Modern UI & System Tray**: Frameless dark/light mode with native Ubuntu system tray icon, window minimize-to-tray, and hotkey toggling.

---

## 🛠️ Supported AI Coding Agents

TokDash passively inspects standard local session logs (read-only) without acting as an interception proxy:

| Agent / Tool | Detection Target | Metrics Tracked |
| :--- | :--- | :--- |
| **Codex CLI** | `~/.codex/` sessions | Tokens, Reasoning, Cache Reads, Cost |
| **Claude Code** | `~/.claude/projects/` JSONL logs | Input, Output, Cache Read/Write, Turns |
| **Antigravity / Gemini CLI** | Local process & session store | Google AI Pro 5h rate limits & quotas |
| **Grok Build** | `~/.tokei/` / `~/.cc-switch/` | Real API tokens, live quotas & sessions |
| **Kimi Code** | `~/.kimi-code/` protocol logs | Agent turn tokens, models & cost |
| **OpenCode** | `~/.opencode/` | DeepSeek / local LLM token telemetry |
| **Cursor Composer** | `~/.config/Cursor/` auth | Monthly plan spend, auto-spend, % used |
| **Pi Coding Agent** | `~/.pi/` agent runs | Tool calls, input/output token counts |
| **Hermes Agent** | `~/.hermes/` runtime | Sessions, token throughput |
| **DeepSeek Harness** | `~/.dsh/` community sessions | JSONL session metrics & model routes |

---

## 🚀 Quick Start

### Prerequisites

- **Ubuntu / Debian Linux** (20.04+)
- **Node.js** >= 18.0.0
- **Python** >= 3.10
- **pnpm** (recommended) or `npm`

### Installation & Launch

```bash
# 1. Clone the repository
git clone https://github.com/kamanager2012/tokdash.git
cd tokdash

# 2. Run the automated installer (installs dependencies, builds UI, and creates desktop launcher)
chmod +x install.sh
./install.sh

# 3. Launch TokDash
./start.sh
```

> **Desktop Launcher**: After running `install.sh`, you can also press `Super` (Windows key) on Ubuntu, search for **TokDash**, and launch it directly from the application menu.

### Development Mode

```bash
# Install dependencies
pnpm install

# Start Vite dev server
pnpm dev

# In another terminal, run Electron against dev server
pnpm start
```

---

## 🇨🇳 中文说明

### 核心设计与特性

1. **专为 Linux 打造**：为 Ubuntu / Linux 桌面开发的独立 Electron 客户端，支持系统托盘常驻与深色/浅色主题。
2. **被动只读解析**：直接扫描本地 `~/.codex/`、`~/.claude/` 等工具落盘的既有日志与 SQLite 数据库，不作为网络拦截代理（Proxy），完全不干扰原有 Agent 工作流。
3. **细分 Token 吞吐与缓存**：明确区分 Prompt 输入、补全输出与缓存命中（Cache Read），避免将缓存读取重复计入新增流量。
4. **透明公开估价**：基于 OpenRouter 实时价目库（`pricing.json`）与本地自定覆写表（`pricing_overrides.json`）进行费用测算与统计参考。
5. **工程项目归属与监听检测**：根据会话的工作区路径聚合各个代码仓库的累计消耗，并附带检测当前运行的本地开发端口。

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
Please check out the [Contributing Guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 📄 License & Acknowledgements

- Licensed under the [MIT License](LICENSE).
- Special thanks to [@cclank](https://github.com/cclank) for the original macOS [tokei](https://github.com/cclank/tokei) concept.
