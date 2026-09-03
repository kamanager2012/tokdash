# 🏛️ TokDash Architecture & Strategic Roadmap

> **Target Architecture, Two-Dimensional Provenance Model, and System Design Guidelines**  
> *Last Updated: 2026-09-04*

---

## 1. Product Positioning & Strategic Vision

TokDash is a local-first, low-overhead desktop application for Linux/Ubuntu designed to passively monitor, account for, and optimize AI coding agent token consumption, quotas, and expenditures.

### Evolutionary Roadmap

```text
[Current Baseline]
L1: Trusted AI Usage Observatory (可信 AI 编程用量观察者)
    - Distinguish Input, Output, Cache Read, and Reasoning tokens
    - Rigorous deduplication & mathematical reconciliation invariants
    - Fail-closed pricing overrides and granular provenance contracts
    - Native Linux Electron frameless desktop & system tray integration

[Next Phase: In Progress]
L2: Trusted AI Resource Observatory (可信 AI 资源与订阅观察站)
    - Two-Dimensional Provenance Model: Measurement × Pricing Provenance
    - Unified Subscription & QuotaWindow domain models (shared windows, saturation)
    - Standardized internal Collector Contracts (detect / scan / health)
    - Diagnostic subsystem (`tokdash doctor`)

[Future Phase]
L3: Agent-Readable Resource Control Plane (智能 Agent 可读控制平面)
    - Read-only Stdio MCP Server (get_usage, get_cost, get_quota, get_remaining)
    - Non-intrusive local desktop quota saturation alerts (80%, 95%, 100%)
    - Utility-driven quota-aware recommendation (marginal cost × remaining capacity)
```

---

## 2. Competitive Landscape: TokDash vs. Tokie

A structural comparison with peer projects (such as `vamshivittali76/Tokie`):

| Dimension | TokDash Approach | Tokie Approach | Architectural Verdict |
| :--- | :--- | :--- | :--- |
| **Accounting Depth** | Micro-level deduplication, cache alias prevention, daily zero-loss reconciliation | Event-store level aggregation | **TokDash Core Barrier (Frozen)** |
| **Pricing Provenance** | 7-tier granular enum, fail-closed alias privilege escalation prevention | Confidence enum (exact / estimated / inferred) | **TokDash Granular Advantage** |
| **Usage Provenance** | Introducing orthogonal `MeasurementProvenance` | Confidence tiering on UsageEvent | **Two-Dimensional Matrix Adopted** |
| **Agent Coverage** | **14 First-Class Mainstream Agents** prioritized on Linux desktop | Broad API vendors + generic CLI | **Observability on Coding Agents** |
| **Desktop Experience**| Linux Electron frameless UI + native Tray + workspace repo/ports tracking | Browser localhost Dashboard + TUI | **TokDash Native Linux Advantage** |
| **Domain Model** | Transitioning from monolithic scanner dicts to unified domain entities | Unified `UsageEvent` + `Subscription` | **TokDash Adopting Domain Model** |
| **Diagnostics** | Native `tokdash doctor` CLI & JSON inspection | `tokie doctor` | **Implemented in TokDash** |
| **Agent Interface** | Read-Only MCP Planned (pure query, zero hallucinated routing) | Read-Only MCP with static YAML routing table | **Adopting Read-Only Interface** |

---

## 3. Product Boundary & Scoping Decision: The 14 First-Class Agents

TokDash deliberately avoids spreading engineering maintenance across redundant or experimental niche tools.

### 3.1. The Canonical 14 Mainstream AI Coding Agents
1. **Claude Code** (`~/.claude/projects/`)
2. **Codex CLI** (`~/.codex/`)
3. **Antigravity / Gemini** (AGY)
4. **Cursor Composer** (`~/.config/Cursor/`)
5. **Grok Build** (`~/.tokei/`, `~/.cc-switch/`)
6. **Grok Bot** (`~/.grok-bot/` — maintained as an independent product, distinct from Grok Build)
7. **Kimi Code** (`~/.kimi-code/`)
8. **DeepSeek Harness** (`~/.dsh/`)
9. **OpenCode** (`~/.opencode/`)
10. **Hermes Agent** (`~/.hermes/`)
11. **Pi Coding Agent** (`~/.pi/`)
12. **GLM Code (ZCode)** (`~/.zcode/cli/db/db.sqlite`)
13. **CodeBuddy / WorkBuddy** (`~/.codebuddy/`, `~/.workbuddy/`)
14. **Qoder** (`~/.qoder/` — Qoder IDE, Work, and CLI unified)

### 3.2. Explicit Pruning & Ecosystem Decisions
- **Qoder vs. Qwen Code**: Factually, Qwen Code and Qoder are distinct, actively evolving products within Alibaba's ecosystem (Qwen Code continues rapid standalone releases and supports installing Qoder plugins). However, from TokDash's product perspective, maintaining duplicate adapters for two overlapping Alibaba coding agent stacks is inefficient. TokDash canonically standardizes on **Qoder** as the first-class supported platform, deprecating the standalone Qwen Code scanner.
- **De-scoped & Pruned Scanners**: `MiMoCode`, `Prime Agent`, `OpenClaw`, `Zed Quota`, and `Sub2API` have been cleanly pruned from active scanning pipelines.
- **Provider Quota Integrations**: Integrations like `Z.AI Quota` (serving GLM/ZCode) and `Antigravity Quota` are maintained as provider quota adapters rather than independent coding agents.

---

## 4. The Two-Dimensional Provenance Matrix
 
 To guarantee uncompromised accounting integrity without conflating token measurements with pricing estimates, TokDash enforces a **Two-Dimensional Provenance Matrix**:
 
```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TOKEE / TOKDASH TWO-DIMENSIONAL MATRIX                   │
├──────────────────────────────┬──────────────────────────────────────────────┤
│ 1. Measurement Provenance    │ 2. Pricing Provenance                        │
│ (How was token usage read?)  │ (Where did the rate/cost come from?)         │
├──────────────────────────────┼──────────────────────────────────────────────┤
│ • provider_authoritative     │ • authoritative (local billing ledger)       │
│   (Official vendor quota API)│ • exact_catalog (official OpenRouter ID)     │
│ • log_exact                  │ • exact_alias (formatting normalization)     │
│   (Local machine JSONL/DB)   │ • price_equivalent (cross-gen rate equality) │
│ • derived                    │ • manual_proxy (human-reviewed representative│
│   (Turn-level parsed context)│ • family_proxy (heuristic keyword fallback)  │
│ • estimated                  │ • unknown (unrecognized, rate = 0.0)         │
│   (Cursor heuristic chars)   │                                              │
│ • unknown                    │                                              │
└──────────────────────────────┴──────────────────────────────────────────────┘
```

---

## 5. Subsystem Architecture

### 5.1. Ingestion Pipeline & Collector Contract
Each collector targeting a mainstream AI coding agent implements a predictable lifecycle:
- **`detect() -> bool`**: Passively inspects standard file paths or processes without disk lock contention.
- **`scan(since_offset) -> List[Record]`**: Idempotent, byte-offset guarded incremental scanner with EOF truncation resilience.
- **`health() -> CollectorHealth`**: Reports permissions, file counts, and last activity timestamps to `tokdash doctor`.

### 5.2. Quota & Subscription Domain Model
Moving beyond provider-specific `if-else` blocks, all rate limits are mapped to unified Quota Windows:
```typescript
export interface QuotaWindow {
  id: string;               // e.g. "claude-pro-5h", "codex-weekly", "cursor-monthly"
  provider: string;         // e.g. "anthropic", "openai", "cursor", "google"
  shared_with: string[];    // Tools sharing this pool, e.g. ["claude-code", "claude-desktop"]
  used: number;             // Current consumption in period
  limit: number;            // Total window capacity
  remaining: number;        // Remaining available allowance
  saturation_pct: number;   // used / limit * 100
  resets_at: string | null; // ISO timestamp of replenishment
  source: 'api' | 'local_state' | 'inferred';
  confidence: 'authoritative' | 'estimated';
}
```

### 5.3. Self-Diagnostics Subsystem (`tokdash doctor`)
Provides instant visibility into local agent configurations:
```bash
tokdash doctor
# Or structured output for UI consumption:
tokdash doctor --json
```
Inspects all 14 mainstream agents (Claude, Codex, Grok, Grok-Bot, Cursor, Antigravity, Kimi, DeepSeek, OpenCode, Hermes, Pi, GLM, CodeBuddy, Qoder).

---

## 6. Engineering Invariants & Security Principles

1. **Non-Negotiable Math Invariants**:
   - $\text{Total Cost} \equiv \sum \text{Tool Costs}$ within $\pm \$0.01$ floating point rounding error.
   - $\text{Total Tokens} \ge \text{Input} + \text{Output} + \text{Cache Read}$.
2. **Fail-Closed Principle**:
   - Unknown models, missing rates, or corrupted configuration rules strictly default to zero estimated cost and `manual_proxy`/`unknown` classification. Never escalate to `exact` without cryptographic provenance.
3. **Local-First & Zero-Exfiltration**:
   - TokDash operates strictly in read-only mode over local session storage. User prompts, code files, and credentials are never transmitted to external telemetry servers.
