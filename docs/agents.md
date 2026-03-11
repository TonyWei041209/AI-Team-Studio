# Agent System

## Overview

AI Team Studio uses a four-role agent pipeline to process tasks. Each role has a defined responsibility, tool permissions, and output format. The orchestrator drives a task through the pipeline sequentially.

## Agent Roles

### Planner (Blue)
- **Responsibility**: Break down goals into executable tasks and set acceptance criteria.
- **Tools**: read, grep, glob (read-only analysis)
- **Output**: goal_summary, task_breakdown, acceptance_criteria, risks
- **Constraint**: Cannot modify code or run risky commands.

### Builder (Green)
- **Responsibility**: Implement features based on the plan.
- **Tools**: read, edit, write, bash, grep, glob (full implementation)
- **Output**: changed_files, what_changed, why, validation, open_issues
- **Constraint**: Cannot do final verification of their own work.

### QA (Yellow)
- **Responsibility**: Verify implementation against acceptance criteria.
- **Tools**: read, bash, grep, glob (testing and verification)
- **Output**: validation_scope, test_actions, result (PASS/FAIL), findings
- **Constraint**: Cannot make large code changes; must be explicit about results.

### Reviewer (Magenta)
- **Responsibility**: Final approval or rejection decision.
- **Tools**: read, grep, glob (audit only)
- **Output**: review_summary, alignment_check, risk_review, decision, reason
- **Decision**: APPROVE / REQUEST_CHANGES / BLOCK
- **Constraint**: Cannot patch incomplete work; must not approve without sufficient info.

## Pipeline Flow

```
pending task
  |
  v
[Planner] --> task status: planning
  |
  v
[Builder] --> task status: in_progress
  |
  v
[QA]      --> task status: reviewing
  |
  v
[Reviewer]
  |
  +--> APPROVE --> task status: done
  |
  +--> REQUEST_CHANGES --> back to Builder (max 3 retries)
  |
  +--> BLOCK --> task status: failed
```

## Orchestrator

The `Orchestrator` class drives a pending task through the pipeline:

1. Validates the task is in `pending` status
2. Iterates through `AGENT_PIPELINE` (Planner, Builder, QA, Reviewer)
3. For each step:
   - Creates an `AgentRun` record (status: pending)
   - Sets status to `running`
   - Executes the agent
   - Sets status to `completed` or `failed`
   - Updates task status on success
   - Logs every transition
4. On agent failure: task status → `failed`, pipeline stops
5. On reviewer rejection: loops back to Builder (max 3 rejections)
6. On reviewer approval: task status → `done`

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/tasks/:id/orchestrate | Start pipeline for a pending task |
| GET | /api/tasks/:id/orchestration-status | Inspect runs and completion state |

### Executor Protocol

The agent execution logic is behind a `Protocol`:

```python
class AgentExecutor(Protocol):
    async def execute(self, role: AgentRole, task_context: dict) -> ExecutionResult
```

- **Phase 3**: `MockAgentExecutor` — returns simulated outputs
- **Phase 6B**: `ModelAgentExecutor` — calls real model providers

This design lets the orchestrator remain unchanged when real model calls are added.

## Model Integration (Phase 6B)

### Per-Role Executor Dispatch

The `Orchestrator` accepts an optional `executors` dict mapping specific roles to custom executors:

```python
Orchestrator(
    executor=mock_executor,                          # default for all roles
    executors={                                       # override for specific roles
        AgentRole.PLANNER: model_executor,
        AgentRole.REVIEWER: model_executor,
    },
)
```

The `_get_executor(role)` method checks the role-specific map first, then falls back to the default.

### Planner Model Integration

**Phase 6B Round 1** connects only the Planner to real LLM calls:

- Uses `ModelAgentExecutor` which calls `ProviderRegistry.complete()`
- Default provider: `anthropic` with model `claude-3-5-haiku-20241022`
- System prompt instructs the model to return **valid JSON only**
- Response is parsed and validated against `PlannerOutputSchema`
- Invalid JSON or schema violations → task fails with clear error (no silent fallback)

### Planner Output Schema

```json
{
  "goal_summary": "string (required, non-empty)",
  "task_breakdown": [
    {"step": 1, "description": "string", "role": "builder|qa|reviewer"}
  ],
  "acceptance_criteria": ["string", ...],
  "risks": ["string", ...],
  "dependencies": ["string", ...]
}
```

### Reviewer Model Integration

**Phase 6B Round 2** connects the Reviewer to real LLM calls alongside Planner:

- Uses `ModelAgentExecutor` which calls `ProviderRegistry.complete()`
- Default provider: `anthropic` with model `claude-3-5-haiku-20241022`
- System prompt instructs the model to return **valid JSON only**
- Response is parsed and validated against `ReviewerOutputSchema`
- Invalid JSON or schema violations → task fails with clear error (no silent fallback)
- Decision is mapped to `ReviewDecision` enum for orchestrator loop control

### Reviewer Output Schema

```json
{
  "decision": "approve | request_changes",
  "reason": "string (required, non-empty)",
  "issues_found": [
    {"severity": "critical|major|minor|nitpick", "description": "string"}
  ],
  "confidence": "high | medium | low"
}
```

### Fallback Behavior

- **Provider configured with API key** → `ModelAgentExecutor` used for Planner and Reviewer
- **Provider NOT configured** → Falls back to `MockAgentExecutor` (same as Phase 3)
- **Provider configured but call fails** → Task fails with error (no silent mock fallback)
- **Builder/QA** → Always use `MockAgentExecutor` (Phase 6B Round 2 scope)

### Manual Completion Endpoint

`POST /api/completion` — send arbitrary prompts to any configured provider for debugging.

## Key Files

| File | Purpose |
|------|---------|
| `agents/definitions.py` | Role registry, pipeline sequence, system prompts |
| `agents/executor.py` | AgentExecutor protocol and MockAgentExecutor |
| `agents/model_executor.py` | ModelAgentExecutor, PlannerOutputSchema, ReviewerOutputSchema, code-fence stripping |
| `agents/orchestrator.py` | Core orchestration engine with per-role executor dispatch |
| `routers/orchestration.py` | HTTP endpoints for orchestration |
| `routers/completion.py` | Manual completion debug endpoint |
