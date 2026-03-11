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
- **Phase 6**: `ModelAgentExecutor` — calls real model providers

This design lets the orchestrator remain unchanged when real model calls are added.

## Key Files

| File | Purpose |
|------|---------|
| `agents/definitions.py` | Role registry and pipeline sequence |
| `agents/executor.py` | AgentExecutor protocol and MockAgentExecutor |
| `agents/orchestrator.py` | Core orchestration engine |
| `routers/orchestration.py` | HTTP endpoints for orchestration |
