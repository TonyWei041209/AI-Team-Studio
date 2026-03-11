# Tool Layer (Phase 4A)

## Overview

The tool layer provides a safe, extensible framework for file system operations, read-only Git queries, and controlled shell execution.  Every tool invocation passes through a two-layer safety model before execution.

```
Request (router or orchestrator)
    │
    ├── Role permission check      (allowed_tools in AgentRoleDefinition)
    ├── Param validation            (BaseTool.validate_params)
    ├── Layer 1: Whitelist check    (shell only — COMMAND_WHITELIST)
    ├── Layer 2: Risk classifier    (pattern matching on params)
    │       ├── SAFE / LOW  → execute
    │       └── HIGH / CRITICAL → ApprovalGate → block until approved
    │
    └── Tool execution → ToolResult
```

## Architecture

```
services/runtime/
  tools/
    __init__.py           Package marker
    base.py               ToolResult, BaseTool, ToolContext, ToolRegistry
    safety.py             RiskLevel, RiskRule, RiskClassifier
    approval_gate.py      ApprovalGate (creates ApprovalRequest, blocks execution)
    file_tools.py         ReadFileTool, ListDirectoryTool, WriteFileTool
    git_tools.py          GitStatusTool, GitDiffTool, GitLogTool
    shell_executor.py     ShellExecutorTool (whitelist + timeout)
  routers/
    tools.py              HTTP endpoints for tool execution
```

## Tool Registry

All tools are registered in a singleton `ToolRegistry` (see `base.py:get_registry()`).  Each tool has:
- **name**: canonical identifier (e.g. `"read_file"`)
- **aliases**: short names used in `allowed_tools` (e.g. `["read"]`)
- **category**: `file`, `git`, or `shell`

## Tools Reference

### File Tools

| Tool | Aliases | Params | Risk | Description |
|------|---------|--------|------|-------------|
| `read_file` | `read` | `path`, `encoding?`, `max_size?` | SAFE | Read file content (1MB limit) |
| `list_directory` | `ls` | `path`, `max_entries?` | SAFE | List directory entries (500 limit) |
| `write_file` | `write`, `edit` | `path`, `content`, `encoding?`, `create_parents?` | LOW* | Write content to file (512KB limit) |

*`write_file` is escalated to HIGH/CRITICAL by the risk classifier for sensitive paths (.env, system dirs).

### Git Tools

| Tool | Aliases | Params | Risk | Description |
|------|---------|--------|------|-------------|
| `git_status` | — | `cwd?` | SAFE | `git status --porcelain` |
| `git_diff` | — | `cwd?`, `staged?`, `ref?`, `path?`, `max_lines?` | SAFE | `git diff` with options |
| `git_log` | — | `cwd?`, `max_count?`, `ref?` | SAFE | Structured commit log |

All git tools use a 15-second timeout and are strictly read-only.

### Shell Executor

| Tool | Aliases | Params | Risk | Description |
|------|---------|--------|------|-------------|
| `shell` | `bash` | `command`, `cwd?`, `timeout?` | LOW* | Execute whitelisted command |

*Escalated to HIGH/CRITICAL by risk classifier for dangerous arguments.

## Risk Classification

### Risk Levels

| Level | Auto-execute? | Description |
|-------|---------------|-------------|
| SAFE | Yes | Read-only, no side effects |
| LOW | Yes | Minor side effects |
| HIGH | No — needs approval | Significant side effects |
| CRITICAL | No — always needs approval | Dangerous or destructive |

### Detected Patterns (shell)

- **CRITICAL**: `rm -rf`, `git reset`, `git push --force`, `sudo`, `mkfs`, `dd`, pipe to shell, `del /s` (Win), `format` (Win)
- **HIGH**: `rm`, `git clean`, `git checkout --`, `chmod`, `chown`, `del` (Win)

### Detected Patterns (file)

- **HIGH**: path traversal (`../`), sensitive dotfiles (`.env`, `.ssh`, `.gitconfig`)
- **CRITICAL**: system directories (`/etc/`, `C:\Windows\`)

## Approval Workflow

```
1. POST /api/tools/execute  {tool_name: "shell", params: {command: "rm file.txt"}}
   → Risk classified as HIGH (delete command)
   → ApprovalRequest created in DB
   → Response: {blocked: true, approval_id: "abc-123", error: "Action blocked: ..."}

2. Human reviews in UI → PATCH /api/approvals/abc-123 {status: "approved"}

3. POST /api/tools/execute-approved {approval_id: "abc-123", tool_name: "shell", params: ...}
   → Approval verified → tool executes → Response: {success: true, output: {...}}
```

## Role Permission Matrix

| Tool | Planner | Builder | QA | Reviewer |
|------|---------|---------|-----|----------|
| read_file | ✓ | ✓ | ✓ | ✓ |
| list_directory | ✓ | ✓ | ✓ | ✓ |
| write_file | — | ✓ | — | — |
| git_status | ✓ | ✓ | ✓ | ✓ |
| git_diff | ✓ | ✓ | ✓ | ✓ |
| git_log | ✓ | ✓ | ✓ | ✓ |
| shell | — | ✓ | ✓ | — |

Enforcement: `ToolRegistry.is_allowed()` checks tool name and aliases against `AgentRoleDefinition.allowed_tools`.

## Shell Command Whitelist

Build/test: `npm`, `npx`, `yarn`, `pnpm`, `python`, `pip`, `pytest`, `mypy`, `ruff`, `black`, `isort`, `flake8`, `tsc`, `eslint`, `prettier`, `cargo`, `rustc`, `make`, `cmake`

Inspection: `ls`, `dir`, `cat`, `head`, `tail`, `wc`, `find`, `grep`, `rg`, `tree`, `file`, `stat`, `du`, `df`, `echo`, `printf`, `type`

Version control: `git` (read-only args; dangerous args caught by risk classifier)

Runtime: `node`, `deno`, `bun`, `uname`, `whoami`, `hostname`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tools` | List all registered tools |
| POST | `/api/tools/execute` | Execute tool (with risk check + gating) |
| POST | `/api/tools/execute-approved` | Re-execute after approval |

## Key Files

| File | Purpose |
|------|---------|
| `services/runtime/tools/base.py` | ToolResult, BaseTool, ToolRegistry |
| `services/runtime/tools/safety.py` | RiskLevel, RiskRule, RiskClassifier |
| `services/runtime/tools/approval_gate.py` | Approval gating integration |
| `services/runtime/tools/file_tools.py` | File system tools |
| `services/runtime/tools/git_tools.py` | Read-only git tools |
| `services/runtime/tools/shell_executor.py` | Controlled shell executor |
| `services/runtime/routers/tools.py` | HTTP endpoints |
