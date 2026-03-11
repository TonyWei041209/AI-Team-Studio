# Architecture

## System Overview

AI Team Studio uses a three-layer architecture:

```
+------------------+
|   Tauri Shell    |  Desktop window, system integration
+------------------+
|   React + TS     |  UI: project view, task board, logs, approvals
+------------------+
        |  HTTP (localhost:9800)
+------------------+
| Python FastAPI   |  Task orchestration, model calls, tools, DB
+------------------+
|     SQLite       |  Local persistence
+------------------+
```

## Directory Structure

```
AI_Team_Studio/
  apps/desktop/          Tauri + React + TypeScript frontend
  services/runtime/      Python FastAPI local server
  packages/shared/       Shared type definitions (future)
  data/                  SQLite database files
  docs/                  Documentation
  scripts/               Dev scripts
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
