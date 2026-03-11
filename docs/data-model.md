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
| GET | /api/projects/:id/tasks | List tasks (filter by status) |
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
| GET | /api/tasks/:id/logs | List logs for task |
| GET | /api/logs/recent | List recent logs |
