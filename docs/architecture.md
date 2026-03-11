# Architecture

## System Overview

AI Team Studio uses a layered architecture:

```
+------------------+
|   Tauri Shell    |  Desktop window, system integration
+------------------+
|   React + TS     |  UI: project view, task board, logs, approvals
+------------------+
        |  HTTP (localhost:9800)
+------------------+
| FastAPI Routers  |  CRUD endpoints, orchestration API, tool execution
+------------------+
|   Orchestrator   |  Drives tasks through the 4-role agent pipeline
+------------------+
|   Tool Layer     |  File tools, Git tools, Shell executor (Phase 4A)
+------------------+
|  Safety + Gate   |  Risk classification, approval gating (Phase 4A)
+------------------+
| Agent Executors  |  Mock (Phase 3) → Model calls (Phase 6)
+------------------+
|     SQLite       |  Local persistence (tasks, runs, logs, approvals)
+------------------+
```

## Directory Structure

```
AI_Team_Studio/
  apps/desktop/          Tauri + React + TypeScript frontend
  services/runtime/      Python FastAPI local server
    agents/              Agent definitions, executors, orchestrator
    tools/               Tool implementations, safety, approval gating
    routers/             API endpoint handlers
  packages/shared/       Shared type definitions (future)
  data/                  SQLite database files
  docs/                  Documentation
  scripts/               Dev scripts
  tests/                 Acceptance test suites
  .claude/               Agent configs and commands
```

## Communication

- Frontend (React) runs inside Tauri's webview
- Runtime (FastAPI) runs as a separate local process on port 9800
- Frontend calls runtime via HTTP fetch to `http://127.0.0.1:9800/api/*`
- CORS is configured to allow local development

## Key Design Decisions

1. **Separate processes** over Tauri sidecar for Phase 0-1 simplicity
2. **HTTP communication** over Tauri IPC for cross-language simplicity
3. **SQLite in data/** for centralized, portable data storage
4. **Dark workstation theme** matching CLAUDE.md UI requirements

## Agent Orchestration (Phase 3)

The orchestrator drives tasks through a four-role pipeline:

```
pending → [Planner] → planning → [Builder] → in_progress → [QA] → reviewing → [Reviewer] → done
```

Key design decisions:
1. **Data-driven pipeline**: Role definitions are declarative (in `agents/definitions.py`), not hardcoded in control flow
2. **Swappable executor**: The `AgentExecutor` Protocol lets Phase 6 swap mock agents for real model calls without modifying the orchestrator
3. **Synchronous execution**: Phase 3 runs the pipeline synchronously; async/background execution can be added later
4. **Rejection loops**: Reviewer can reject up to 3 times, looping back to Builder each time
5. **Full audit trail**: Every step creates AgentRun records, log events, and tracks status transitions

## Tool Layer (Phase 4A)

The tool layer provides safe, auditable file/git/shell operations with a two-layer safety model:

```
Layer 1: WHITELIST (shell only)
  → Blocks unknown commands at the gate
Layer 2: RISK CLASSIFIER (all tools)
  → Detects dangerous arguments/paths via regex patterns
  → HIGH/CRITICAL risk → approval gating → blocks until human approves
```

Key design decisions:
1. **Two-layer safety**: Whitelist (blocks unknown commands) + Risk classifier (catches dangerous args to known commands)
2. **Approval gating**: High-risk tool invocations create ApprovalRequest records and block until a human approves
3. **Standalone tools**: Tools can be called via HTTP API or by the orchestrator/executor
4. **Role enforcement**: Tool access is checked against `AgentRoleDefinition.allowed_tools` using alias resolution
5. **Conservative defaults**: Write operations are size-limited, shell has a command whitelist, git is read-only
