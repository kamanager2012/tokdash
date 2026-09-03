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

**TokDash** is a dedicated Linux desktop dashboard and system tray utility designed for developers using autonomous AI coding tools. It performs zero-overhead, non-invasive local log parsing across all installed coding agents to deliver instant visibility into **total tokens**, **prompt cache hits**, **real USD expenditure**, and **per-project consumption**.

Inspired by [cclank/tokei](https://github.com/cclank/tokei) (macOS menu bar app), TokDash is completely re-engineered as a modern Linux desktop application with dark/light themes, frameless window aesthetics, and live system tray integration.

---

## ✨ Features

- 🔒 **100% Local & Zero-Cloud Privacy**: Reads only local session transcripts and SQLite/JSONL cache files on your disk. No prompts, code, or tokens are ever sent to remote analytics servers.
- ⚡ **Full Token Metrics Breakdown**: Distinguishes **Prompt Input**, **Completion Output**, and **Cache Reads** (saving you from double-counting cached tokens).
- 💰 **Accurate Cost Estimation**: Synchronized with OpenRouter's 300+ model pricing catalog (`pricing.json`) plus customizable local rate overrides (`pricing_overrides.json`).
- 📈 **Two-Week Daily Expense Trend**: Interactive daily bar chart with granular tool breakdown on hover.
- 🤖 **Multi-Agent Quota & Window Limits**: Real-time quota countdowns for Antigravity (Google AI Pro), Codex Plus/Pro, Cursor Ultra, and Grok.
- 📂 **Workspace & Project Tracking**: Aggregates token spend and session counts per code repository and detects local listening ports.
- 🌓 **Modern UI & System Tray**: Frameless dark/light mode with native Ubuntu system tray icon, window minimize-to-tray, and hotkey toggling.

---

## 🛠️ Supported AI Coding Agents

TokDash passively inspects standard session storage locations without modifying agent behavior:

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

1. **专为 Linux 打造**：为 Ubuntu 桌面量身定制的独立 Electron 客户端，完美支持系统托盘常驻与无边框极简暗黑卡片风格。
2. **零侵入纯本地解析**：所有用量数据均直接扫描本地 `~/.codex/`、`~/.claude/` 等工具的既有日志文件，绝不在外部安装常驻后门或上传你的提问与代码。
3. **真实 Token 流量与计费**：精准剥离 Prompt 输入、补全输出与缓存命中（Cache Read），让你清晰看到缓存帮你省下的费用。
4. **项目级归属与端口检测**：展示每个项目目录的累计 Token 消耗与当前正在监听的本地调试端口。

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!
Please check out the [Contributing Guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 📄 License & Acknowledgements

- Licensed under the [MIT License](LICENSE).
- Special thanks to [@cclank](https://github.com/cclank) for the original macOS [tokei](https://github.com/cclank/tokei) concept.
