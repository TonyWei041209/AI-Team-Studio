# Data Model

## Core Entities

### Project
Container for a local software project.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | string | Project display name |
| local_repo_path | string | Absolute path to local repo |
| default_branch | string | Default git branch (default: "main") |
| description | string | Optional description |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### Task
A unit of work within a project.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| project_id | UUID | FK → Project |
| title | string | Task title |
| description | string | Detailed description |
| status | enum | pending/planning/in_progress/reviewing/done/failed |
| priority | enum | low/medium/high/critical |
| assigned_agent_role | enum? | planner/builder/qa/reviewer (nullable) |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### AgentRun
A single execution of an agent role against a task.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| task_id | UUID | FK → Task |
| role | enum | planner/builder/qa/reviewer |
| model_provider | string? | e.g. "anthropic", "openai" |
| model_name | string? | e.g. "claude-sonnet-4-20250514" |
| status | enum | pending/running/completed/failed |
| input_summary | string | What was given to the agent |
| output_summary | string | What the agent produced |
| started_at | datetime? | When execution began |
| ended_at | datetime? | When execution finished |
| created_at | datetime | Creation timestamp |
| raw_output | string? | Audit-grade full provider output (V23; lists not collapsed) |
| finish_reason | string? | Provider finish/stop reason (V23) |
| attempt_number | int? | Rejection round that produced this run (V24): first pass = 0; the builder→qa→security_reviewer→reviewer tail re-run after the Nth Reviewer `request_changes` = N. Planner/Architect (never re-run) are always 0. Set from the orchestrator's `rejection_count`; NULL on pre-V24 rows. Additive/write-side only — does not affect control flow. |

### ApprovalRequest
A pending approval for a high-risk action.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| task_id | UUID | FK → Task |
| run_id | UUID? | FK → AgentRun (nullable) |
| action_type | string | e.g. "delete_file", "shell_exec", "git_force" |
| action_payload | JSON string | Details of the action |
| status | enum | pending/approved/rejected |
| reviewer_comment | string | Human reviewer's comment |
| created_at | datetime | Creation timestamp |
| resolved_at | datetime? | When resolved |

### LogEvent
An auditable event in the system timeline.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| task_id | UUID? | FK → Task (nullable for system logs) |
| run_id | UUID? | FK → AgentRun (nullable) |
| level | enum | debug/info/warn/error |
| source | string | e.g. "system", "planner", "builder" |
| message | string | Human-readable message |
| payload | JSON string | Structured data |
| created_at | datetime | Creation timestamp |

## Task State Machine

```
pending → planning → in_progress → reviewing → done
  ↓          ↓            ↓             ↓
failed     failed       failed        failed
  ↓                                     ↓
pending                            in_progress
```

Valid transitions:
- `pending` → `planning`, `in_progress`, `failed`
- `planning` → `in_progress`, `failed`
- `in_progress` → `reviewing`, `failed`
- `reviewing` → `done`, `in_progress`, `failed`
- `failed` → `pending` (retry)
- `done` → (terminal)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/projects | Create project |
| GET | /api/projects | List projects |
| GET | /api/projects/:id | Get project |
| PATCH | /api/projects/:id | Update project |
| DELETE | /api/projects/:id | Delete project |
| POST | /api/projects/:id/tasks | Create task |
| GET | /api/projects/:id/tasks?status= | List tasks (optional filter: `?status=pending\|planning\|...`) |
| GET | /api/tasks/:id | Get task |
| PATCH | /api/tasks/:id/status | Update task status |
| POST | /api/tasks/:id/runs | Create agent run |
| GET | /api/tasks/:id/runs | List runs for task |
| GET | /api/runs/:id | Get run |
| PATCH | /api/runs/:id | Update run |
| POST | /api/tasks/:id/approvals | Create approval |
| GET | /api/tasks/:id/approvals | List approvals for task |
| GET | /api/approvals/pending | List pending approvals |
| PATCH | /api/approvals/:id | Resolve approval |
| POST | /api/logs | Create log event |
| GET | /api/tasks/:id/logs | List logs for task (`?level=debug\|info\|warn\|error`, `?limit=N`, default 100) |
| GET | /api/logs/recent | List recent logs (`?level=debug\|info\|warn\|error`, `?limit=N`, default 50) |
| POST | /api/tasks/:id/orchestrate | Start orchestration pipeline (optional body: `failure_rate`, `rejection_rate`, `delay_seconds`) |
| GET | /api/tasks/:id/orchestration-status | Get orchestration state: task status, runs, completion flag |
| GET | /api/tools | List all registered tools |
| POST | /api/tools/execute | Execute tool (risk check + approval gating; body: `tool_name`, `params`, optional `project_id`/`task_id`/`run_id`/`role`) |
| POST | /api/tools/execute-approved | Re-execute blocked tool after approval (body: `approval_id`, `tool_name`, `params`) |
| GET | /api/tasks/:id/proposals | List execution proposals |
| GET | /api/tasks/:id/audit-trail | Aggregated audit timeline |
| GET | /api/proposals/:id | Get single proposal |
| POST | /api/proposals/:id/freeze | Freeze approved proposal into snapshot |
| GET | /api/snapshots/:id | Get single snapshot |
| GET | /api/snapshots/:id/execution-request | Get execution request for snapshot |
| POST | /api/snapshots/:id/request-execution | Create execution request |
| GET | /api/execution-requests/:id | Get single execution request |
| PATCH | /api/execution-requests/:id | Confirm or reject execution request |
| POST | /api/execution-requests/:id/dry-run | Trigger dry-run execution (idempotent) |
| GET | /api/execution-requests/:id/dry-run | Read dry-run result |
| GET | /api/execution-requests/:id/action-plan | Read computed action plan (pure, not stored) |

## Execution Pipeline (Phase 6E)

### Object Chain

```
task
 └─ execution_proposal   (Builder output, structured plan)
      └─ approval_request (human decision: approved / rejected)
           └─ execution_snapshot  (frozen immutable copy, SHA-256 hash)
                └─ execution_request  (execution intent record)
                     └─ confirmed / rejected  (terminal, irreversible)
```

### ExecutionProposal

Builder agent produces a structured execution plan.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| task_id | UUID | FK → Task |
| run_id | UUID | FK → AgentRun |
| role | string | Agent role (default: "builder") |
| proposal_data | JSON string | Structured plan (files, commands, summary) |
| risk_level | enum | low/medium/high/critical |
| requires_approval | boolean | Whether human approval needed |
| approval_reasons | JSON string | List of reasons for approval |
| status | enum | pending/approved/rejected |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### ExecutionSnapshot

Frozen immutable copy of an approved proposal. Created via `freeze_snapshot()`.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| proposal_id | UUID | FK → ExecutionProposal (UNIQUE) |
| approval_id | UUID | FK → ApprovalRequest |
| task_id | UUID | FK → Task |
| snapshot_data | JSON string | Frozen copy of proposal_data |
| content_hash | string | SHA-256 of snapshot_data (tamper detection) |
| risk_level | string | Inherited from proposal |
| status | enum | frozen (only valid value) |
| created_at | datetime | Creation timestamp |

**Immutability guarantee**: Once created, snapshot_data and content_hash never change.
One proposal can have at most one snapshot (UNIQUE constraint on proposal_id).

### ExecutionRequest

Records execution intent, bound to a frozen snapshot.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| task_id | UUID | FK → Task |
| proposal_id | UUID | FK → ExecutionProposal |
| approval_id | UUID | FK → ApprovalRequest |
| snapshot_id | UUID | FK → ExecutionSnapshot (UNIQUE) |
| snapshot_content_hash | string | Copied from snapshot at creation |
| risk_level | string | Inherited from snapshot |
| status | enum | requested/confirmed/rejected |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### Execution Request State Machine

```
requested ──→ confirmed   (terminal, irreversible)
    │
    └──────→ rejected     (terminal, irreversible)
```

**Critical semantics**:
- `confirmed` means "human has confirmed execution intent"
- `confirmed` does NOT mean "execution has occurred"
- No executor exists yet — Phase 6F will add dry-run capability
- Terminal states cannot be changed (409 Conflict on retry)
- One snapshot can have at most one execution request (UNIQUE constraint)

### Audit Trail

`GET /api/tasks/{task_id}/audit-trail` aggregates the full object chain into a flat timeline.

Each event has 8 top-level fields:

| Field | Type | Description |
|-------|------|-------------|
| event_type | string | e.g. `proposal:created`, `approval:decided` |
| object_type | string | proposal / approval / snapshot / execution_request / execution_result |
| object_id | UUID | ID of the source object |
| status | string | Current status of the object |
| timestamp | datetime | When the event occurred |
| summary | string | Human-readable one-liner |
| related_ids | dict | Cross-references (task_id, proposal_id, etc.) |
| detail | dict | Supplementary data (not required by UI) |

Event types: `proposal:created`, `approval:decided`, `snapshot:frozen`,
`execution_request:created`, `execution_request:finalized`,
`execution_result:completed`, `execution_result:failed`.

Sort: timestamp ASC, then fixed event order for tie-breaking.
Only decided approvals included (pending filtered out).
Only completed/failed execution results included (pending/running filtered out).

## Dry-Run Execution (Phase 6F)

### Object Chain (extended)

```
task
 └─ execution_proposal   (Builder output, structured plan)
      └─ approval_request (human decision: approved / rejected)
           └─ execution_snapshot  (frozen immutable copy, SHA-256 hash)
                └─ execution_request  (execution intent record)
                     └─ confirmed / rejected  (terminal, irreversible)
                          └─ execution_result  (dry-run simulation output)
```

### ExecutionResult

Records the outcome of a dry-run execution against a confirmed execution request.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| execution_request_id | UUID | FK → ExecutionRequest (UNIQUE) |
| task_id | UUID | FK → Task |
| snapshot_id | UUID | FK → ExecutionSnapshot |
| snapshot_content_hash | string | Copied from snapshot at creation (tamper detection) |
| status | enum | pending/running/completed/failed |
| result_data | JSON string | Structured dry-run output (see below) |
| started_at | datetime? | When execution began |
| completed_at | datetime? | When execution finished |
| created_at | datetime | Creation timestamp |

**result_data structure:**

```json
{
  "mode": "dry_run",
  "summary": "Dry-run completed: 2 file action(s), 1 command action(s)",
  "planned_file_actions": [
    {"path": "src/foo.py", "action": "create", "status": "simulated"}
  ],
  "planned_command_actions": [
    {"command": "npm install", "working_dir": "/tmp/proj", "status": "simulated"}
  ],
  "warnings": [],
  "snapshot_content_hash": "sha256..."
}
```

**Idempotency**: One execution request can have at most one result (UNIQUE constraint).
Repeat POST returns 200 with existing result. POST on non-confirmed request returns 409.

### Dry-Run API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/execution-requests/:id/dry-run | Trigger dry-run (idempotent, 200/409) |
| GET | /api/execution-requests/:id/dry-run | Read dry-run result (200/404) |

### Dry-Run Semantics

- **dry-run ≠ real execution**: No files are written, no commands are run, no git operations occur.
- Only `confirmed` execution requests can trigger a dry-run (409 otherwise).
- The dry-run service reads the frozen snapshot, iterates proposed file actions and command actions, and marks each as `simulated`.
- `snapshot_content_hash` is verified at execution time for tamper detection.
- Warnings are generated for potentially risky operations (e.g., delete, force push).

## Action Plan (Phase 6G-A)

### Overview

An action plan is a **computed, read-only** view of a confirmed execution request's snapshot.
It normalizes raw snapshot data into standardized actions and evaluates each against a policy gate.
Action plans are **not stored in the database** — they are computed on-the-fly from snapshot data.

### Action Plan Contract

`GET /api/execution-requests/:id/action-plan` returns:

| Field | Type | Description |
|-------|------|-------------|
| execution_request_id | UUID | Source execution request |
| snapshot_id | UUID | Source snapshot |
| snapshot_content_hash | string | SHA-256 hash for integrity verification |
| actions | array | List of normalized actions (see below) |
| overall_risk | enum | Highest risk across all actions: safe/low/high/critical |
| has_denied | boolean | Whether any action was denied |
| needs_confirmation_count | integer | Count of actions needing confirmation |
| summary | string | Human-readable summary, e.g. "4 action(s): 2 allowed, 1 need confirmation, 1 denied" |

### NormalizedAction

Each element in the `actions` array:

| Field | Type | Description |
|-------|------|-------------|
| type | enum | file_create / file_modify / file_delete / command_run / git_commit / git_checkout / unsupported |
| target | string | File path or full command string |
| params | dict | Metadata: `{"operation": "create"}` for files, extra command fields for commands |
| risk_level | enum | safe / low / high / critical |
| policy_decision | enum | allow / deny / needs_confirmation |
| reason | string | Human-readable explanation of the policy decision |

### Policy Decision Semantics

| Decision | Meaning |
|----------|---------|
| `allow` | Safe to execute without further confirmation |
| `deny` | Must not be executed (critical risk or unsupported) |
| `needs_confirmation` | Requires explicit human confirmation before execution |

### Policy Rules Summary

| Action | Risk | Decision |
|--------|------|----------|
| file_create / file_modify | SAFE/LOW | allow |
| file_create / file_modify | HIGH | needs_confirmation |
| file_create / file_modify | CRITICAL | deny |
| file_delete (any) | SAFE/LOW | needs_confirmation |
| file_delete | HIGH/CRITICAL | deny |
| command_run (whitelisted) | SAFE/LOW | allow |
| command_run (not whitelisted) | SAFE/LOW | needs_confirmation |
| command_run | HIGH | needs_confirmation |
| command_run | CRITICAL | deny |
| git_commit / git_checkout | non-CRITICAL | needs_confirmation |
| git_commit / git_checkout | CRITICAL | deny |
| unsupported | any | deny |

### Key Semantic Clarifications

- **confirmed ≠ executed**: `confirmed` on an execution request means "human confirmed intent", not that any action has been carried out.
- **dry-run ≠ executed**: Dry-run simulates the execution plan without side effects. No files are written, no commands run.
- **action plan ≠ permission**: The action plan shows what *would* happen and the policy evaluation, but does not grant permission to execute. It is purely informational.
- **Pure computation**: Action plans reuse `tools.safety.RiskClassifier` and `tools.shell_executor.COMMAND_WHITELIST` for consistent risk evaluation across the system.
