# 项目总说明

本项目是一个**个人自用的桌面端 AI 工作站**。

它不是普通聊天应用，也不是单一 coding assistant，而是一个**本地优先、多模型、多角色、多工具、可审批**的 agent 工作站，用来协同推进我的软件项目。

## 第一原则

1. **本地优先**
   - 主要在本机运行。
   - 默认围绕本地项目目录、本地命令、本地 Git、本地日志工作。
   - 不要优先设计云端部署和多人协作。

2. **项目对象优先，而不是聊天优先**
   核心对象应该是：
   - Project
   - Task
   - AgentRun
   - ApprovalRequest
   - LogEvent

3. **小步迭代**
   - 先做最小可运行骨架。
   - 每次只推进一个清晰阶段。
   - 不要擅自扩大范围。

4. **高风险动作必须审批**
   以下操作默认不自动执行：
   - 删除文件
   - 批量改动关键目录
   - 危险 shell 命令
   - git reset / force push
   - 修改敏感配置文件
   - 未来的系统级自动化动作

5. **角色边界清晰**
   第一版至少有四个角色：
   - Planner
   - Builder
   - QA
   - Reviewer

   Builder 不能给自己做最终验收。

## 第一版范围

### 必做
- Tauri 桌面壳
- React + TypeScript 前端
- Python + FastAPI 本地 runtime
- SQLite 本地数据库
- 任务系统
- Agent 编排
- 文件读写
- Shell 执行
- Git 基础集成
- 日志系统
- 审批系统
- 模型 provider 抽象层
- 基础设置页

### 不做
- 云同步
- 账号系统
- 团队协作
- 自动部署生产环境
- 完整电脑接管
- 复杂浏览器自动化
- 无确认的高风险自动执行

## 技术栈偏好

- Desktop shell: **Tauri**
- Frontend: **React + TypeScript**
- Runtime: **Python + FastAPI**
- Database: **SQLite**
- Shared schemas/types: 单独抽离

## UI 原则

界面风格应偏：
- 深色
- 控制台 / 工作站
- 清晰分区
- 高信息密度但不拥挤

至少包含：
- Project 视图
- Task Board
- Agent Team 面板
- Logs 面板
- Approvals 面板
- Settings

## 模型层原则

必须有统一 provider 抽象，不要把不同模型的调用逻辑散落在业务代码里。

第一版至少预留这些 provider 的接入接口：
- Anthropic / Claude
- OpenAI
- Gemini
- DeepSeek
- Kimi
- MiniMax

如果来不及全部接完，先完成：
1. Claude
2. OpenAI-compatible provider
3. Gemini

## 工作方式

每个阶段开始前，先输出：
1. 当前阶段目标
2. 计划修改的文件
3. 设计理由
4. 验收标准

每个阶段完成后，输出：
1. 完成了什么
2. 还缺什么
3. 当前风险
4. 下一步建议

## 代码原则

- 优先可维护性，不炫技
- 模块边界清晰
- 先正确，再优雅
- 尽量写类型
- 尽量补基础测试
- 不做过度抽象
- 优先让系统可运行

## 文档要求

核心模块完成后，及时更新：
- README.md
- docs/architecture.md
- docs/agents.md
- docs/data-model.md

## 执行顺序

严格按这个顺序推进：
1. 本地运行骨架
2. 核心数据模型与任务系统
3. Agent 与 orchestrator
4. 工具层与审批机制
5. UI 打磨
6. 模型接入与路由
7. 可用性增强
8. 后续再评估桌面自动化能力

## 关键约束

- 不要把第一版做成纯聊天产品。
- 不要先做完整电脑接管。
- 不要跳过审批系统。
- 不要让 Builder 兼任最终 Reviewer。
- 不要在第一轮就实现所有高级特性。

## 运行时安全边界（Runtime Safety Boundaries）

以下规则在整个开发过程中始终有效。任何 phase 开始前必须重新读取本节并确认遵守。

### 命令执行
- `file_delete` 默认 **blocked**，不允许 agent 自动删除文件
- `git` 写操作（commit / push / reset / checkout -b / merge / rebase）默认 **blocked**
- `install` / `publish` / `network` 类命令默认 **blocked**
- `shell=True` / shell 拼接风格默认 **blocked**
- 命令白名单仅限本地 build / test / lint / inspect 工具
- 命令不可回滚（rollback 仅覆盖文件操作）

### 安全门控
- **Approval gate** 必须保留：高风险 proposal 必须经过人工审批
- **Confirm gate** 必须保留：execution request 的 confirmed 状态是不可逆终态，必须人工触发
- **Dry-run-before-execute** 原则必须保留：真实执行前必须有 dry-run 模拟
- **Execute 必须保持人工触发**，除非用户明确另行决定
- **Rollback 必须保持人工触发**，除非用户明确另行决定

### 能力边界
- 不自动扩展 command / file / git / network 能力
- 不新增高风险自动执行路径
- 不删除或降级现有安全门控
- 不自动串联 execute 或 rollback

### 开发节奏
- 本地优先 / 桌面优先 / 小步推进
- 一次只推进一个最小 phase
- 每个 phase 前必须重新读取本节安全边界
- policy / eligibility / audit / tests first
- Builder 不能给自己做最终验收
