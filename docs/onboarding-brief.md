# AI Team Studio — 项目交接说明（给接手的 Claude）

> 本文档是 AI Team Studio 的完整交接说明，面向**没有任何前置上下文**的接手者（人或 Claude）。
> 所有结论均来自对当前代码的只读核验；少数无法核验项已明确标注。
>
> **三个数据可信度提醒**：
> 1. 各 phase **没有可信日期**——git 历史被重写过，提交日期全坍缩到一天。
> 2. 测试基线 **20/20 来自状态文档，未在本次重跑**，引用前请先 `python scripts/run-all-regression.py` 重新确立。
> 3. 表数量约 17 张逻辑表（schema V16）；迁移期有表重建导致计数视角差异。

---

## 0. 基本坐标

- 项目根：`C:\Users\1\AI_Team_Studio`（Windows，本地优先）
- 当前分支：`phase-6e-b-approved-snapshot`，工作树 clean，已与 origin 同步
- 启动：双击桌面 `AI Team Studio.lnk`，或根目录 `start-desktop.bat`（Tauri）/ `start.bat`（Web）

---

## 1. 项目是什么

**一句话**：一个本地优先的桌面端**多角色 AI Agent 工作站**——把单个 coding assistant 升级成"Planner→Builder→QA→Reviewer 四角色协作 + 人工审批 + 可回滚执行"的工程化工作台，用来安全地驱动 AI 改本地代码项目。

**要解决的真实痛点**（`CLAUDE.md` / `README.md`）：
1. 单代理"自写自审"，缺交叉校验；
2. 主流工具云优先，源码/密钥/git 历史被迫离开本机；
3. 高危动作（删文件 / git reset / install / 任意 shell）零审批。

**它本质上不是聊天产品**。核心对象是五个工程实体：`Project / Task / AgentRun / ApprovalRequest / LogEvent`。一切围绕"任务推进 + 审批 + 审计"。

---

## 2. 第一原则与设计哲学（宪法，不可违反）

来自 `CLAUDE.md`，每个开发阶段开始前必须重读：

1. **本地优先**：本机运行、本地目录/命令/git/日志，不优先云端。
2. **项目对象优先，而非聊天优先**。
3. **小步迭代**：先最小可运行骨架，每次只推进一个清晰阶段，不擅自扩大范围。
4. **高风险动作必须审批**：删文件 / 批量改关键目录 / 危险 shell / git reset / force push / 改敏感配置，默认不自动执行。
5. **角色边界清晰**：Planner / Builder / QA / Reviewer，**Builder 不能给自己做最终验收**。

开发节奏铁律：policy / eligibility / audit / tests 先行；每阶段收口必须输出"完成了什么 / 还缺什么 / 当前风险 / 下一步"。

---

## 3. 系统架构（分层）

```
Tauri 桌面壳 (apps/desktop)
  └─ React 19 + TypeScript + Vite（webview 内）
        │  HTTP fetch → http://127.0.0.1:9800/api/*
        ▼
FastAPI 本地服务 (services/runtime, main.py, 端口 9800, CORS 全开仅本地)
  ├─ routers/        14 个路由文件，~79 个 REST endpoint
  ├─ agents/         orchestrator + 各执行链 service + executor
  ├─ tools/          file/git/shell + safety + approval_gate + audit
  ├─ providers/      6 家 LLM 统一抽象 + registry
  ├─ models.py       Pydantic 模型 + 枚举 + 状态机
  └─ database.py     SQLite 迁移（schema V16）
        ▼
SQLite (data/)  约 17 张逻辑表
```

进程模型：前后端是两个独立进程，用 HTTP 通信（不是 Tauri IPC），跨语言简单。

---

## 4. 四角色 Agent 系统（核心）

定义在 `agents/definitions.py`（`AGENT_PIPELINE`），输出 schema 在 `agents/model_executor.py`，执行在 `agents/orchestrator.py`。

| 角色 | 颜色 | 职责 | 工具 | 必须输出（结构化 JSON） | 当前 executor |
|---|---|---|---|---|---|
| **Planner** | 青 `#22D3EE` | 拆任务、定验收标准、列风险 | read/grep/glob | `goal_summary` / `task_breakdown[]` / `acceptance_criteria[]` / `risks[]` / `dependencies[]` | **真实模型**（启用时），provider/model 取自 DB |
| **Builder** | 玫红 `#FB7185` | 产出执行提案（**只规划不执行**） | read/edit/write/bash/grep/glob（plan-only 不真用） | `change_summary` / `proposed_files[]{path,action,reason,content}` / `change_steps[]` / `reasoning_summary` / `validation_plan[]` / `risk_notes[]` + 可选 `proposed_commands[]` / `risk_level` / `requires_approval` 等 | **真实模型**，plan-only（Phase 6D 起） |
| **QA** | 紫 `#A78BFA` | 验证、测试、归因 | read/bash/grep/glob | `validation_scope` / `test_actions[]` / `result` / `findings[]` | **永远 mock**（`ModelAgentExecutor` 对 QA 抛 `NotImplementedError`，无系统提示词） |
| **Reviewer** | 黄 `#FBBF24` | 终审，给 `approve`/`request_changes` | read/grep/glob | `decision`(approve\|request_changes) / `reason` / `issues_found[]{severity,description}` / `confidence`(high\|medium\|low) | **真实模型**（启用时） |

**关键事实**：
- Builder 系统提示词硬性写明：**绝不真正执行文件/命令/git，绝不谎称已完成、绝不编造输出**。
- Reviewer 的 `decision` 当前只有 `approve` / `request_changes` 生效；`BLOCK` 枚举存在但保留未用。
- ⚠️ **Reviewer 的 "approve" 不是人工审批**——它是模型输出，由 orchestrator 自动消费。真正的人门在执行链里（见第 7 节）。

---

## 5. 编排流程与拒绝循环

`Orchestrator.run(task_id)`（`orchestrator.py`）：

1. **加载并原子认领**：任务必须 `PENDING`，CAS 更新到 `PLANNING` 防并发重入。
2. **组装上下文**：task 元数据 + `previous_outputs`；从 `project_role_participants` 读取本项目启用了哪些角色（无配置默认 4 个全开）。
3. **流水线循环**：按启用角色顺序执行；每步建 `AgentRun`(pending→running→completed/failed)；失败则任务立即 `FAILED` 中止；成功则把输出塞给下一个角色并推进任务状态。
4. Builder 成功后，若 `requires_approval`，创建 `execution_proposal`（Phase 6E-A）。
5. 每步记录 token 用量到 `token_usage_log`（Phase 12）。

**任务状态机**（`models.py`，`TASK_TRANSITIONS` 强校验）：
```
PENDING → PLANNING → IN_PROGRESS → REVIEWING → DONE
  每个非终态都可 → FAILED；REVIEWING 可回退 IN_PROGRESS；FAILED 可重试回 PENDING；DONE 终态
```

**拒绝循环**：Reviewer 给 `request_changes` → 计数+1 → 回到 Builder 重做；**最多 3 次**，第 4 次任务 `FAILED("Exceeded maximum rejections")`。历史拒绝压缩成一行摘要省 token（只保留最后一条全文）。

---

## 6. 八步执行链（项目的灵魂）

Builder 产出提案后**不会直接执行**。要落到真实文件，必须走完这条链，每环都有数据库记录和保证：

```
execution_proposal      Builder 输出，存 proposal_data + risk_level + requires_approval
   └─ approval_request   人工审批门（高风险）；状态级联回提案
        └─ execution_snapshot   审批后冻结的不可变副本，含 SHA-256 content_hash（防篡改）
             └─ execution_request   执行意图；status: requested→confirmed/rejected（终态、不可逆、人工触发）
                  └─（eligibility check 只读门：12 项校验）
                       └─ execution_result   mode = dry_run | real_run | rollback
                            └─ execution_file_backups   real_run 时为 file_modify 存原文件（供回滚）
```

**每环硬保证**：
- **幂等**：snapshot / request / dry_run 先查存在再插；重复调用返回已有结果。DB 用 `UNIQUE(snapshot_id)`、`UNIQUE(execution_request_id, mode)` 等约束兜底。
- **SHA-256 完整性**：冻结时算 `content_hash = SHA256(canonical_json(proposal_data))`；eligibility、dry-run 都会重算校验。
- **eligibility 12 项校验**（`execution_eligibility_service.py`）：请求已 confirmed、有 workspace、hash 一致、action plan 能编译、无 denied/needs_confirmation 动作、动作类型 ∈ `{file_create, file_modify, command_run}`、路径在 workspace 内、命令过白名单与 shell 语法、**dry_run 必须已完成**。任何一项不过 → 整个请求被拦（全或无）。
- **回滚**：`file_modify` → 从 backup 还原；`file_create` → 删除。逐文件状态（restored/deleted/already_absent/rollback_failed/skipped），best-effort。**命令副作用不可回滚**（只覆盖文件层）。

---

## 7. 安全模型（接手后无条件遵守）

### 7.1 默认硬封的能力
`file_delete`、git 写（commit/push/reset/checkout/merge/rebase/stash）、install/publish/network（npm install、pip install、cargo install…）、`shell=True` 与 shell 元字符（`&& || ; > >> < |` 反引号 `$()`）、白名单外命令、写敏感 dotfile（.env/.ssh/.gitconfig…）、写系统目录、路径穿越（`../`）。

### 7.2 命令执行三层防御
白名单：`node npm npx yarn pnpm python python3 pip pip3 cargo rustc go make cmake tsc eslint prettier jest pytest vitest mocha git(只读)`。
- Layer 1 资格门：白名单 + 禁元字符 + 禁子shell + workdir 边界。
- Layer 2 执行器复检（TOCTOU 防御）：再查一遍 + 禁子命令清单（`npm install`、`git push` 等）。
- Layer 3 运行环境：`shell=False` + 环境变量白名单 + `stdin=DEVNULL` + 60s 超时 + 64KB 输出帽 + fail-fast。

### 7.3 四个不可绕过的人工门
1. **Approval Gate**（高/危风险提案）
2. **Confirm Gate**（`execution_request.confirmed`，不可逆终态）
3. **Execute 触发**（不可由审批自动串联）
4. **Rollback 触发**（不自动）

### 7.4 风险分级
`tools/safety.py` 的 `RiskClassifier`：HIGH/CRITICAL 触发审批门。CRITICAL 规则含 `rm -r`、`git reset`、force push、`sudo`、`mkfs`、`dd`、`curl|sh` 等。

> ⚠️ **软肋**：低风险提案会被**自动批准并生成合成 approval 记录**，直接冻结成可执行快照（`snapshot_service.freeze_snapshot()`）。即"人保留扳机"在低风险带有缺口。**不要拓宽这个自动批准带**；charter 禁止降级安全门。

---

## 8. 数据模型（约 17 张逻辑表，schema V16）

`database.py`，从 V1 线性迁移到 V16。核心表：

| 表 | 用途 |
|---|---|
| `schema_version` | 迁移版本 |
| `projects` | 项目（含 `local_repo_path` workspace 根） |
| `tasks` | 任务（状态机核心） |
| `agent_runs` | 每次角色执行（role/model/输入输出/状态） |
| `approval_requests` | 审批门（含 `proposal_id` FK） |
| `log_events` | 审计日志（JSON payload） |
| `provider_settings` | LLM provider 配置（API key 仅本地） |
| `role_model_settings` | **按角色路由模型的运行时真值**（role→provider/model/enabled） |
| `execution_proposals` | Builder 提案 |
| `execution_snapshots` | 冻结快照（SHA-256） |
| `execution_requests` | 执行请求（UNIQUE snapshot_id） |
| `execution_results` | 执行结果（UNIQUE request_id+mode） |
| `execution_file_backups` | 回滚备份 |
| `token_usage_log` | token 统计 |
| `skills` | 技能（全局/角色级，Phase 14） |
| `roles` | 角色注册表（系统+自定义，Phase 15） |
| `project_role_participants` | 每项目启用哪些角色（Phase 15） |

---

## 9. API 全貌（~79 个 REST endpoint，base `http://127.0.0.1:9800/api`）

按域分组（详见 `routers/`）：
- **projects**(8)：CRUD + participants + Godot 检测
- **tasks**(5) / **agent_runs**(4)
- **approvals**(4)：含 `GET /approvals/pending`
- **orchestration**(19，最核心)：`POST /tasks/{id}/orchestrate`、`orchestration-status`、proposals、`POST /proposals/{id}/freeze`、snapshots、`request-execution`、execution-requests（PATCH 确认/拒绝）、`dry-run`、`real-run`、`action-plan`、`POST .../execute`、`POST /execution-results/{id}/rollback`、`audit-trail`、`token-usage`
- **providers**(3) / **settings**(5：providers + role-models + readiness)
- **tools**(3：list/execute/execute-approved)
- **logs**(3) / **dashboard**(3：summary/search/attention)
- **skills**(5) / **roles**(5) / **completion**(1) / **health**(1)

---

## 10. 前端与真实用户动线

技术：Tauri + React 19 + TS + Vite，i18next 双语（en / zh-CN，本地打包，存 localStorage）。深色控制台风格，高信息密度。

**面板**（`apps/desktop/src/panels/`）：Dashboard（总览/健康/readiness）、Projects（CRUD + 角色参与配置）、TaskBoard（核心工作台）、Skills、Roles、Approvals（审批队列，sidebar 角标实时）、Logs、Settings（语言 + provider key + 按角色选模型）。还有 sidebar 的 **WorkspaceQuickComposer**（一句话建任务自动编排）和 **CommandPalette**（Ctrl+K 搜索导航）。

**TaskBoard 子组件**：`TeamConversation`（群聊式 agent 消息流，主视觉）、`RoleStatusCards`（四角色工位卡）、`ProposalCard` + `ExecutionPipelineViewer`（提案→快照→请求→dry-run→执行→回滚全链可视化）、`AuditTrailSection`（不可变时间线）、`ConfirmModal`（执行/回滚危险确认）。

**典型用户动线**：
1. sidebar 一句话输入"实现登录页" → 自动建任务、自动 orchestrate → 每 1.5s 轮询，阶段标签 Planning→Building→Reviewing→Done。
2. TeamConversation 实时显示四角色产出；RoleStatusCards 显示状态/耗时/模型。
3. 提案卡出现 → 内联点 **Approve** → 自动串联 freeze→建 request→confirm→dry-run。
4. dry-run 显示"将创建 3 文件" → 点 **Execute** → 危险确认弹窗 → 真实执行 → 绿色文件状态。
5. 出错可点 **Rollback**（红色危险确认）→ 逐文件还原。审计时间线全程留痕。

---

## 11. 技术栈

- 后端：FastAPI 0.115 / uvicorn / pydantic 2.9 / aiosqlite / `httpx[socks]` / anthropic / openai / google-genai SDK。
- 前端：React 19 / TypeScript / Vite / Tauri v2 / react-i18next（零额外状态库、零 router，原生 fetch + useState）。
- DB：SQLite。测试：38 个 acceptance 文件，20 套进统一回归（`scripts/run-all-regression.py`）。

---

## 12. Provider 与模型路由（含一个真实代码冲突）

6 家 provider 已抽象接入：Anthropic / OpenAI / DeepSeek / Kimi / MiniMax / Gemini（OpenAI-compatible 复用一个类接 DeepSeek/Kimi/MiniMax）。

**运行时真值是 DB 表 `role_model_settings`**，不是代码常量。`model_executor._resolve_provider()` 只读 DB。

⚠️ **代码冲突（中等，已核验）**：`definitions.py` 把三个角色都硬编码成 `claude-3-5-haiku`，但 `database.py` V5 种子是 Planner=`claude-sonnet-4`、Reviewer=`claude-3-5-haiku`。运行时以 DB 为准、**当前行为正确**，但 `definitions.py` 那几个 model 字段是**误导性死配置**。
→ 若要清理：**不要把 definitions.py 改成"和 DB 一致"**（可能影响未来 fresh-DB 行为），而应删除/标注废弃那些字段，并加测试断言只读 DB。

实际配置：据状态文档仅 **Gemini** 配了 key（无法读 secret 核实其余）；Planner/Builder/Reviewer 曾映射到 Gemini，QA 恒 mock。

---

## 13. 当前状态与运行模式

- 分支 `phase-6e-b-approved-snapshot`，clean，已 push。
- schema **V24**；核心执行管线完成到 **Phase 8B**（命令执行 + 回滚）。
- **运行模式 = Daily Usage Mode**：不主动开发新功能，只在真实使用出现 blocker 时修。
- 曾在真实项目 us-quant-research 端到端跑通（6 文件创建 + 3 命令 + 1 回滚，状态文档记录，本会话未重跑）。
- **测试基线**：`python scripts/run-all-regression.py` = **57 套 / 57 通过**（本会话重跑确立）。
- **C2 step 1（mock comparator 已构建 + 已接线，方向 LOCKED = 内部 collapse N→1）**：read-only mock Comparator 角色，在 N 个 builder-shaped 候选中选 1。
  - **构建（`bdb6a92`）**：`AgentRole.COMPARATOR` + `COMPARATOR_DEFINITION`（no-op status IN_PROGRESS、allowed_tools=[]）+ `MockAgentExecutor._comparator`（规则：优先 `requires_approval==false`，并列取首个；veto-safe 兜底：异常/畸形/N=0→最低 risk_level，缺失→critical 排末，全不可读→首个；**永远 success=True**）+ `tests/comparator_test.py`。
  - **接线（人工审查后，本次）**：① comparator 插入 AGENT_PIPELINE **builder 与 qa 之间**（post-definition `.insert()`，因 list literal 先于常量求值）；② **filter-exemption**——编排器 active_pipeline 过滤把 comparator **豁免**（始终在管线，是强制 collapse 基建，非可禁用 participant；故 `_get_enabled_roles` / participant 系统 / phase15_3 T6 **字节未动**）；③ **Option-A collapse handoff**——generic handoff 后加一个 role==COMPARATOR 的**附加、容错**特例：把 comparator 的 `chosen`（builder-shaped）写进 `previous_outputs["builder"]`，使 qa/sr/reviewer/documentation **字节未改**地读到选中提案；`chosen` 缺失/畸形→不覆写（保留 builder 原值）；comparator 自身完整输出留在 `previous_outputs["comparator"]` 作审计；④ mock 候选来源：优先 `ctx["candidate_proposals"]`，否则回退 `[previous_outputs["builder"]]`（step-1 builder 产 1 → N=1 → no-op passthrough）。
  - **step 1 = no-op passthrough**：builder 仍产 1 → comparator 选中那 1 → 1:1 安全链不变（**提案创建未动**：仍在 builder step，gate 仍 `(BUILDER, DOCUMENTATION)`，comparator **不在 gate**，建 0 个提案）。**控制流（idx/rejection/MAX_REJECTIONS/审批门/blocking-veto）未动**。
  - 测试：`tests/comparator_wiring_test.py`（17 检查 a–g：接线/collapse/审计槽/容错/下游不变/提案1:1/filter-exemption）。phase3 + phase6b_round2 的管线 shape 断言 7→8 更新（含 comparator，DOC-3 同款先例；phase3 的两处 positional steps[3]/steps[5] 改为按角色选取）。**4 个下游 reader 测试字节未改**。Unit 28→29，回归 56→57 全绿。
  - **下一步 = step 2（待人工）**：让 builder 产 N + 把提案创建从 builder 转到 comparator（保 1:1）。
- **C-series 预备工作（已完成，不触碰编排控制流）**：
  - (1) 删除死键 `rejection_feedback`（mock 的 `is_retry` 重新指向 live 的 `rejection_history`）——C1 会踩的潜在陷阱已清除。
  - (2) `agent_runs.attempt_number` 列（V24，V23 式纯增量/可空/幂等迁移；由编排器 `rejection_count` 写入：首轮=0，被退回重跑的 builder→qa→sr→reviewer 尾段=1/2）——C1/C3 现可直接区分重跑轮次，不再只靠 `created_at`。
  - `proposal_group_id`（C2 的提案分组）**推迟到 C2**（不同表、形态未定）。控制流（idx 循环 / 退回重写 / MAX_REJECTIONS / 审批门 / blocking-veto 语义）**完全未动**。
  - 这只是预备工作；**C2/C1/C3 的控制流实现仍待定（建议先定 C2 方向）**。
- ⚠️ git 历史被重写过，提交日期不可信，无法据 git 推断真实开发周期。

---

## 14. 待决策 backlog 与禁区

**已设计未实现 / deferred**：
- **Phase 5.9 受控桌面操作**（Godot 自动化）——仅"设计冻结"，被 charter 明令禁止桌面接管，**需先正式修订 CLAUDE.md 才能动**。文档有 5 个开放问题（GUI 库选型、状态校验法、DesktopSession 数据模型、Godot 版本兼容、CLAUDE.md 修订草案）。
- Builder 监督式真实执行（超出 plan-only）；`packages/shared/` 共享类型；Phase 14 Skills 面板深化。

**被设计封死（需专门 phase + 用户点头才解封）**：`file_delete`（缺删除回滚策略）、git 写（缺完整 git 回滚策略）、install/network、`shell=True`（永久封）、QA 真实模型（角色设计约束）。

> 重要心态：**backlog 的存在 ≠ 动手授权**。这些大多挂在一个用户尚未批准的 CLAUDE.md 修订上。当前是 Daily Usage Mode，默认冻结，除非用户明确发起。

---

## 15. 给接手者的工作守则 + 关键陷阱

**守则**：
1. 每个 phase 前重读 `CLAUDE.md` §运行时安全边界。
2. 一切落在 Project/Task/AgentRun/ApprovalRequest/LogEvent 五对象上。
3. policy/eligibility/audit/tests 先行。
4. 高风险动作只给推荐，**扳机（审批/Confirm/Execute/Rollback）留给人**，绝不自动串联。
5. 小步、单 phase、收口报"完成/缺失/风险/下一步"。

**5 个最容易踩的陷阱**：
1. 别把 **Reviewer 的 approve** 当成人工审批——它是模型输出。
2. 注意**低风险自动合成审批**那条缝（§7.4 末），别拓宽。
3. 清理 `definitions.py` 死配置时别"改成和 DB 一致"。
4. 别因为有 Phase 5.9 详细设计文档就去实现——它被 charter 冻结。
5. 命令副作用不可回滚，UI 必须如实提示，别假装能撤销。

---

## 16. 关键文件地图

| 想看什么 | 去哪 |
|---|---|
| 宪法/安全边界 | `CLAUDE.md` |
| 角色定义 + 系统提示词 | `services/runtime/agents/definitions.py` |
| 真实模型调用 + 输出 schema | `services/runtime/agents/model_executor.py` |
| 编排引擎 + 拒绝循环 | `services/runtime/agents/orchestrator.py` |
| 执行链各 service | `agents/{snapshot,execution_request,execution_result,rollback,execution_eligibility,action_policy}_service.py` |
| 安全分级 / 审批门 | `tools/safety.py`、`tools/approval_gate.py` |
| 命令沙箱 | `agents/scoped_command_executor.py`、`scoped_file_executor.py` |
| DB schema 迁移 | `services/runtime/database.py` |
| API | `services/runtime/routers/*.py`、`main.py` |
| 前端工作台 | `apps/desktop/src/panels/TaskBoard.tsx` + `panels/taskboard/*` |
| 文档 | `docs/{architecture,agents,execution-pipeline,providers,data-model,self-use-runbook}.md` |
| 启动 | 双击桌面 `AI Team Studio.lnk`，或根目录 `start-desktop.bat` / `start.bat` |
