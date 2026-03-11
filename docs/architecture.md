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
| FastAPI Routers  |  CRUD endpoints, orchestration API
+------------------+
|   Orchestrator   |  Drives tasks through the 4-role agent pipeline
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
