"""File system tools: read_file, list_directory, write_file.

read_file / list_directory are SANDBOXED: they route through the verified scoped-file
containment (``agents.scoped_file_reader.read_scoped_file`` / ``list_scoped_dir``), scoped
to ``context.working_dir`` (the project's ``local_repo_path``). They reject absolute paths,
``../`` traversal, sensitive paths (.git/.env/...), symlinks, and out-of-workspace targets,
and FAIL CLOSED when there is no usable workspace root — never an unsandboxed read. This
closes the live POST /api/tools/execute arbitrary-path-read hole AND pre-empts the B3
tool-use hazard. (write_file still uses ``_resolve_path`` — unchanged, out of scope.)
"""

from __future__ import annotations

import os
from pathlib import Path

from agents.scoped_file_reader import list_scoped_dir, read_scoped_file
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

        # SANDBOXED: route through the verified scoped-file containment (rejects absolute /
        # ../ / sensitive / symlink / out-of-workspace paths). FAIL CLOSED when there is no
        # usable workspace root — NEVER fall back to an unsandboxed read.
        workspace_root = context.working_dir if context else None
        if not workspace_root or not os.path.isdir(workspace_root):
            return ToolResult(
                success=False, output=None,
                error="no workspace root configured for sandboxed read",
                tool_name=self.name, risk_level="safe",
            )

        # Preserve the tool's historical 1 MB ceiling by passing it as the read cap.
        ok, result = read_scoped_file(
            params["path"], workspace_root, params.get("max_size", 1_048_576),
        )
        if not ok:
            # result is the rejection reason (containment / sensitive / symlink / missing / size).
            return ToolResult(
                success=False, output=None, error=result,
                tool_name=self.name, risk_level="safe",
            )
        # Use the caller's relative path in the output (avoids leaking absolute host paths).
        return ToolResult(
            success=True,
            output={
                "path": params["path"],
                "content": result,
                "size": len(result.encode("utf-8")),
            },
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

        # SANDBOXED: route through the scoped single-level lister (same containment as the
        # scoped reader; preserves the per-entry {name,type,size} shape). FAIL CLOSED when
        # there is no usable workspace root. "" / "." lists the workspace root itself.
        workspace_root = context.working_dir if context else None
        if not workspace_root or not os.path.isdir(workspace_root):
            return ToolResult(
                success=False, output=None,
                error="no workspace root configured for sandboxed listing",
                tool_name=self.name, risk_level="safe",
            )

        ok, result = list_scoped_dir(
            params["path"], workspace_root, params.get("max_entries", 500),
        )
        if not ok:
            # result is the rejection reason (containment / sensitive / symlink / missing).
            return ToolResult(
                success=False, output=None, error=result,
                tool_name=self.name, risk_level="safe",
            )
        return ToolResult(
            success=True,
            output={"path": params["path"], "entries": result, "count": len(result)},
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
