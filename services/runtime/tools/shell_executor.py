"""Controlled shell executor with command whitelist, timeout, and output capture.

Safety model
------------
Layer 1 — **Whitelist** (this module):
    Only commands whose base name appears in ``COMMAND_WHITELIST`` can
    execute.  Everything else is rejected immediately.

Layer 2 — **Risk classifier** (``tools/safety.py``):
    Even whitelisted commands have their full argument string scanned
    for dangerous patterns (e.g. ``git reset --hard``, ``rm -rf /``).
    Matches trigger the approval gate.

Both layers are applied *before* the subprocess is spawned.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from pathlib import Path

from tools.base import BaseTool, ToolCategory, ToolContext, ToolResult


# ── Whitelist ─────────────────────────────────────────────────

COMMAND_WHITELIST: frozenset[str] = frozenset({
    # Build & test
    "npm", "npx", "yarn", "pnpm",
    "python", "python3", "pip", "pip3",
    "pytest", "mypy", "ruff", "black", "isort", "flake8",
    "tsc", "eslint", "prettier",
    "cargo", "rustc",
    "make", "cmake",
    # Inspection & filesystem
    "ls", "dir", "cat", "head", "tail", "wc", "find", "grep", "rg",
    "tree", "file", "stat", "du", "df", "mkdir",
    "echo", "printf", "type",
    # Git (read-only subset; dangerous args caught by RiskClassifier)
    "git",
    # System info
    "uname", "whoami", "hostname",
    "node", "deno", "bun",
})

# ── Limits ────────────────────────────────────────────────────

MAX_OUTPUT_SIZE = 262_144       # 256 KB per stream
DEFAULT_TIMEOUT = 30.0          # seconds
MAX_TIMEOUT = 120.0             # seconds


# ── Tool ──────────────────────────────────────────────────────


class ShellExecutorTool(BaseTool):
    name = "shell"
    description = "Execute a whitelisted shell command with timeout and output capture"
    category = ToolCategory.SHELL
    aliases = ["bash"]

    def validate_params(self, params: dict) -> str | None:
        if "command" not in params:
            return "Missing required parameter: command"
        return None

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        err = self.validate_params(params)
        if err:
            return ToolResult(success=False, output=None, error=err, tool_name=self.name)

        command = params["command"]
        timeout = min(params.get("timeout", DEFAULT_TIMEOUT), MAX_TIMEOUT)

        # ── Layer 1: whitelist check ──────────────────────────
        base_cmd = self._extract_base_command(command)
        if base_cmd is None:
            return ToolResult(
                success=False, output=None,
                error=f"Could not parse command: {command}",
                tool_name=self.name, risk_level="high",
            )

        if base_cmd not in COMMAND_WHITELIST:
            return ToolResult(
                success=False, output=None,
                error=(
                    f"Command not in whitelist: '{base_cmd}'. "
                    f"Allowed: {sorted(COMMAND_WHITELIST)}"
                ),
                tool_name=self.name, risk_level="high",
            )

        # ── Resolve working directory ─────────────────────────
        cwd = params.get("cwd", "")
        if not cwd and context and context.working_dir:
            cwd = context.working_dir
        if cwd:
            cwd = str(Path(cwd).resolve())
        else:
            cwd = None  # subprocess default

        # ── Pre-check cwd existence ──────────────────────────
        if cwd and not Path(cwd).is_dir():
            return ToolResult(
                success=False, output=None,
                error=f"Working directory does not exist: {cwd}",
                tool_name=self.name, risk_level="low",
            )

        # ── Execute ───────────────────────────────────────────
        # Uses asyncio.to_thread(subprocess.run) instead of
        # asyncio.create_subprocess_shell for reliable Windows
        # support inside uvicorn.
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                timeout=timeout,
            )

            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")

            # Truncate if too large
            stdout_truncated = len(stdout) > MAX_OUTPUT_SIZE
            stderr_truncated = len(stderr) > MAX_OUTPUT_SIZE
            if stdout_truncated:
                stdout = stdout[:MAX_OUTPUT_SIZE] + "\n... (truncated)"
            if stderr_truncated:
                stderr = stderr[:MAX_OUTPUT_SIZE] + "\n... (truncated)"

            return ToolResult(
                success=result.returncode == 0,
                output={
                    "command": command,
                    "exit_code": result.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "cwd": cwd or "(default)",
                },
                error=stderr if result.returncode != 0 else None,
                tool_name=self.name,
                risk_level="low",
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False, output=None,
                error=f"Command timed out after {timeout}s: {command}",
                tool_name=self.name, risk_level="low",
            )
        except Exception as exc:
            return ToolResult(
                success=False, output=None,
                error=f"Shell execution error: {type(exc).__name__}: {exc}",
                tool_name=self.name, risk_level="low",
            )

    # ── Internal ──────────────────────────────────────────────

    @staticmethod
    def _extract_base_command(command: str) -> str | None:
        """Extract the first token (base command name) from a shell string.

        Handles paths (``/usr/bin/python`` → ``python``) and Windows
        ``.exe`` suffixes (``python.exe`` → ``python``).
        """
        try:
            tokens = shlex.split(command)
            if not tokens:
                return None
            base = tokens[0].replace("\\", "/").split("/")[-1]
            if base.lower().endswith(".exe"):
                base = base[:-4]
            return base
        except ValueError:
            # shlex.split fails on unmatched quotes — fallback
            parts = command.strip().split()
            if not parts:
                return None
            base = parts[0].replace("\\", "/").split("/")[-1]
            if base.lower().endswith(".exe"):
                base = base[:-4]
            return base
