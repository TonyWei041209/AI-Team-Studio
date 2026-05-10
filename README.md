# AI Team Studio

> 本地优先的多角色 AI 工作站 —— 把 coding assistant 升级为一支可审批、可审计、可回滚的"虚拟工程团队"。

AI Team Studio 不是聊天产品，也不是单一 coding assistant，而是一个**桌面端、本地运行、多模型、多角色、多工具、强审批**的 agent 工作站，用来协同推进真实软件项目。

---

## 一、项目解决的核心痛点

主流单代理（single-agent）编码助手在个人长期项目里有三个硬伤：

1. **无角色边界、自审自证**：同一个模型既写代码又给自己打分，缺乏交叉校验。
2. **云优先、不本地优先**：源码、密钥、git 历史被迫离开本机；多项目切换困难。
3. **高危动作零审批**：删除文件、`git reset`、`npm install`、任意 shell 命令，一句"yes"就可能毁掉本地仓库。

AI Team Studio 的目标就是把这三件事一次性解决：**本地运行、多角色分工、关键步骤强制人工审批**。

---

## 二、核心逻辑流：多 Agent 长链协作

### 2.1 四角色固定分工，Builder 不能自审

| 角色 | 颜色 | 职责 | 工具权限 | 输出 |
|---|---|---|---|---|
| **Planner** | 蓝 | 拆任务、定验收标准 | read / grep / glob | goal_summary、task_breakdown、acceptance_criteria、risks |
| **Builder** | 绿 | 生成执行计划与文件改动提案 | 计划层（不直接执行） | proposed_files、change_steps、proposed_commands、risk_level |
| **QA** | 黄 | 测试设计与失败归因 | read / bash / grep | validation_scope、test_actions、PASS/FAIL、findings |
| **Reviewer** | 紫 | diff 审查、风险评估、放行/打回 | read / grep / glob（只读） | decision: APPROVE / REQUEST_CHANGES / BLOCK |

> Reviewer 可拒绝最多 3 次，每次回到 Builder 重写，超过则任务标记 `failed`。

### 2.2 完整 8 步执行链（每个 task 都走一遍）

```
task
 └─ execution_proposal       Builder 产出（含 proposed_files + commands + risk_level）
      └─ approval_request    人工审批（HIGH/CRITICAL 风险必须审批）
           └─ execution_snapshot   SHA-256 冻结快照（不可篡改）
                └─ execution_request   Confirmed = 不可逆终态
                     ├─ dry_run        模拟执行，不写盘
                     ├─ real_run       真实执行 + 自动备份
                     └─ rollback       基于备份恢复（仅文件层）
```

每一步全部落库到 SQLite，并产出 audit trail；前端 **Team Conversation** 视图把四个 agent 的 reasoning、产出、token 用量、耗时实时投影成"群聊"。

### 2.3 模型路由（按角色独立配置）

- 统一 `BaseProvider` 抽象，已接入：**Anthropic / OpenAI / DeepSeek / Kimi / MiniMax / Gemini**。
- DB 表 `role_model_settings` 是**运行时唯一真值**，可对 Planner / Builder / Reviewer 分别绑定不同模型。
- 例：Planner 用 Claude Opus 做长链推理，Reviewer 用 Sonnet 做快速审查，Builder 用 Gemini Flash 做大量代码生成 —— 平衡能力与成本。
- API key 仅落本地 SQLite，HTTP 响应自动遮罩为 `sk-****1234`，日志永不打印明文。

---

## 三、安全门控（Runtime Safety Boundaries）

以下规则在所有 phase 都不可降级、不可绕过：

### 3.1 默认 blocked 的高危能力

| 类别 | 默认状态 | 说明 |
|---|---|---|
| `file_delete` | 🚫 blocked | 不允许 agent 自动删文件 |
| `git_commit` / `git_checkout` / `git push` / `reset` / `merge` / `rebase` | 🚫 blocked | git 写操作全部禁用 |
| `npm install` / `pip install` / `cargo install` | 🚫 blocked | 安装、网络类命令禁用 |
| `shell=True` / shell 拼接 | 🚫 blocked | 永远走 `subprocess.run(args, shell=False)` |
| 任意未在白名单内的命令 | 🚫 blocked | 仅允许 build / test / lint / inspect |

**命令白名单**：`node / npm / npx / yarn / pnpm / python / python3 / pip / cargo / rustc / go / make / cmake / tsc / eslint / prettier / jest / pytest / vitest / mocha / git`（git 仅只读子命令）。

### 3.2 三层防御

```
Layer 1  Eligibility Gate     白名单 + 禁字符 (&&, ||, ;, >, |, $())
Layer 2  Executor Re-Validate  TOCTOU 防御 + 子命令禁用清单
Layer 3  Execution Env        shell=False / 环境变量白名单 / stdin=DEVNULL / 60s timeout / 64KB 输出帽
```

### 3.3 不可绕过的人工触发点

- **Approval Gate**：高风险 proposal 必须人工 Approve。
- **Confirm Gate**：execution_request 的 `confirmed` 是不可逆终态，必须人工触发。
- **Dry-Run-Before-Execute**：真实执行前必须先有 dry_run 模拟。
- **Execute / Rollback**：均由人工触发，不自动串联。

### 3.4 Snapshot + Rollback

- 审批通过后立刻冻结 `execution_snapshot`（SHA-256 哈希校验）。
- `real_run` 自动备份原始内容到 `execution_file_backups`。
- `rollback` 按 backup 逐文件还原，best-effort：单文件失败不影响其他文件。
- 命令副作用**不可回滚**（rollback 仅覆盖文件层），UI 会明确提示。

---

## 四、功能矩阵

### 4.1 后端（FastAPI）

| 模块 | 关键能力 |
|---|---|
| `agents/orchestrator.py` | 四角色 pipeline 驱动，per-role executor dispatch |
| `agents/model_executor.py` | 真实模型调用，结构化 JSON 校验，code-fence 剥离 |
| `agents/snapshot_service.py` | SHA-256 内容冻结 |
| `agents/execution_eligibility_service.py` | 全或无的 eligibility gate |
| `agents/scoped_file_executor.py` | workspace 内安全写文件，atomic write，自动建父目录 |
| `agents/scoped_command_executor.py` | 命令白名单 + 子命令禁用 + 环境变量隔离 |
| `agents/rollback_service.py` | 文件级回滚，per-file 状态 |
| `tools/proposal_validator.py` | 提案级风险预分析 |
| `tools/safety.py` + `tools/approval_gate.py` | 风险分级 + 审批门控 |
| `providers/registry.py` | 6 家 provider 统一注册 |
| `routers/*.py` | 完整 REST API（projects / tasks / orchestration / approvals / execution / providers / settings / logs / dashboard） |

### 4.2 前端（React + TypeScript on Tauri v2）

| 面板 | 功能 |
|---|---|
| **Dashboard** | 项目卡片 + 任务态势 + 审批队列 |
| **Project** | 创建 / 选择项目，绑定本地仓库路径 |
| **Task Board** | 自然语言快速建任务、单一工作台、Team Conversation、Team View、Token 摘要 |
| **Roles** | 四角色 system prompt、模型绑定、enabled 切换 |
| **Approvals** | pending 队列、风险摘要、内联 Approve / Reject |
| **Skills** | 技能库（Phase 14） |
| **Logs** | 终端风格日志，按 level 过滤 |
| **Settings** | Provider / API key 管理 + 连通性测试 |

### 4.3 任务工作台细分组件

`apps/desktop/src/panels/taskboard/` 内已有：
- `RoleStatusCards`：四角色"工位卡"，显示当前动作、下一步、最近产出、token 用量、耗时
- `TeamConversation`：群聊风格消息流（基于 runs + audit-trail 真实数据投影）
- `ProposalCard` + `ExecutionChainStepper`：提案折叠摘要 + 风险标签 + 内联审批
- `ExecutionPipelineViewer`：dry_run / real_run / rollback 三段 result 可视化（含 stdout / stderr / exit_code / 耗时）
- `AuditTrailSection`：完整时间线
- `TaskTemplates` + `QuickTaskInput`：模板化建任务
- `OrchestrationErrorGuide` + `PipelineErrorHint` + `NextStepHint`：错误引导

### 4.4 Quick Composer

左侧 WORKSPACE 顶部有紧凑输入条：选项目 → 一句话描述 → 自动 orchestrate → 切到 Tasks。

---

## 五、技术栈

| 层 | 选型 |
|---|---|
| Desktop Shell | **Tauri v2** |
| Frontend | **React 19 + TypeScript + Vite** |
| Runtime | **Python 3.11 + FastAPI** |
| DB | **SQLite**（schema 已演进至 V6+） |
| LLM Providers | Anthropic / OpenAI / DeepSeek / Kimi / MiniMax / Gemini |
| 通信 | localhost HTTP（127.0.0.1:9800），CORS 限本地 |
| 测试 | 20 套回归 / ~720 checks，全部 isolated runner |

---

## 六、快速开始

### 6.1 前置依赖

- Node.js 18+
- Python 3.11+
- Rust（仅桌面 App 模式需要，via rustup）

### 6.2 启动

#### 方式 A：双击桌面快捷方式（已生成）

- **AI Team Studio.lnk** → Tauri 桌面 App 模式
- **AI Team Studio (Web).lnk** → Web 模式（浏览器打开，无需 Rust）

#### 方式 B：项目根 `.bat`

```bat
start-desktop.bat        :: Tauri 桌面 App
start.bat                :: Web 模式（http://localhost:5173）
```

#### 方式 C：手动两端启动

```bash
# Terminal 1 — Runtime
cd services/runtime
pip install -r requirements.txt
python main.py

# Terminal 2 — Desktop
cd apps/desktop
npm install
npm run tauri:dev
```

### 6.3 健康检查

- Runtime: <http://127.0.0.1:9800/api/health>
- Frontend (Web 模式): <http://localhost:5173>

---

## 七、目录结构

```
AI_Team_Studio/
├─ apps/desktop/              Tauri + React + TypeScript
│  └─ src/
│     ├─ api/                 typed fetch + 各 domain API
│     ├─ components/          通用组件（CommandPalette, WorkspaceQuickComposer, ...）
│     ├─ hooks/               useProjects / useTasks / useApprovals / useLogs
│     ├─ panels/              7 大面板 + taskboard 子组件
│     ├─ i18n/                中英文文案
│     └─ types/               与后端 Pydantic 对齐的 TS 接口
├─ services/runtime/          FastAPI 本地服务
│  ├─ agents/                 orchestrator / model_executor / snapshot / eligibility / executors / rollback
│  ├─ providers/              anthropic / openai_compatible / gemini / registry
│  ├─ tools/                  file / git / shell / safety / approval_gate / audit
│  └─ routers/                projects / tasks / orchestration / approvals / providers / settings / logs / dashboard / completion / skills / roles / agent_runs
├─ data/                      SQLite DB
├─ docs/                      architecture / agents / data-model / execution-pipeline / providers / tools / runbook
├─ scripts/                   dev.bat / verify-all.bat / run-*-regression.py
├─ tests/                     20 套 acceptance 回归
├─ start.bat                  Web 模式启动
├─ start-desktop.bat          Tauri 模式启动
└─ CLAUDE.md                  Runtime Safety Boundaries（不可降级）
```

---

## 八、开发现状

- **当前分支**：`phase-6e-b-approved-snapshot`
- **回归基线**：20 suites / 20 pass / 0 fail
- **Phase 进度**：已完成 0~8B（runtime 骨架 → 数据模型 → orchestrator → 工具/审批 → 前端 → provider 接入 → 执行管线 → 命令执行 + 回滚）
- **真实落地验证**：us-quant-research 项目跑通端到端 —— 6 个文件创建 / 3 条命令执行 / 1 次回滚，全部按预期。
- **运行模式**：Daily Usage Mode（不主动开发新功能，仅在真实使用中出现 blocker 时介入修复）。

---

## 九、不做的事（明确 out-of-scope）

- ❌ 云同步 / 账号系统 / 团队协作
- ❌ 自动部署到生产环境
- ❌ 完整电脑接管 / 复杂浏览器自动化
- ❌ 无确认的高危自动执行
- ❌ 自动扩展 command / file / git / network 能力

> 本项目为个人自用桌面工作站，所有设计决策都围绕"本地优先 + 小步推进 + 安全门控不可降级"。

---

## 十、文档索引

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 分层架构、模块设计决策 |
| [docs/agents.md](docs/agents.md) | 四角色定义、orchestrator、executor 协议 |
| [docs/execution-pipeline.md](docs/execution-pipeline.md) | 8 步执行链、三层防御、回滚语义 |
| [docs/providers.md](docs/providers.md) | provider 抽象层与 6 家集成 |
| [docs/data-model.md](docs/data-model.md) | SQLite schema 演进 |
| [docs/tools.md](docs/tools.md) | 工具层 + 安全分级 |
| [docs/self-use-runbook.md](docs/self-use-runbook.md) | 日常使用 runbook |
| [CLAUDE.md](CLAUDE.md) | 项目第一原则 + Runtime Safety Boundaries |

---

## License

个人自用项目，未发布开源 license。
