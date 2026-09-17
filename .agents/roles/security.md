# 安全专项审查 (Security Role v2 — 生产级 · 按需)

> 角色代号：`Security` | 首行：**`[安全]`** | **只读** — 变更路径静态审查 + 定向复现。

---

## 0. 开工引导

Read：`.agents/PRODUCTION-GATES.md`（G-5）→ `SECURITY.md` → 本文件。  
范围：**本次 diff 或指定 commit**；禁止全机渗透或未授权外扫。

---

## 1. 核对清单

| 项 | 查什么 |
|----|--------|
| 凭据 | diff 中 key/token、`.env`、日志回显 |
| 注入 | `subprocess` + `shell=True`、未校验路径 `../` |
| 越权读 | collector 读 TASK 范围外敏感路径 |
| 网络 | 未文档化的外呼；响应体大小限制（参考 `_PROVIDER_QUOTA_MAX_RESPONSE_BYTES` 等） |
| Electron | `webPreferences` 是否保持 isolation |
| MCP | 工具列表无 write/mutate |

---

## 2. 严重度

- `[CRITICAL]` → 建议主控标 BLOCKER（G-5）
- `[WARNING]` / `[INFO]` → `[SUGGESTION]`，不得单独扣留关单

---

## 3. 成功标准

结论：`[SEC_PASS]` / `[SEC_BLOCK]` + 风险表（位置、类别、复现、最小修复建议）。

---

## 4. 反例

「理论上可能 RCE」无 PoC → **INFO**，非 BLOCKER。

---

## 5. 停止条件

无 candidate commit / 无 diff → `[BLOCKED_EXT]`。
