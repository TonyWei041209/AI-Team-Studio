# Execution Pipeline

Technical reference for the execution pipeline in AI Team Studio (Phase 7 + 8A + 8B).

---

## 1. Object Chain

```
execution_proposal → approval_request → execution_snapshot → execution_request
  → execution_result  (mode: dry_run | real_run | rollback)
  → execution_file_backups  (real_run only)
```

| Object | Table | Role |
|---|---|---|
| execution_proposal | `execution_proposals` | Agent-generated plan; triggers approval flow |
| approval_request | `approval_requests` | Human gate; links to proposal via `proposal_id` FK |
| execution_snapshot | `execution_snapshots` | Immutable content copy taken after approval; holds `snapshot_data` + `content_hash` |
| execution_request | `execution_requests` | Confirmed work order derived from snapshot; one per snapshot (`UNIQUE(snapshot_id)`) |
| execution_result | `execution_results` | Per-mode outcome record; `UNIQUE(execution_request_id, mode)` |
| execution_file_backup | `execution_file_backups` | Original file content stored before a real_run file_modify |

---

## 2. Execution Modes

| Mode | What it does | Idempotent | Status values |
|---|---|---|---|
| `dry_run` | Validates actions and paths, reports what would happen; no file writes | Returns existing result (`is_new=false`) on repeat | `pending`, `running`, `completed`, `failed` |
| `real_run` | Writes files to workspace, then executes commands; records backups for modified files | Returns existing result (`is_new=false`) on repeat | `pending`, `running`, `completed`, `failed` |
| `rollback` | Reverts a real_run: restores modified files from backup, deletes created files | Returns existing rollback result (`is_new=false`) on repeat | `pending`, `running`, `completed`, `failed` |

---

## 3. Allowed Execution Scope (Phase 7)

**Allowed (`ALLOWED_ACTION_TYPES` in `execution_eligibility_service.py`):**

| Action type | Behavior |
|---|---|
| `file_create` | Write new file; fails if file already exists |
| `file_modify` | Overwrite existing file; fails if file not found |

| `command_run` | Execute whitelisted local command; shell=False | Phase 8A |

**Blocked by eligibility gate (entire request rejected if any action has these types):**

- `file_delete`
- `git_commit`, `git_checkout`
- `unsupported`

The gate is all-or-nothing: if any single action is disallowed, the full execution_request is blocked before any file operation runs.

---

## 3a. Command Execution (Phase 8A + 8B)

`command_run` actions are fully integrated end-to-end: eligibility gate → restricted executor → execute API → `result_data.command_results` → TaskBoard viewer.

Commands are subject to a three-layer defense model:

### Layer 1: Eligibility gate (`execution_eligibility_service.py`)

| Check | Blocked reason | Detail |
|---|---|---|
| Whitelist | `command_not_whitelisted` | Only `node`, `npm`, `npx`, `yarn`, `pnpm`, `python`, `python3`, `pip`, `pip3`, `cargo`, `rustc`, `go`, `make`, `cmake`, `tsc`, `eslint`, `prettier`, `jest`, `pytest`, `vitest`, `mocha`, `git` |
| Shell metacharacters | `command_contains_forbidden_shell_syntax` | `&&`, `\|\|`, `;`, `>`, `>>`, `<`, `\|`, backticks |
| Subshell / substitution | `command_contains_subshell` | `$()`, `$(())` |
| Working dir boundary | `command_workdir_outside_workspace` | `working_dir` must resolve inside `workspace_root` |

### Layer 2: Executor re-validation (`scoped_command_executor.py`, TOCTOU defense)

Before each command runs, the executor re-checks all Layer 1 rules plus:

| Check | Detail |
|---|---|
| Blocked subcommands | `npm install/publish`, `pip install/uninstall`, `cargo add/install/publish`, `git push/commit/reset/checkout/merge/rebase/stash`, `yarn add/install`, `pnpm add/install` |

### Layer 3: Execution environment

| Control | Detail |
|---|---|
| `shell=False` | All commands run via `subprocess.run(args, shell=False)` — no shell interpretation |
| Env allowlist | Minimal set: `PATH`, `HOME`, `LANG`, `TMP`, `TEMP`, `VIRTUAL_ENV`, `NODE_PATH`, `PYTHONPATH`, plus Windows essentials. All other env vars stripped |
| `stdin=DEVNULL` | No interactive input |
| Timeout | 60 seconds per command (configurable) |
| Output cap | stdout/stderr each capped at 64KB |
| Fail-fast | First failure/timeout stops remaining commands |

### Execution order

Files execute first, then commands. If any file action fails, commands do not run.

### Command results in `result_data`

```json
{
  "file_results": [...],
  "command_results": [
    {
      "command": "npm test",
      "working_dir": "/path/to/workspace",
      "exit_code": 0,
      "stdout": "...",
      "stderr": "...",
      "duration_ms": 1234,
      "status": "success",
      "truncated": false
    }
  ]
}
```

### Command rollback

Commands are **not reversible**. The rollback system (Phase 7C) only covers file operations. If a real_run includes commands, rollback restores files but cannot undo command side effects.

### Frontend display (Phase 8B-2)

The TaskBoard real-run result viewer displays `command_results` alongside `file_results`:

| Field shown | Detail |
|---|---|
| command | Full command text (monospace, ellipsis on overflow) |
| status | Color-coded badge: green (success), red (failed), muted (skipped) |
| exit_code | Green for 0, red for non-zero |
| stdout | Pre-formatted block, truncated at 2000 chars in UI, max-height 120px with scroll |
| stderr | Pre-formatted block in red tint, same truncation |
| duration_ms | Displayed as ms (<1s) or seconds (≥1s) |
| error | Red text below command row (if present) |

When `command_results` is absent or empty, the section is not rendered (backward compatible).

---

## 4. Idempotent Semantics

All three execution entry points check for an existing result before doing work.

- **execute (real_run / dry_run):** if a result row already exists for `(execution_request_id, mode)`, returns it immediately with `is_new=false`.
- **rollback:** if a rollback result row already exists for the originating `execution_result_id`, returns it with `is_new=false`.
- **Database constraint:** `UNIQUE(execution_request_id, mode)` on `execution_results` enforces this at the storage layer.

A single execution_request can therefore have up to three result rows: one for `dry_run`, one for `real_run`, one for `rollback`.

---

## 5. Safety Boundaries

| Control | Detail |
|---|---|
| `workspace_root` | Explicitly passed to executor; resolved from `project.local_repo_path`; never inferred |
| Path validation | Relative paths only; rejects `../` traversal, absolute paths, symlink escapes outside workspace, and system directories |
| Sensitive path deny list | `.git/`, `.env`, `node_modules/`, and similar prefixes are rejected before any write |
| `snapshot_content_hash` | SHA-256 of canonical JSON verified against stored hash before execution proceeds |
| Eligibility gate | All-or-nothing check runs before any file I/O |
| Fail-fast (real_run) | Stops at first file error; remaining actions are marked `skipped`; no auto-rollback |
| Best-effort (rollback) | Continues past individual file failures; each file gets its own status in the result |
| Atomic writes | real_run uses `tempfile` + `os.replace()` to avoid partial writes |

---

## 6. File Backup and Rollback

**Pre-execution backup (real_run, file_modify only):**

Before overwriting a file, the executor reads the original content, computes its SHA-256, and writes a row to `execution_file_backups` with:
- `path` — workspace-relative path
- `original_content` — full file text
- `original_hash` — SHA-256 of original content

`file_create` actions do not produce a backup row (there is no prior content).

**Rollback operation per action type:**

| Original action | Rollback behavior |
|---|---|
| `file_modify` | Write `original_content` back from backup row |
| `file_create` | Delete (unlink) the created file |

**Per-file rollback statuses in result_data:**

| Status | Meaning |
|---|---|
| `restored` | file_modify backup successfully written back |
| `deleted` | file_create target successfully removed |
| `already_absent` | file_create target was already missing; treated as success |
| `rollback_failed` | Operation failed for this file; see error field |
| `skipped` | File was not in a success state in the original run |

---

## 7. Audit Trail Integration

The audit trail endpoint (`GET /tasks/{task_id}/audit-trail`) aggregates all pipeline objects for a task into a single timeline.

**Execution result event types:**

| Event type | Trigger |
|---|---|
| `execution_result:completed` | real_run or dry_run finished without error |
| `execution_result:failed` | real_run or dry_run hit an error |
| `execution_result:rollback_completed` | rollback finished without error |
| `execution_result:rollback_failed` | rollback hit an error |

**Sorting:** events are sorted by `timestamp ASC`, with `_EVENT_ORDER` used as a tiebreaker. Within the same timestamp, the fixed order is: `dry_run` (order 5) before `real_run` (order 5, same slot), before `rollback` (order 6).

**Summaries** are auto-generated from `result_data` at query time; no separate summary storage is required. Real-run summaries include command counts (`cmd_success`, `cmd_fail`) when commands were executed.

---

## 8. Regression Baseline

| Phase | Suites | Checks | Runner |
|---|---|---|---|
| 6E | 4 | ~277 | `scripts/run-6e-regression-isolated.py` |
| 6F | 3 | ~100 | `scripts/run-6f-regression-isolated.py` |
| 6G | 2 | ~60 | `scripts/run-6g-regression-isolated.py` |
| 7 | 8 | ~191 | `scripts/run-7-regression-isolated.py` |
| 8A | 2 | ~54 | `scripts/run-8a-regression-isolated.py` |
| 8B | 1 | ~38 | `scripts/run-8b-regression-isolated.py` |
| **Total** | **20** | **~720** | `scripts/run-all-regression.py` |

---

## 9. Still Blocked (Intentional)

The following remain blocked by design and are not scheduled:

| Capability | Reason |
|---|---|
| `file_delete` | Destructive; no rollback strategy yet |
| `git_commit`, `git_checkout` | Git write operations need full rollback strategy |
| `npm install`, `pip install`, etc. | Package install / network operations not allowed |
| `git push`, `git pull`, etc. | Network + repository mutation not allowed |
| Shell interpretation (`shell=True`) | Permanently blocked by design |
| Unconfirmed high-risk auto-execution | Requires human approval gate |
