"""Approval gating: blocks high-risk tool invocations until human approval.

Usage
-----
    gate = ApprovalGate()
    blocked = gate.check(tool_name, params, context)
    if blocked is not None:
        return blocked          # ToolResult with blocked=True, approval_id
    # safe to proceed with tool.execute()
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from database import get_connection
from models import ApprovalStatus
from tools.base import ToolContext, ToolResult
from tools.safety import RiskClassifier, RiskLevel


class ApprovalGate:
    """Checks risk and creates ApprovalRequest records for high-risk actions."""

    def __init__(self, classifier: RiskClassifier | None = None) -> None:
        self._classifier = classifier or RiskClassifier()

    def check(
        self,
        tool_name: str,
        params: dict,
        context: ToolContext | None = None,
    ) -> ToolResult | None:
        """Check whether *tool_name* + *params* requires approval.

        Returns ``None`` if safe to proceed.
        Returns a ``ToolResult(blocked=True)`` with an ``approval_id``
        if the action was blocked.
        """
        risk_level, reason = self._classifier.classify(tool_name, params)

        if not self._classifier.needs_approval(risk_level):
            return None  # safe — proceed to execution

        # Create an ApprovalRequest and block
        approval_id = self._create_approval_request(
            tool_name=tool_name,
            params=params,
            risk_level=risk_level,
            reason=reason,
            context=context,
        )

        return ToolResult(
            success=False,
            output=None,
            error=f"Action blocked: {reason}. Approval required (id={approval_id}).",
            tool_name=tool_name,
            risk_level=risk_level.value,
            blocked=True,
            approval_id=approval_id,
        )

    def check_pre_approved(self, approval_id: str) -> bool:
        """Return ``True`` if *approval_id* has been approved.

        Used by the ``execute-approved`` endpoint to verify that the
        human reviewer approved the action before retrying.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT status FROM approval_requests WHERE id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return False
            return row["status"] == ApprovalStatus.APPROVED.value
        finally:
            conn.close()

    # ── Internal ──────────────────────────────────────────────

    def _create_approval_request(
        self,
        tool_name: str,
        params: dict,
        risk_level: RiskLevel,
        reason: str,
        context: ToolContext | None,
    ) -> str:
        """INSERT an ``approval_requests`` row and return its id.

        Uses the same SQL pattern as ``routers/approvals.py``.
        """
        approval_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        task_id = context.task_id if context else None
        run_id = context.run_id if context else None

        payload = json.dumps({
            "tool_name": tool_name,
            "params": params,
            "risk_level": risk_level.value,
            "reason": reason,
            "working_dir": context.working_dir if context else "",
            "role": context.role if context else None,
        })

        # action_type prefixed with "tool:" to distinguish from manual requests
        action_type = f"tool:{tool_name}"

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO approval_requests
                   (id, task_id, run_id, action_type, action_payload,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    approval_id, task_id, run_id, action_type, payload,
                    ApprovalStatus.PENDING.value, now,
                ),
            )
            conn.commit()
            return approval_id
        finally:
            conn.close()
