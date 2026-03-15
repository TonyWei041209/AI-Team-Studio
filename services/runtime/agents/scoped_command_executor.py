"""Restricted command executor for Phase 8A-2.

Executes only whitelisted, safe command_run actions from the action plan.
All commands run without shell interpretation (subprocess, shell=False).

Phase 8A-2 first version restrictions:
- Only whitelisted base commands (COMMAND_WHITELIST)
- No shell metacharacters (&&, ||, ;, >, <, |)
- No subshell / command substitution (``, $())
- No package install / publish / network commands (BLOCKED_SUBCOMMANDS)
- working_dir must resolve inside workspace_root
- Minimal env allowlist (no secret leakage)
- 60-second timeout per command
- stdout/stderr capped at 64KB each
- stdin closed (no interactive commands)

Usage::

    from agents.scoped_command_executor import execute_scoped_commands

    results = execute_scoped_commands(
        execution_request_id, workspace_root, proposed_commands
    )
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from typing import Any

from agents.execution_eligibility_service import (
    _validate_command,
    _validate_command_workdir,
)


# ── Constants ─────────────────────────────────────────────

COMMAND_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 65536  # 64KB per stream

# Blocked subcommand patterns: base_command + first arg combinations
# that indicate package install / publish / network operations.
_BLOCKED_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset({"install", "i", "ci", "publish", "unpublish", "pack", "link"}),
    "npx": frozenset(),  # npx is allowed (runs local tools)
    "yarn": frozenset({"add", "install", "publish", "link"}),
    "pnpm": frozenset({"add", "install", "publish", "link"}),
    "pip": frozenset({"install", "uninstall", "download"}),
    "pip3": frozenset({"install", "uninstall", "download"}),
    "python": frozenset(),  # python itself is allowed
    "python3": frozenset(),
    "cargo": frozenset({"add", "install", "publish"}),
    "git": frozenset({"push", "pull", "fetch", "clone", "remote", "commit", "reset", "checkout", "switch", "merge", "rebase", "stash"}),
    "make": frozenset(),  # make targets are fine
}


# ── Env allowlist ─────────────────────────────────────────

_ENV_ALLOWLIST: frozenset[str] = frozenset({
    # Cross-platform essentials
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMP",
    "TEMP",
    "TMPDIR",
    # Windows-specific
    "USERPROFILE",
    "SystemRoot",
    "ComSpec",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "CommonProgramFiles",
    # Python/Node runtime
    "VIRTUAL_ENV",
    "NODE_PATH",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
})


def _build_safe_env() -> dict[str, str]:
    """Build a minimal env dict from allowlisted variables only."""
    env: dict[str, str] = {}
    for key in _ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


# ── Pre-execution re-validation ───────────────────────────

def _revalidate_command(
    cmd: str,
    working_dir: str | None,
    workspace_root: str,
) -> str | None:
    """Re-validate a command before execution. Returns error string or None."""
    # 1. Eligibility-level checks (TOCTOU defense)
    reasons = _validate_command(cmd)
    if reasons:
        return f"Command re-validation failed: {', '.join(reasons)}"

    # 2. Working dir boundary
    if working_dir and not _validate_command_workdir(working_dir, workspace_root):
        return f"working_dir escapes workspace: {working_dir}"

    # 3. Blocked subcommand check (package install / network / git write)
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()

    if len(parts) >= 2:
        base = os.path.basename(parts[0])
        subcommand = parts[1].lower()
        blocked_subs = _BLOCKED_SUBCOMMANDS.get(base)
        if blocked_subs is not None and subcommand in blocked_subs:
            return f"Blocked subcommand: '{base} {subcommand}' (package install / network / git write not allowed in 8A)"

    return None


# ── Output truncation ─────────────────────────────────────

def _truncate(text: str, max_chars: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    """Truncate text to max_chars. Returns (text, was_truncated)."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n... (truncated at 64KB)", True


# ── Main executor ─────────────────────────────────────────

def execute_scoped_commands(
    execution_request_id: str,
    workspace_root: str,
    proposed_commands: list[dict | str],
) -> list[dict]:
    """Execute command_run actions with full safety restrictions.

    Parameters
    ----------
    execution_request_id : str
        For audit/logging context.
    workspace_root : str
        Project workspace root (must exist).
    proposed_commands : list
        From snapshot_data["proposed_commands"]. Each entry is a string
        or dict with at least {"command": "..."}.

    Returns
    -------
    list[dict]
        Per-command results. Each dict has:
        command, working_dir, exit_code, stdout, stderr,
        duration_ms, status, truncated, error (optional).
    """
    real_workspace = os.path.realpath(workspace_root)
    safe_env = _build_safe_env()
    results: list[dict] = []

    for c in proposed_commands:
        if isinstance(c, str):
            cmd = c.strip()
            working_dir = None
        else:
            cmd = c.get("command", "").strip()
            working_dir = c.get("working_dir")

        if not cmd:
            continue

        # Determine cwd
        if working_dir:
            resolved_cwd = os.path.realpath(
                os.path.normpath(os.path.join(real_workspace, working_dir))
            )
        else:
            resolved_cwd = real_workspace

        result_entry: dict[str, Any] = {
            "command": cmd,
            "working_dir": resolved_cwd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
            "status": "pending",
            "truncated": False,
        }

        # Re-validate before execution
        error = _revalidate_command(cmd, working_dir, real_workspace)
        if error:
            result_entry["status"] = "failed"
            result_entry["error"] = error
            results.append(result_entry)
            break  # fail-fast

        # Parse command into args (no shell=True)
        try:
            args = shlex.split(cmd)
        except ValueError as e:
            result_entry["status"] = "failed"
            result_entry["error"] = f"Failed to parse command: {e}"
            results.append(result_entry)
            break

        # Execute
        start = time.monotonic()
        try:
            proc = subprocess.run(
                args,
                cwd=resolved_cwd,
                env=safe_env,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)

            stdout, stdout_trunc = _truncate(proc.stdout or "")
            stderr, stderr_trunc = _truncate(proc.stderr or "")

            result_entry["exit_code"] = proc.returncode
            result_entry["stdout"] = stdout
            result_entry["stderr"] = stderr
            result_entry["duration_ms"] = elapsed_ms
            result_entry["truncated"] = stdout_trunc or stderr_trunc

            if proc.returncode == 0:
                result_entry["status"] = "success"
            else:
                result_entry["status"] = "failed"
                result_entry["error"] = f"Non-zero exit code: {proc.returncode}"

        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result_entry["status"] = "timeout"
            result_entry["duration_ms"] = elapsed_ms
            result_entry["error"] = f"Command timed out after {COMMAND_TIMEOUT_SECONDS}s"

        except FileNotFoundError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result_entry["status"] = "failed"
            result_entry["duration_ms"] = elapsed_ms
            result_entry["error"] = f"Command not found: {args[0]}"

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result_entry["status"] = "failed"
            result_entry["duration_ms"] = elapsed_ms
            result_entry["error"] = f"Execution error: {e}"

        results.append(result_entry)

        # Fail-fast: stop on first failure/timeout
        if result_entry["status"] != "success":
            break

    return results
