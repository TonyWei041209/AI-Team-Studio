"""Sandboxed, read-only file reader for agent roles (B1).

Gives a role the ability to read the CURRENT on-disk content of a file, scoped
to the project's ``workspace_root``, by REUSING the exact write-sandbox boundary:

- ``agents.execution_eligibility_service._validate_file_path`` —
  rejects absolute paths and ``../`` traversal; enforces realpath containment
  within ``workspace_root``.
- ``agents.scoped_file_executor._is_sensitive_path`` —
  denies ``.git`` / ``.env`` / ``.venv`` / ``node_modules`` / ``dist`` /
  ``build`` / ``__pycache__``.

It ADDS a symlink rejection (mirroring the executor's defense) and a size cap.

This module deliberately does **NOT** use ``tools.file_tools.ReadFileTool``,
which is UNSANDBOXED (accepts absolute paths, allows ``../`` traversal, has no
sensitive deny-list, and no symlink check).

Read-only: no writes, no mutations.  It never raises on a denied/missing path —
it always returns ``(ok, content_or_reason)``.
"""

from __future__ import annotations

import os

# Reuse the write-sandbox boundary functions — IMPORT, never modify.
from agents.execution_eligibility_service import _validate_file_path
from agents.scoped_file_executor import _is_sensitive_path


# Default per-file read cap.  Keeps a single injected file from blowing the
# model's context budget; oversized files are DENIED (not silently truncated)
# here — any cross-file budgeting is the caller's decision.
DEFAULT_MAX_BYTES = 100_000


def read_scoped_file(
    relative_path: str,
    workspace_root: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> tuple[bool, str]:
    """Read a single file, scoped to ``workspace_root`` and the deny-list.

    Returns ``(True, content)`` on success, or ``(False, reason)`` on ANY
    rejection: empty/absolute/traversal path, sensitive path, symlink target,
    resolved-path escape, missing file, non-file, oversized, or non-UTF-8.
    Never raises for these cases.
    """
    # 0. workspace_root must be a real directory.
    if not workspace_root or not str(workspace_root).strip():
        return False, "workspace_root not provided"
    real_workspace = os.path.realpath(workspace_root)
    if not os.path.isdir(real_workspace):
        return False, f"workspace_root is not a directory: {workspace_root}"

    if not isinstance(relative_path, str) or not relative_path.strip():
        return False, "Empty file path"

    # 1. Boundary check — reuse the write-sandbox validator (rejects absolute
    #    paths and ../ traversal; enforces realpath containment in workspace).
    valid, reason = _validate_file_path(relative_path, real_workspace)
    if not valid:
        return False, reason

    # 2. Sensitive-path deny-list — reuse the executor's checker.
    sensitive, s_reason = _is_sensitive_path(relative_path)
    if sensitive:
        return False, s_reason

    # 3. Resolve and reject symlinks (mirror scoped_file_executor's defense).
    full_path = os.path.normpath(os.path.join(real_workspace, relative_path))
    if os.path.islink(full_path):
        return False, f"Target is a symlink: {relative_path}"

    # 4. Defense-in-depth: the resolved real path must still be inside workspace.
    real_full = os.path.realpath(full_path)
    if not real_full.startswith(real_workspace + os.sep):
        return False, f"Resolved path escapes workspace: {relative_path}"

    # 5. Must exist and be a regular file.
    if not os.path.exists(full_path):
        return False, f"File not found: {relative_path}"
    if not os.path.isfile(full_path):
        return False, f"Not a regular file: {relative_path}"

    # 6. Size cap — stat before read; DENY (do not truncate) if oversized.
    try:
        size = os.path.getsize(full_path)
    except OSError as exc:
        return False, f"Cannot stat file: {exc}"
    if size > max_bytes:
        return False, f"File too large: {size} bytes (max {max_bytes})"

    # 7. Read UTF-8 text.
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except UnicodeDecodeError:
        return False, f"File is not valid UTF-8: {relative_path}"
    except OSError as exc:
        return False, f"Read error: {exc}"

    return True, content


def resolve_workspace_root(project_id: str | None) -> str | None:
    """Resolve a project's workspace root (``projects.local_repo_path``).

    Read-only SELECT (mirrors the lookup at routers/projects.py /
    execution_eligibility_service.py).  Returns the path string, or ``None``
    when there is no ``project_id``, no matching project, a null/empty path, or
    any DB error — callers treat ``None`` as "reads unavailable" and proceed on
    text only.  Never raises.
    """
    if not project_id:
        return None
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT local_repo_path FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    path = row["local_repo_path"]
    if not path or not str(path).strip():
        return None
    return path
