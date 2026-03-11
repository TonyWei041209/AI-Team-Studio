# Claude Code 启动提示词

## 1. 第一次启动（推荐）

请严格按照仓库中的 `CLAUDE.md`、`.claude/agents/` 和《Claude_Code_执行计划书_优化版.pdf》推进本项目。

当前只做 **Phase 0 + Phase 1**，目标是搭建最小可运行骨架：
- Tauri 桌面壳
- React + TypeScript 前端
- FastAPI runtime
- SQLite 初始化
- 前后端 health check
- 首页空壳

开始前先输出：
1. 阶段目标
2. 文件改动计划
3. 设计理由
4. 验收标准

完成后再输出阶段总结，不要提前做 Phase 2 之后的内容。

---

## 2. 第二次启动（进入任务系统）

现在继续推进 **Phase 2：核心数据模型与任务系统**。

请先阅读：
- `CLAUDE.md`
- `docs/architecture.md`（如果已存在）
- `docs/data-model.md`（如果已存在）
- 《Claude_Code_执行计划书_优化版.pdf》中的数据模型和 Phase 2 要求

只做以下内容：
- Project / Task / AgentRun / ApprovalRequest / LogEvent 数据模型
- SQLite schema
- 基础 CRUD
- 基础任务状态流转

开始前先输出计划，完成后输出阶段总结。

---

## 3. 第三次启动（进入 agent 编排）

现在推进 **Phase 3：Agent 与 Orchestrator**。

请读取：
- `CLAUDE.md`
- `.claude/agents/*.md`
- 已有数据模型与 runtime 代码
- 《Claude_Code_执行计划书_优化版.pdf》Phase 3 部分

本阶段只做：
- Planner / Builder / QA / Reviewer 的角色结构
- 简单 orchestrator
- 一轮基本任务流转

不要提前接复杂自动化。

---

## 4. 审查模式

请扮演 Reviewer，不要修改代码。只根据以下内容进行审查：
- 当前任务目标
- 当前 diff
- 验收标准
- 最近测试结果

输出：
1. Review Summary
2. Alignment Check
3. Risk Review
4. Decision
5. Reason

---

## 5. 修复模式

请扮演 Builder，只修复当前失败项，不做额外重构。

输入材料：
- 当前任务
- 失败日志
- 相关 diff
- 验收标准

要求：
- 最小修复
- 列出修改文件
- 说明验证方式
- 说明剩余风险
