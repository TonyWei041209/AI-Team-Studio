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
| Agent Executors  |  Mock (Phase 3) → Model calls (Phase 6B)
+------------------+
| Provider Layer   |  Anthropic, OpenAI-compat, Gemini (Phase 6A)
+------------------+
|     SQLite       |  Local persistence (tasks, runs, logs, approvals, settings)
+------------------+
```

## Directory Structure

```
AI_Team_Studio/
  apps/desktop/          Tauri + React + TypeScript frontend
    src/
      api/               Typed fetch wrapper + domain API functions
      components/        Shared UI components (StatusIndicator)
      hooks/             Custom React hooks (useProjects, useTasks, etc.)
      panels/            Top-level panel components (Project, Task, Approvals, Logs)
      types/             TypeScript interfaces matching backend Pydantic models
  services/runtime/      Python FastAPI local server
    agents/              Agent definitions, executors, orchestrator
    providers/           LLM provider abstraction (Anthropic, OpenAI, Gemini)
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
3. **Per-role executor dispatch** (Phase 6B): Orchestrator accepts `executors: dict[AgentRole, AgentExecutor]` for role-specific overrides
4. **Synchronous execution**: Phase 3 runs the pipeline synchronously; async/background execution can be added later
5. **Rejection loops**: Reviewer can reject up to 3 times, looping back to Builder each time
6. **Full audit trail**: Every step creates AgentRun records, log events, and tracks status transitions

## Model Agent Executor (Phase 6B + 6C + 6D)

The `ModelAgentExecutor` calls real LLM providers via the ProviderRegistry:

```
Orchestrator._get_executor(role)
  ├── PLANNER → ModelAgentExecutor (when enabled in role_model_settings + API key)
  │                 └── _resolve_provider() reads from role_model_settings DB
  │                 └── ProviderRegistry.get(cfg.provider).complete(model=cfg.model)
  │                 └── Parse JSON → PlannerOutputSchema.validate()
  ├── BUILDER → ModelAgentExecutor (Phase 6D: plan-only, when enabled + API key)
  │                 └── _resolve_provider() reads from role_model_settings DB
  │                 └── ProviderRegistry.get(cfg.provider).complete(model=cfg.model)
  │                 └── Parse JSON → BuilderOutputSchema.validate()
  │                 └── NOTE: No tool execution — plan output only
  ├── QA      → MockAgentExecutor (always)
  └── REVIEWER → ModelAgentExecutor (when enabled in role_model_settings + API key)
                      └── _resolve_provider() reads from role_model_settings DB
                      └── ProviderRegistry.get(cfg.provider).complete(model=cfg.model)
                      └── Parse JSON → ReviewerOutputSchema.validate()
```

Key design decisions:
1. **Config-driven routing (Phase 6C)**: `role_model_settings` DB table is the sole runtime truth source
2. **Definitions as seed only**: `definitions.py` provides system prompts and V5 migration defaults, NOT runtime routing
3. **Dual-model support**: Same provider, different models per role (e.g., Planner→opus, Reviewer→sonnet)
4. **Structured JSON output**: Planner, Builder, and Reviewer must return valid JSON matching their schemas
5. **No silent fallback**: If role is enabled but misconfigured, task fails with clear error
6. **Builder plan-only (Phase 6D)**: Builder outputs change plan but does NOT execute tools, file ops, or git
7. **QA blocked**: Cannot enable real models; API rejects the request
8. **Code fence stripping**: Handles common LLM behavior of wrapping JSON in markdown fences
9. **Output summary truncation**: Large structured outputs are summarized before storage in AgentRun

## Data Model (Phase 6C)

### role_model_settings (Schema V5)

```sql
role       TEXT PRIMARY KEY,  -- planner, builder, qa, reviewer
provider   TEXT NOT NULL DEFAULT 'mock',
model      TEXT NOT NULL DEFAULT '',
enabled    INTEGER NOT NULL DEFAULT 0
```

Seeded from definitions.py defaults on first V5 migration.
Updated via `PATCH /api/settings/role-models` with strong validation.

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

## Audit Trail (Phase 4B)

Tool execution events are logged to the `log_events` table with `source='tool_audit'`:

1. **Atomic approval consumption**: `consume_approval()` uses SQL CAS (`UPDATE WHERE status='approved'`) — an approval can only be used once
2. **11 audit event types**: request, risk_classified, blocked, success, failed, role_denied, execute_approved request/success/failed/denied/already_consumed
3. **Param sanitization**: Sensitive keys (password, token, etc.) redacted to `***`, previews truncated to 200/500 chars
4. **HTTP status semantics**: 409 for already-consumed, 403 for pending/rejected/not_found denials

## Frontend (Phase 5)

The frontend is a React 19 + TypeScript single-page application inside Tauri's webview:

```
App.tsx (shell)
  state: activeTab, selectedProjectId, connectionState, pendingCount
  │
  ├── ProjectPanel     List/create/select projects (GET/POST /api/projects)
  ├── TaskBoard        Task list + create for selected project
  ├── ApprovalsPanel   Pending approvals + approve/reject flow
  ├── LogsPanel        Terminal-style log viewer with level filter
  └── SettingsPanel    Provider config, API keys, test connection (Phase 6A)
```

Key design decisions:
1. **Zero dependencies added**: No react-router, no state manager, no axios — uses native fetch and useState
2. **Tab navigation**: Simple `useState<TabId>` in App — no routing library needed for 4 panels
3. **Lifted state**: `selectedProjectId` lives in App, passed to TaskBoard; each panel manages its own fetch state via custom hooks
4. **Typed API client**: Centralized `api/client.ts` with `ApiError` class; domain-specific functions in `api/projects.ts`, `api/tasks.ts`, etc.
5. **Custom hooks pattern**: `useProjects()`, `useTasks(projectId)`, `useApprovals()`, `useLogs(level?)` — consistent loading/error/refresh API
6. **CSS variables**: All styling uses dark theme variables from `index.css` — no inline colors

## Provider Layer (Phase 6A)

Unified LLM provider abstraction supporting Anthropic, OpenAI-compatible (OpenAI, DeepSeek, Kimi, MiniMax), and Google Gemini.

Key design decisions:
1. **Abstract BaseProvider**: `complete()`, `list_models()`, `healthcheck()` — all providers implement same interface
2. **Registry pattern**: `ProviderRegistry` singleton discovers, configures, and exposes all providers
3. **Settings in SQLite**: API keys stored in `provider_settings` table, never in git-tracked files
4. **Key masking**: All API responses mask keys (`sk-****1234`), logs never contain full keys
5. **OpenAI-compatible reuse**: Single `OpenAICompatibleProvider` class with configurable `base_url` for DeepSeek, Kimi, MiniMax
6. **Startup loading**: Settings applied to live registry during FastAPI lifespan startup

See `docs/providers.md` for full details.
