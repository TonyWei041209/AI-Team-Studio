"""Action normalization & policy gate service.

Compiles snapshot data into a standardized action plan and evaluates
each action against a policy gate.  Pure computation — no side effects,
no DB writes, no file/shell operations.

Usage::

    from agents.action_policy_service import build_action_plan

    plan = build_action_plan(snapshot_data_json)
    # plan == {"actions": [...], "overall_risk": "low", ...}
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tools.safety import RiskClassifier, RiskLevel
from tools.shell_executor import COMMAND_WHITELIST


# ── Enums ────────────────────────────────────────────────────


class ActionType(str, Enum):
    file_create = "file_create"
    file_modify = "file_modify"
    file_delete = "file_delete"
    command_run = "command_run"
    git_commit = "git_commit"
    git_checkout = "git_checkout"
    unsupported = "unsupported"


class PolicyDecision(str, Enum):
    allow = "allow"
    deny = "deny"
    needs_confirmation = "needs_confirmation"


# ── Data classes ─────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedAction:
    type: ActionType
    target: str
    params: dict = field(default_factory=dict)
    risk_level: str = "safe"
    policy_decision: str = "allow"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "target": self.target,
            "params": self.params,
            "risk_level": self.risk_level,
            "policy_decision": self.policy_decision,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ActionPlan:
    actions: tuple[NormalizedAction, ...] = ()
    overall_risk: str = "safe"
    has_denied: bool = False
    needs_confirmation_count: int = 0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "overall_risk": self.overall_risk,
            "has_denied": self.has_denied,
            "needs_confirmation_count": self.needs_confirmation_count,
            "summary": self.summary,
        }


# ── Operation → ActionType mapping ──────────────────────────

_FILE_OP_MAP: dict[str, ActionType] = {
    "create": ActionType.file_create,
    "modify": ActionType.file_modify,
    "update": ActionType.file_modify,
    "edit": ActionType.file_modify,
    "delete": ActionType.file_delete,
    "remove": ActionType.file_delete,
}

_GIT_COMMIT_RE = re.compile(r"^\s*git\s+commit\b", re.IGNORECASE)
_GIT_CHECKOUT_RE = re.compile(r"^\s*git\s+(checkout|switch)\b", re.IGNORECASE)

# Shared classifier instance (stateless, safe to reuse)
_classifier = RiskClassifier()

_RISK_RANK = {"safe": 0, "low": 1, "high": 2, "critical": 3}


# ── Action Compiler ──────────────────────────────────────────


def compile_actions(snapshot_data: dict) -> list[NormalizedAction]:
    """Extract and normalize actions from parsed snapshot_data dict.

    Returns a list of NormalizedAction with type and target set,
    but risk_level / policy still at defaults (to be filled by
    ``evaluate_policy``).
    """
    actions: list[NormalizedAction] = []

    # ── File actions ──
    for f in snapshot_data.get("proposed_files", []):
        path = f.get("path", "") if isinstance(f, dict) else str(f)
        operation = f.get("operation", "") if isinstance(f, dict) else ""
        action_type = _FILE_OP_MAP.get(operation.lower(), ActionType.unsupported)
        file_params = {"operation": operation} if operation else {}
        actions.append(NormalizedAction(type=action_type, target=path, params=file_params))

    # ── Command actions ──
    for c in snapshot_data.get("proposed_commands", []):
        cmd = c if isinstance(c, str) else c.get("command", str(c))
        cmd = cmd.strip()
        if not cmd:
            continue
        if _GIT_COMMIT_RE.search(cmd):
            action_type = ActionType.git_commit
        elif _GIT_CHECKOUT_RE.search(cmd):
            action_type = ActionType.git_checkout
        else:
            action_type = ActionType.command_run
        cmd_params: dict = {}
        if isinstance(c, dict):
            cmd_params = {k: v for k, v in c.items() if k != "command"}
        actions.append(NormalizedAction(type=action_type, target=cmd, params=cmd_params))

    return actions


# ── Policy Gate ──────────────────────────────────────────────


def _extract_base_command(cmd: str) -> str:
    """Extract the base command name from a full command string."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    return parts[0] if parts else ""


def _evaluate_file_action(action: NormalizedAction) -> NormalizedAction:
    """Evaluate policy for a file action."""
    risk, reason = _classifier.classify("write_file", {"path": action.target})
    p = action.params

    if action.type == ActionType.file_delete:
        # Deletions always need at least confirmation
        if _RISK_RANK[risk.value] >= _RISK_RANK["high"]:
            return NormalizedAction(
                type=action.type, target=action.target, params=p,
                risk_level=risk.value,
                policy_decision=PolicyDecision.deny.value,
                reason=reason or "File deletion in sensitive location",
            )
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value if risk != RiskLevel.SAFE else "low",
            policy_decision=PolicyDecision.needs_confirmation.value,
            reason="File deletion requires confirmation",
        )

    # file_create / file_modify
    if risk == RiskLevel.CRITICAL:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.deny.value,
            reason=reason,
        )
    if risk == RiskLevel.HIGH:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.needs_confirmation.value,
            reason=reason,
        )
    # SAFE or LOW
    return NormalizedAction(
        type=action.type, target=action.target, params=p,
        risk_level=risk.value,
        policy_decision=PolicyDecision.allow.value,
        reason="Non-sensitive file operation",
    )


def _evaluate_command_action(action: NormalizedAction) -> NormalizedAction:
    """Evaluate policy for a command_run action."""
    risk, reason = _classifier.classify("shell", {"command": action.target})
    p = action.params

    if risk == RiskLevel.CRITICAL:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.deny.value,
            reason=reason,
        )
    if risk == RiskLevel.HIGH:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.needs_confirmation.value,
            reason=reason,
        )

    # SAFE or LOW — check whitelist
    base_cmd = _extract_base_command(action.target)
    if base_cmd in COMMAND_WHITELIST:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.allow.value,
            reason="Whitelisted command",
        )
    # Not whitelisted
    return NormalizedAction(
        type=action.type, target=action.target, params=p,
        risk_level="low" if risk == RiskLevel.SAFE else risk.value,
        policy_decision=PolicyDecision.needs_confirmation.value,
        reason=f"Command '{base_cmd}' not in whitelist",
    )


def _evaluate_git_action(action: NormalizedAction) -> NormalizedAction:
    """Evaluate policy for git_commit / git_checkout actions."""
    risk, reason = _classifier.classify("shell", {"command": action.target})
    p = action.params

    if risk == RiskLevel.CRITICAL:
        return NormalizedAction(
            type=action.type, target=action.target, params=p,
            risk_level=risk.value,
            policy_decision=PolicyDecision.deny.value,
            reason=reason,
        )
    # All non-critical git writes need confirmation
    return NormalizedAction(
        type=action.type, target=action.target, params=p,
        risk_level=risk.value if risk != RiskLevel.SAFE else "low",
        policy_decision=PolicyDecision.needs_confirmation.value,
        reason="Git write operation requires confirmation",
    )


def _evaluate_unsupported(action: NormalizedAction) -> NormalizedAction:
    """Unsupported actions are always denied."""
    return NormalizedAction(
        type=action.type, target=action.target, params=action.params,
        risk_level="high",
        policy_decision=PolicyDecision.deny.value,
        reason="Unsupported action type",
    )


_EVALUATORS = {
    ActionType.file_create: _evaluate_file_action,
    ActionType.file_modify: _evaluate_file_action,
    ActionType.file_delete: _evaluate_file_action,
    ActionType.command_run: _evaluate_command_action,
    ActionType.git_commit: _evaluate_git_action,
    ActionType.git_checkout: _evaluate_git_action,
    ActionType.unsupported: _evaluate_unsupported,
}


def evaluate_policy(actions: list[NormalizedAction]) -> ActionPlan:
    """Evaluate policy for each action and produce an ActionPlan."""
    evaluated: list[NormalizedAction] = []
    for a in actions:
        evaluator = _EVALUATORS.get(a.type, _evaluate_unsupported)
        evaluated.append(evaluator(a))

    # ── Aggregate ──
    allow_count = sum(1 for a in evaluated if a.policy_decision == "allow")
    confirm_count = sum(1 for a in evaluated if a.policy_decision == "needs_confirmation")
    deny_count = sum(1 for a in evaluated if a.policy_decision == "deny")

    max_risk = "safe"
    for a in evaluated:
        if _RISK_RANK.get(a.risk_level, 0) > _RISK_RANK.get(max_risk, 0):
            max_risk = a.risk_level

    total = len(evaluated)
    summary = (
        f"{total} action(s): {allow_count} allowed, "
        f"{confirm_count} need confirmation, {deny_count} denied"
    )

    return ActionPlan(
        actions=tuple(evaluated),
        overall_risk=max_risk,
        has_denied=deny_count > 0,
        needs_confirmation_count=confirm_count,
        summary=summary,
    )


# ── Public entry point ───────────────────────────────────────


def build_action_plan(snapshot_data_json: str) -> dict:
    """Build a standardized action plan from a snapshot_data JSON string.

    Parameters
    ----------
    snapshot_data_json : str
        Raw JSON string from ``execution_snapshots.snapshot_data``.

    Returns
    -------
    dict
        Serialized ``ActionPlan`` with all actions and policy decisions.
    """
    data = json.loads(snapshot_data_json)
    raw_actions = compile_actions(data)
    plan = evaluate_policy(raw_actions)
    return plan.to_dict()
