"""Tool execution audit logging (Phase 4B).

Every tool invocation — whether it succeeds, fails, or gets blocked — is
recorded as a ``log_events`` row with ``source='tool_audit'``.

The audit module provides:
- ``log_tool_event()`` — single entry point for writing audit records
- ``_sanitize_params()`` — redact sensitive keys, truncate to 200 chars
- ``_truncate_preview()`` — cap stdout/stderr previews at 500 chars

Audit policy
------------
- ``/tools/execute``: logs request, risk classification, role denial,
  approval blocking, and execution outcome.
- ``/tools/execute-approved``: logs request, denial/consumed status,
  and execution outcome.
- 404 (unknown tool) and 422 (param validation) do **not** produce
  audit records — they are not tool-level events.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database import get_connection

# ── Sensitive-key filter ─────────────────────────────────────

_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "secret", "token", "key",
    "api_key", "apikey", "credential", "auth",
})


def _sanitize_params(params: dict, max_len: int = 200) -> str:
    """Return a JSON string of *params* with sensitive values masked.

    Keys whose lowercase form contains any entry in ``_SENSITIVE_KEYS``
    have their values replaced with ``"***"``.  The output is truncated
    to *max_len* characters.
    """
    sanitized: dict[str, Any] = {}
    for k, v in params.items():
        k_lower = k.lower()
        if any(s in k_lower for s in _SENSITIVE_KEYS):
            sanitized[k] = "***"
        else:
            sanitized[k] = v
    text = json.dumps(sanitized, ensure_ascii=False, default=str)
    if len(text) > max_len:
        return text[:max_len] + "...(truncated)"
    return text


def _truncate_preview(text: str | None, max_len: int = 500) -> str | None:
    """Truncate *text* to *max_len* characters for log previews."""
    if text is None:
        return None
    if len(text) > max_len:
        return text[:max_len] + "...(truncated)"
    return text


# ── Core audit writer ────────────────────────────────────────


def log_tool_event(
    *,
    event: str,
    tool_name: str,
    level: str = "info",
    params: dict | None = None,
    risk_level: str | None = None,
    risk_reason: str | None = None,
    blocked: bool = False,
    approval_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    role: str | None = None,
    success: bool | None = None,
    error_summary: str | None = None,
    stdout_preview: str | None = None,
    stderr_preview: str | None = None,
) -> None:
    """Write a single audit record to ``log_events``.

    Parameters are only included in the payload JSON when they are not
    ``None``, keeping log rows compact.
    """
    payload_dict: dict[str, Any] = {"event": event, "tool_name": tool_name}

    if params is not None:
        payload_dict["params_summary"] = _sanitize_params(params)
    if risk_level is not None:
        payload_dict["risk_level"] = risk_level
    if risk_reason:
        payload_dict["risk_reason"] = risk_reason
    if blocked:
        payload_dict["blocked"] = True
    if approval_id is not None:
        payload_dict["approval_id"] = approval_id
    if project_id is not None:
        payload_dict["project_id"] = project_id
    if role is not None:
        payload_dict["role"] = role
    if success is not None:
        payload_dict["success"] = success
    if error_summary is not None:
        payload_dict["error_summary"] = _truncate_preview(error_summary, 500)
    if stdout_preview is not None:
        payload_dict["stdout_preview"] = _truncate_preview(stdout_preview, 500)
    if stderr_preview is not None:
        payload_dict["stderr_preview"] = _truncate_preview(stderr_preview, 500)

    # Build human-readable message
    message = f"[{event}] tool={tool_name}"
    if risk_level:
        message += f" risk={risk_level}"
    if blocked:
        message += " BLOCKED"
    if success is True:
        message += " OK"
    elif success is False:
        message += " FAIL"

    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload_dict, ensure_ascii=False, default=str)

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO log_events
               (id, task_id, run_id, level, source, message, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, task_id, run_id, level, "tool_audit",
             message, payload_json, now),
        )
        conn.commit()
    finally:
        conn.close()
