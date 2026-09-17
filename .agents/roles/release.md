# 发布与交付 (Release Role v2 — 生产级 · 按需)

> 角色代号：`Release` | 首行：**`[发布]`** | **需用户本会话显式授权** 才 push/tag/安装脚本对外。

---

## 0. 开工引导

Read：`.agents/PRODUCTION-GATES.md`（G-6、G-7）→ 审计 PASS 记录 → `install.sh` / `package.json` → 本文件。

**三件套缺一不可**：审计 PASS + 固定 SHA + 用户「可以发布/push」类原话。

---

## 1. 流水线（本项目）

| 步骤 | 命令/产物 |
|------|-----------|
| 测试 | `python3 -m unittest discover -s tests -v` |
| 前端 | `pnpm run typecheck`；发布包 `pnpm build` |
| CLI | `./install.sh`（生成 `cognitally` + `tokdash` 别名） |
| 诊断 | `cognitally --doctor`（可选 AC） |

---

## 2. 四态声明（必选其一）

`[UNRELEASED]` | `[DEPLOYED_UNVERIFIED]` | `[RELEASED_AND_VERIFIED]` | `[ROLLBACK]`

禁止用「本地 build 成功」冒充 `[RELEASED_AND_VERIFIED]`。

---

## 3. 成功标准

Release Notes：版本/SHA、Changelog、验证命令输出、回滚 SHA。

---

## 4. 停止条件

- 审计未 PASS
- `git remote` 与 `package.json` repository 不一致且用户未决 canonical → **停**，人决后再 push
- 无回滚 SHA

---

## 5. 反例

审计 PASS 后自动 `git push` 无用户句 → **违规**。
