"""File system tools: read_file, list_directory, write_file.

All paths are resolved relative to ``context.working_dir`` (the project's
``local_repo_path``) when the path is relative and a context is provided.
"""

from __future__ import annotations

from pathlib import Path

from tools.base import BaseTool, ToolCategory, ToolContext, ToolResult


# ── Shared helper ─────────────────────────────────────────────


def _resolve_path(raw_path: str, context: ToolContext | None) -> Path:
    """Resolve *raw_path* relative to the working directory when possible."""
    p = Path(raw_path)
    if not p.is_absolute() and context and context.working_dir:
        p = Path(context.working_dir) / p
    return p.resolve()


# ── read_file ─────────────────────────────────────────────────


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file"
    category = ToolCategory.FILE
    aliases = ["read"]

    def validate_params(self, params: dict) -> str | None:
        if "path" not in params:
            return "Missing required parameter: path"
        return None

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        err = self.validate_params(params)
        if err:
            return ToolResult(success=False, output=None, error=err, tool_name=self.name)

        path = _resolve_path(params["path"], context)

        if not path.exists():
            return ToolResult(
                success=False, output=None,
                error=f"File not found: {path}",
                tool_name=self.name, risk_level="safe",
            )
        if not path.is_file():
            return ToolResult(
                success=False, output=None,
                error=f"Not a file: {path}",
                tool_name=self.name, risk_level="safe",
            )

        try:
            max_size = params.get("max_size", 1_048_576)  # 1 MB
            stat = path.stat()
            if stat.st_size > max_size:
                return ToolResult(
                    success=False, output=None,
                    error=f"File too large: {stat.st_size} bytes (max {max_size})",
                    tool_name=self.name, risk_level="safe",
                )

            content = path.read_text(encoding=params.get("encoding", "utf-8"))
            return ToolResult(
                success=True,
                output={"path": str(path), "content": content, "size": stat.st_size},
                tool_name=self.name, risk_level="safe",
            )
        except Exception as exc:
            return ToolResult(
                success=False, output=None,
                error=f"Read error: {exc}",
                tool_name=self.name, risk_level="safe",
            )


# ── list_directory ────────────────────────────────────────────


class ListDirectoryTool(BaseTool):
    name = "list_directory"
    description = "List contents of a directory"
    category = ToolCategory.FILE
    aliases = ["ls"]

    def validate_params(self, params: dict) -> str | None:
        if "path" not in params:
            return "Missing required parameter: path"
        return None

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        err = self.validate_params(params)
        if err:
            return ToolResult(success=False, output=None, error=err, tool_name=self.name)

        path = _resolve_path(params["path"], context)

        if not path.exists():
            return ToolResult(
                success=False, output=None,
                error=f"Directory not found: {path}",
                tool_name=self.name, risk_level="safe",
            )
        if not path.is_dir():
            return ToolResult(
                success=False, output=None,
                error=f"Not a directory: {path}",
                tool_name=self.name, risk_level="safe",
            )

        try:
            max_entries = params.get("max_entries", 500)
            entries = []
            for i, entry in enumerate(sorted(path.iterdir())):
                if i >= max_entries:
                    break
                entries.append({
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else None,
                })

            return ToolResult(
                success=True,
                output={"path": str(path), "entries": entries, "count": len(entries)},
                tool_name=self.name, risk_level="safe",
            )
        except Exception as exc:
            return ToolResult(
                success=False, output=None,
                error=f"List error: {exc}",
                tool_name=self.name, risk_level="safe",
            )


# ── write_file ────────────────────────────────────────────────


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file (conservative, single file only)"
    category = ToolCategory.FILE
    aliases = ["write", "edit"]

    def validate_params(self, params: dict) -> str | None:
        if "path" not in params:
            return "Missing required parameter: path"
        if "content" not in params:
            return "Missing required parameter: content"
        return None

    async def execute(
        self,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult:
        err = self.validate_params(params)
        if err:
            return ToolResult(success=False, output=None, error=err, tool_name=self.name)

        path = _resolve_path(params["path"], context)
        content = params["content"]

        # Safety: limit content size to 512 KB
        max_content = params.get("max_content_size", 524_288)
        if len(content) > max_content:
            return ToolResult(
                success=False, output=None,
                error=f"Content too large: {len(content)} chars (max {max_content})",
                tool_name=self.name, risk_level="low",
            )

        try:
            create_parents = params.get("create_parents", True)
            if create_parents:
                path.parent.mkdir(parents=True, exist_ok=True)

            existed = path.exists()
            path.write_text(content, encoding=params.get("encoding", "utf-8"))

            return ToolResult(
                success=True,
                output={
                    "path": str(path),
                    "bytes_written": len(content.encode("utf-8")),
                    "created": not existed,
                },
                tool_name=self.name, risk_level="low",
            )
        except Exception as exc:
            return ToolResult(
                success=False, output=None,
                error=f"Write error: {exc}",
                tool_name=self.name, risk_level="low",
            )
