"""Read-only Git tools: git_status, git_diff, git_log.

All tools use ``asyncio.create_subprocess_exec`` with a timeout to avoid
blocking the event loop.  No write operations (commit, push, reset) are
provided — those require Phase 4B or later.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from tools.base import BaseTool, ToolCategory, ToolContext, ToolResult


# ── Shared base ───────────────────────────────────────────────


class _GitBaseTool(BaseTool):
    """Shared base for read-only git tools."""

    category = ToolCategory.GIT

    async def _run_git(
        self,
        args: list[str],
        cwd: str,
        timeout: float = 15.0,
    ) -> tuple[int, str, str]:
        """Run ``git <args>`` and return ``(returncode, stdout, stderr)``.

        Uses ``asyncio.to_thread(subprocess.run, ...)`` instead of
        ``asyncio.create_subprocess_exec`` for reliable Windows support
        inside uvicorn.
        """
        # Pre-check: verify cwd exists
        if not Path(cwd).is_dir():
            return -1, "", f"Directory does not exist: {cwd}"

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                timeout=timeout,
            )
            return (
                result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        except subprocess.TimeoutExpired:
            return -1, "", "Git command timed out"
        except FileNotFoundError:
            return -1, "", "git executable not found"
        except OSError as exc:
            return -1, "", f"Git error: {exc}"

    def _get_cwd(self, params: dict, context: ToolContext | None) -> str:
        """Resolve working directory from params or context."""
        cwd = params.get("cwd", "")
        if not cwd and context and context.working_dir:
            cwd = context.working_dir
        if not cwd:
            return ""
        return str(Path(cwd).resolve())


# ── git_status ────────────────────────────────────────────────


class GitStatusTool(_GitBaseTool):
    name = "git_status"
    description = "Show working tree status (git status --porcelain)"
    aliases = []

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        cwd = self._get_cwd(params, context)
        if not cwd:
            return ToolResult(
                success=False, output=None,
                error="No working directory specified",
                tool_name=self.name, risk_level="safe",
            )

        code, stdout, stderr = await self._run_git(["status", "--porcelain"], cwd)
        if code != 0:
            return ToolResult(
                success=False, output=None,
                error=f"git status failed: {stderr}",
                tool_name=self.name, risk_level="safe",
            )

        entries = []
        for line in stdout.strip().splitlines():
            if len(line) >= 3:
                status_code = line[:2].strip()
                filepath = line[3:]
                entries.append({"status": status_code, "path": filepath})

        return ToolResult(
            success=True,
            output={
                "cwd": cwd,
                "entries": entries,
                "clean": len(entries) == 0,
                "raw": stdout.strip(),
            },
            tool_name=self.name, risk_level="safe",
        )


# ── git_diff ──────────────────────────────────────────────────


class GitDiffTool(_GitBaseTool):
    name = "git_diff"
    description = "Show changes in working tree (git diff)"
    aliases = []

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        cwd = self._get_cwd(params, context)
        if not cwd:
            return ToolResult(
                success=False, output=None,
                error="No working directory specified",
                tool_name=self.name, risk_level="safe",
            )

        args = ["diff"]
        if params.get("staged", False):
            args.append("--cached")
        if params.get("ref"):
            args.append(params["ref"])
        if params.get("path"):
            args.extend(["--", params["path"]])

        code, stdout, stderr = await self._run_git(args, cwd)
        if code != 0:
            return ToolResult(
                success=False, output=None,
                error=f"git diff failed: {stderr}",
                tool_name=self.name, risk_level="safe",
            )

        max_lines = params.get("max_lines", 2000)
        lines = stdout.splitlines()
        truncated = len(lines) > max_lines
        if truncated:
            lines = lines[:max_lines]

        return ToolResult(
            success=True,
            output={
                "cwd": cwd,
                "diff": "\n".join(lines),
                "lines": len(lines),
                "truncated": truncated,
            },
            tool_name=self.name, risk_level="safe",
        )


# ── git_log ───────────────────────────────────────────────────


class GitLogTool(_GitBaseTool):
    name = "git_log"
    description = "Show commit log"
    aliases = []

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        cwd = self._get_cwd(params, context)
        if not cwd:
            return ToolResult(
                success=False, output=None,
                error="No working directory specified",
                tool_name=self.name, risk_level="safe",
            )

        max_count = params.get("max_count", 20)
        args = [
            "log",
            f"--max-count={max_count}",
            "--format=%H|%h|%s|%an|%ai",
        ]
        if params.get("ref"):
            args.append(params["ref"])

        code, stdout, stderr = await self._run_git(args, cwd)
        if code != 0:
            return ToolResult(
                success=False, output=None,
                error=f"git log failed: {stderr}",
                tool_name=self.name, risk_level="safe",
            )

        commits = []
        for line in stdout.strip().splitlines():
            parts = line.split("|", 4)
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "subject": parts[2],
                    "author": parts[3],
                    "date": parts[4],
                })

        return ToolResult(
            success=True,
            output={"cwd": cwd, "commits": commits, "count": len(commits)},
            tool_name=self.name, risk_level="safe",
        )
