"""Tool protocol, result container, context, and registry.

Every tool inherits from BaseTool and registers itself in the global
ToolRegistry via get_registry().  The registry is the single lookup
point used by routers/tools.py and the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────


class ToolCategory(str, Enum):
    FILE = "file"
    GIT = "git"
    SHELL = "shell"


# ── Result container ──────────────────────────────────────────


@dataclass
class ToolResult:
    """Outcome of a single tool invocation.

    Attributes
    ----------
    success       Whether the tool completed without errors.
    output        The tool's primary output (string, list, dict, …).
    error         Human-readable error message (only when success is False).
    tool_name     Name of the tool that produced this result.
    risk_level    The classified risk level of this invocation.
    blocked       True if execution was blocked pending approval.
    approval_id   UUID of the ApprovalRequest if blocked.
    """

    success: bool
    output: Any
    error: str | None = None
    tool_name: str = ""
    risk_level: str = "safe"
    blocked: bool = False
    approval_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "blocked": self.blocked,
            "approval_id": self.approval_id,
        }


# ── Execution context ────────────────────────────────────────


@dataclass
class ToolContext:
    """Execution context passed to tools for scoping and audit.

    Attributes
    ----------
    project_id     The project being worked on.
    task_id        The task this tool invocation relates to (optional).
    run_id         The agent run invoking the tool (optional).
    role           The agent role requesting the tool (optional).
    working_dir    The project's local_repo_path (filesystem root for tools).
    """

    project_id: str
    task_id: str | None = None
    run_id: str | None = None
    role: str | None = None
    working_dir: str = ""


# ── Base tool ─────────────────────────────────────────────────


class BaseTool:
    """Abstract base for all tools.

    Subclasses must set ``name``, ``category``, and override ``execute()``.
    The optional ``aliases`` list lets short names (like ``"read"``) resolve
    to the canonical tool name (like ``"read_file"``).
    """

    name: str = ""
    description: str = ""
    category: ToolCategory = ToolCategory.FILE
    aliases: list[str] = []

    def validate_params(self, params: dict) -> str | None:
        """Return an error string if *params* are invalid, else ``None``."""
        return None

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute the tool.  Must be overridden by subclasses."""
        raise NotImplementedError


# ── Registry ──────────────────────────────────────────────────


class ToolRegistry:
    """Maps tool names (and aliases) to BaseTool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        # alias → canonical name
        self._alias_map: dict[str, str] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        for alias in tool.aliases:
            self._alias_map[alias] = tool.name

    def get(self, name: str) -> BaseTool | None:
        """Look up by canonical name or alias."""
        canonical = self._alias_map.get(name, name)
        return self._tools.get(canonical)

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category.value,
            }
            for t in self._tools.values()
        ]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def is_allowed(self, tool_name: str, allowed_tools: list[str]) -> bool:
        """Check whether *tool_name* (or any of its aliases) is in *allowed_tools*.

        ``allowed_tools`` comes from ``AgentRoleDefinition.allowed_tools`` and
        uses short alias names like ``"read"``, ``"bash"``, ``"write"``.
        """
        # Direct match on canonical name
        if tool_name in allowed_tools:
            return True
        # Check if any of the tool's aliases appear in allowed_tools
        tool = self._tools.get(tool_name)
        if tool is not None:
            for alias in tool.aliases:
                if alias in allowed_tools:
                    return True
        # Also resolve if tool_name itself is an alias
        canonical = self._alias_map.get(tool_name)
        if canonical and canonical in allowed_tools:
            return True
        return False


# ── Singleton registry ────────────────────────────────────────


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the global tool registry, creating it on first call."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_all_tools(_registry)
    return _registry


def _register_all_tools(registry: ToolRegistry) -> None:
    from tools.file_tools import ListDirectoryTool, ReadFileTool, WriteFileTool
    from tools.git_tools import GitDiffTool, GitLogTool, GitStatusTool
    from tools.shell_executor import ShellExecutorTool

    registry.register(ReadFileTool())
    registry.register(ListDirectoryTool())
    registry.register(WriteFileTool())
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    registry.register(GitLogTool())
    registry.register(ShellExecutorTool())
