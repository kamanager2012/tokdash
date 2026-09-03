# Contributing to TokDash

Thank you for your interest in contributing to TokDash! We welcome contributions, bug reports, and feature requests from the community.

---

## 🛠️ Development Setup

TokDash is an Electron desktop app built with React, TypeScript, Tailwind CSS, and Python:

- **Frontend**: Vite + React 18 + Tailwind CSS + Lucide Icons
- **Desktop Shell**: Electron 33 (Linux / Ubuntu native frameless window & system tray)
- **Telemetry Engine**: Python 3 backend (`usage.30s.py`) scanning local AI tool logs

### Prerequisites

- **Node.js**: >= 18.0.0
- **pnpm**: >= 9.0.0 (or npm)
- **Python**: >= 3.10

### Getting Started

1. Fork and clone the repository:
   ```bash
   git clone https://github.com/kamanager2012/tokdash.git
   cd tokdash
   ```

2. Install dependencies:
   ```bash
   pnpm install
   ```

3. Run in development mode:
   ```bash
   # Terminal 1: Vite dev server
   pnpm dev

   # Terminal 2: Electron runner
   pnpm start
   ```

4. Type checking & production build:
   ```bash
   pnpm typecheck
   pnpm build
   ```

---

## 📐 Project Structure

```
tokdash/
├── electron/           # Electron main process & IPC preload bridges
│   ├── main.cjs        # Window lifecycle, system tray, and Python subprocess IPC
│   └── preload.cjs     # Context bridge (window.tokdash API)
├── src/                # React frontend application
│   ├── components/     # UI widgets (cards, breakdown table, chart, modal)
│   ├── types.ts        # TypeScript data contracts & period types
│   ├── utils.ts        # Shared token/currency formatting and tool metadata
│   ├── App.tsx         # Root container with theme & polling logic
│   └── index.css       # Tailwind CSS styling
├── usage.30s.py        # Local AI telemetry scraper & aggregator
├── pricing.json        # OpenRouter-synchronized model price catalogue
├── pricing_overrides.json # Local model aliases and rate overrides
└── install.sh          # Desktop entry registration and autostart script
```

---

## 📋 Pull Request Guidelines

1. **Keep Changes Focused**: Please ensure PRs address a specific issue or feature.
2. **Ensure Clean Builds**: Run `pnpm typecheck` and `pnpm build` locally before submitting.
3. **Privacy First**: Never hardcode personal tokens, user paths, or session transcripts in code or tests.
4. **Follow Semantic Commits**:
   - `feat: ...` for new features or tool support
   - `fix: ...` for bug fixes or pricing adjustments
   - `docs: ...` for documentation updates
   - `refactor: ...` for code quality and structural improvements
