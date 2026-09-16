<div align="center">

# 🧠 Cognitally

**Sovereign AI Coding Agent Observability & Token Cost Ledger for Linux / Ubuntu**  
*(Formerly TokDash — evolved with Stdio MCP Server & Single-Flight Streaming)*

*Real-time token throughput, prompt caching, quota tracking, and read-only MCP control plane for AI coding agents.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%2F%20Linux-orange.svg)](https://ubuntu.com/)
[![MCP 2024-11-05](https://img.shields.io/badge/MCP-2024--11--05-8A2BE2.svg)](https://modelcontextprotocol.io/)
[![Electron](https://img.shields.io/badge/Electron-33.x-47848F.svg)](https://www.electronjs.org/)
[![React](https://img.shields.io/badge/React-18.x-61DAFB.svg)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.x-38B2AC.svg)](https://tailwindcss.com/)

**English** | [简体中文](README.zh-CN.md)

</div>

---

## 📖 Overview

**Cognitally** (from *Cognitive* + *Tally*) is a local-first, low-overhead desktop observatory, CLI tool, and standard **Model Context Protocol (MCP)** server for developers using autonomous AI coding tools. It performs passive, zero-leakage local log parsing across 14 mainstream coding agents to provide mathematical certainty into **token volume**, **cache reads**, **cost provenance**, **subscription quotas**, and **project attribution**.

Automatic seamless one-time migration is supported from legacy `~/.config/tokdash` and `~/.tokei`.

---

## ✨ Features

- 🔌 **Read-Only Stdio MCP Server (`cognitally --mcp`)**: Native support for the MCP specification (2024-11-05). Claude Code, Cursor, Codex, and OpenCode can query current token consumption, quotas, and costs directly via 6 strictly read-only tools (`get_usage`, `get_cost`, `get_quota`, `get_projects`, `get_models`, `get_accounting_status`).
- ⚡ **Single-Flight Refresh & Streaming Engine**: Shared flock-protected canonical snapshots prevent redundant concurrent parsing and eliminate memory spikes on live rollouts.
- 🔒 **Local-First & Transparent Privacy**: Reads local session transcripts and SQLite/JSONL cache files on your disk for passive accounting. No prompts, code, or context logs are uploaded to third-party telemetry servers.
- 📤 **Canonical Export (`cognitally --export json/csv`)**: Export deterministic, schema-versioned token and cost datasets to JSON or CSV.

- ⚡ **Full Token Metrics Breakdown**: Distinguishes **Prompt Input**, **Completion Output**, and **Cache Reads**, avoiding cache-token double counting.
- 💰 **Configurable Cost Estimation**: Uses OpenRouter's model pricing catalog (`pricing.json`) together with customizable local rate overrides (`pricing_overrides.json`) for private endpoints, discounts, and explicit pricing provenance.
- 📈 **Two-Week Daily Expense Trend**: Interactive daily bar chart with per-tool cost breakdowns on hover.
- 🤖 **Multi-Agent Quota & Window Limits**: Real-time quota countdowns for Antigravity (Google AI Pro), Codex Plus/Pro, Cursor Ultra, and Grok.
- 📂 **Workspace & Project Tracking**: Aggregates token spend and session counts per code repository and detects local listening ports.
- 🌓 **Modern UI & System Tray**: Frameless dark/light mode with a native Ubuntu system tray icon, minimize-to-tray behavior, and hotkey toggling.

---

## 🛠️ Supported AI Coding Agents

Cognitally passively inspects standard local session logs in read-only mode and does not act as an interception proxy.

| Agent / Tool | Detection Target | Metrics Tracked |
| :--- | :--- | :--- |
| **Claude Code** | `~/.claude/projects/` JSONL logs | Input, Output, Cache Read/Write, Turns & Estimated Cost |
| **Codex CLI** | `~/.codex/` sessions | Tokens, Reasoning, Cache Reads, Estimated Cost |
| **Grok Build** | `~/.tokei/` / `~/.cc-switch/` | Real API tokens, live quotas, windows & cost |
| **Grok Bot** | `~/.grok-bot/` / local logs | Bot interaction turns & token throughput |
| **Cursor Composer** | `~/.config/Cursor/` auth | Monthly plan spend, auto-spend, % used & reset countdown |
| **Antigravity / Gemini CLI** | Local process & session store | Google AI Pro 5h rate limits, quotas & per-step tokens |
| **Kimi Code** | `~/.kimi-code/` protocol logs | Agent turn tokens, model routing & cost |
| **DeepSeek Harness** | `~/.dsh/` community sessions | JSONL session metrics, model routing & cost |
| **OpenCode** | `~/.opencode/` storage & SQLite | DeepSeek / local LLM token telemetry & cost |
| **Hermes Agent** | `~/.hermes/` runtime | Authoritative local ledger, sessions, token throughput |
| **Pi Coding Agent** | `~/.pi/` agent runs | Tool calls, input/output token counts |
| **GLM Code** | `~/.zcode/` CLI SQLite database | Zhipu GLM-5 series tokens & session metrics |
| **CodeBuddy / WorkBuddy** | `~/.codebuddy/` / `~/.workbuddy/` | Tencent coding assistant turns & token counts |
| **Qoder** | `~/.qoder/` workspace & SQLite | Qoder IDE / Work / CLI multi-target tokens & metrics |

> **Design Note**: Cognitally explicitly focuses on these 14 first-class production AI coding agents, ensuring rock-solid ingestion pipelines and strict mathematical reconciliation. Peripheral niche tools are de-emphasized.

---

## 🚀 Quick Start

### Prerequisites

- **Ubuntu / Debian Linux** (20.04+)
- **Node.js** >= 18.0.0
- **Python** >= 3.10
- **pnpm** recommended (or `npm`)

### Installation & Launch

```bash
# 1. Clone the repository
git clone https://github.com/kamanager2012/tokdash.git
cd tokdash

# 2. Run the automated installer
#    (installs dependencies, builds UI, and creates 'cognitally' CLI + desktop launcher)
chmod +x install.sh
./install.sh

# 3. CLI & Desktop Usage
cognitally --doctor       # Run system & 14-agent health check
cognitally --mcp          # Launch standard Stdio MCP server for Claude Code / Codex / Cursor
cognitally --export json  # Export canonical snapshot to JSON
cognitally --export csv   # Export daily model breakdown to CSV
./start.sh                # Launch frameless Linux desktop observatory
```

> **Desktop Launcher**: After running `install.sh`, press `Super` (Windows key) on Ubuntu, search for **Cognitally**, and launch it from the application menu.

### Development Mode

```bash
# Install dependencies
pnpm install

# Start the Vite development server
pnpm dev

# In another terminal, run Electron against the development server
pnpm start
```

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome.

Please read the [Contributing Guide](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 📄 License & Acknowledgements

- Licensed under the [MIT License](LICENSE).
- Special thanks to [@cclank](https://github.com/cclank) for the original macOS [tokei](https://github.com/cclank/tokei) concept.
