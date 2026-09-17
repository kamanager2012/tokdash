---
task_id: "TASK-YYYYMMDD-ID"
version: "1.1"
# status: DRAFT | IN_PROGRESS | SUBMITTED | APPROVED | REJECTED | BLOCKED
status: "DRAFT"
role: "implementer"
# domain: architecture | frontend | backend | fullstack
domain: "fullstack"
# Max 2 handoff rounds before circuit breaker escalation to human
handoff_round: 1
# Git Commit SHA before changes (or 'HEAD~1')
base_commit: "HEAD~1"
# Git Commit SHA after changes
candidate_commit: ""
target_project: "tokdash"
scope_files:
    - "core/collectors/xxx.py"
# Single-writer rule: 每个文件在同一时段有且仅有唯一的写入方
file_write_owners:
    "core/collectors/xxx.py": "implementer"
---

# 单次任务与交接契约模板 (TASK Template v1.1)

> 目的：在主控协调 (Orchestrator)、架构 (Architect)、前端 (Frontend)、后端 (Backend) 与独立审计 (Auditor) 之间建立无歧义、防扯皮、附带可复现证据的唯一交接标准。
> 核心原则：**按专业分工审计，统一形成修复方案；按责任边界实施，按完整用户链路验收。**
> 约定：小任务可压缩为简明摘要；必须包含上述 YAML Frontmatter 元数据块以便自动化 linter 校验。

---

## 1. 任务定义（主控角色发起）

| 字段 | 填报内容与规范 |
| :--- | :--- |
| **任务 ID / 版本** | 例如 `TASK-20260917-P2-01` / `v1.1` |
| **目标项目与仓库** | 项目名称、本地工作目录绝对路径、Git 远程仓库 |
| **专业领域与主责方** | `architecture` / `frontend` / `backend` / `fullstack`（单一主责方，指定协同方） |
| **当前负责角色** | `Implementer`（实现者）或指定子 Agent |
| **文件唯一写入责任 (Single-Writer)** | 每个文件指定唯一修改方；共享接口定义、公共类型、数据库迁移明确归属 |
| **文件责任范围** | 明确限定允许触碰的文件列表或目录（如 `core/collectors/*.py`） |
| **基线提交 (Base Ref)** | 开始修改前 HEAD 的 Git Commit SHA（未核实写“未核实”） |
| **用户目标与授权来源** | 用户对话具体指示、RFC 编号或工单来源 |
| **生效规则与事实入口** | 关联的 `shared-rules.md`、`PROJECT.md` 或项目约定 |

### 本次交付目标与边界
- **用户可见的新增/修复能力**：
- **本次必须完成的工作 (Scope IN)**：
- **本次明确不作的工作 (Scope OUT)**：
- **客观验收条件 (Acceptance Criteria)**：
  1. `[AC-1]`：
  2. `[AC-2]`：

### 执行与权限约束
- **允许写入路径**：严格限定目标路径，遵循 Single-Writer Rule，禁止并发写同一文件
- **操作授权明细**：
  - 本地文件写入：`[是/否]`
  - 定向单元测试执行：`[是/否]`
  - 外部模型或真实 API 调用：`[是/否]`
  - Git Commit / Push：`[需单独授权]`
- **测试与资源边界**：限制并发数、超时时间、严禁跑全量长时压测

---

## 2. 实现交接报告（实现角色交付）

| 字段 | 实际落地情况与证据 |
| :--- | :--- |
| **候选提交 (Candidate Ref)** | 本次产出的 Git Commit SHA 或暂存快照 |
| **工作树状态** | `git status` 是否干净；是否有保留的用户未提交修改 |
| **实际变更文件清单** | `git diff --stat` 概括 |

### 逐项验收对账表
| 验收编号 | 期望条件 | 实际状态 (`PASS` / `FAIL` / `BLOCKED`) | 证明依据（命令、输出摘要、代码行） |
| :--- | :--- | :--- | :--- |
| `AC-1` | ... | PASS | `python3 -m unittest discover -s tests -v` 退出码 0 |
| `AC-2` | ... | PASS | 接口输出符合预期 |

### 专业落地证据 (Domain Evidence)
- **前端证据**：区分源码推断与实际浏览器/DOM/控制台/网络证据（路由参数、空数据、加载与失败态）。
- **后端证据**：区分协议正确（200 OK）、模拟测试绿灯与真实业务持久化数据落地证据。

### 运行环境与实测数据
- **实际执行的命令及退出码**：
- **未运行项目及合理解释**：
- **真实 API / Token 消耗情况（如有）**：
- **未提交 / 未推送 / 未部署声明**：真实注明当前状态
- **剩余外部阻断与非阻断建议 (`[SUGGESTION]`)**：

---

## 3. 独立审计核验结论（审计角色判定）

> 审计原则：在独立会话中基于固定候选提交进行核实。严禁在当前轮次追加未授权的新需求。
> 架构原则：还原实际架构与跨层根因，禁止将美学偏好伪装成阻断项。
> 整体验收原则：核对受影响的完整用户交互链路；由主控统一收口，禁止各专业角色分别单方面宣布“生产就绪”。
> 熔断原则：同一任务交接轮次 `handoff_round` 上限为 2 轮；若两轮退回仍无法解决，立刻挂起并上报人类决策者，禁止无限互搏。

| 审查项 | 审计结论 |
| :--- | :--- |
| **适用候选版本** | 核验的目标 Git Commit SHA |
| **综合裁定** | `[满足本次验收关闭]` / `[存在明确阻断退回]` / `[外部证据不足待补充]` |

### 阻断项清单 (仅当退回时填写，必须包含标准 5 要素)
对每个阻断项必须给出以下五要素，禁止含糊其词：
1. **违反条款**：对应验收条款 / 硬约束（如 `AC-1` 或 `Hard-Gate-2`）
2. **代码位置**：涉及文件与行号（如 `core/collectors/xxx.py:120`）
3. **触发条件**：具体复现触发条件
4. **实际影响**：实际产生的影响与危害证据
5. **复现依据**：最小复现命令与真实报错日志

