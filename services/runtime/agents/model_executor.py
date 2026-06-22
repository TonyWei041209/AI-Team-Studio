"""Model-backed agent executor (Phase 6B + 6C + 6D).

Calls real LLM providers via the ProviderRegistry for agent execution.
Currently supports: Planner, Builder (plan-only), and Reviewer roles.

Phase 6C: Provider/model resolution is config-driven via role_model_settings DB table.
Phase 6D: Builder support added in plan-only mode (structured change plan, no tool execution).
"""

from __future__ import annotations

import json
import re
from typing import Any

from models import AgentRole, ReviewDecision
from agents.definitions import get_definition
from agents.skill_loader import build_enhanced_system_prompt
from agents.executor import ExecutionResult
from providers.base import CompletionRequest, Message, MessageRole
from providers.registry import get_registry, ProviderRegistry


# ── Planner output schema validation ──────────────────────────


class PlannerOutputSchema:
    """Validates Planner JSON output against the required schema."""

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Planner output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # goal_summary: required, non-empty string
        gs = data.get("goal_summary")
        if not isinstance(gs, str) or not gs.strip():
            return False, "goal_summary must be a non-empty string"

        # task_breakdown: required, list with >= 1 item
        tb = data.get("task_breakdown")
        if not isinstance(tb, list) or len(tb) == 0:
            return False, "task_breakdown must be a non-empty list"
        for i, item in enumerate(tb):
            if not isinstance(item, dict):
                return False, f"task_breakdown[{i}] must be an object"
            if "step" not in item or not isinstance(item["step"], (int, float)):
                return False, f"task_breakdown[{i}].step must be an integer"
            if "description" not in item or not isinstance(item["description"], str):
                return False, f"task_breakdown[{i}].description must be a string"
            if "role" not in item or not isinstance(item["role"], str):
                return False, f"task_breakdown[{i}].role must be a string"

        # acceptance_criteria: required, list with >= 1 string item
        ac = data.get("acceptance_criteria")
        if not isinstance(ac, list) or len(ac) == 0:
            return False, "acceptance_criteria must be a non-empty list"
        for i, item in enumerate(ac):
            if not isinstance(item, str):
                return False, f"acceptance_criteria[{i}] must be a string"

        # risks: optional, list of strings (can be empty)
        risks = data.get("risks")
        if risks is None:
            pass  # tolerate missing — treat as empty
        elif not isinstance(risks, list):
            return False, "risks must be a list"
        else:
            for i, item in enumerate(risks):
                if not isinstance(item, str):
                    return False, f"risks[{i}] must be a string"

        # dependencies: optional, list of strings (can be empty)
        deps = data.get("dependencies")
        if deps is None:
            pass  # tolerate missing — treat as empty
        elif not isinstance(deps, list):
            return False, "dependencies must be a list"
        else:
            for i, item in enumerate(deps):
                if not isinstance(item, str):
                    return False, f"dependencies[{i}] must be a string"

        return True, ""


# ── Reviewer output schema validation ─────────────────────────

_VALID_DECISIONS = {"approve", "request_changes"}
_VALID_SEVERITIES = {"critical", "major", "minor", "nitpick"}
_VALID_CONFIDENCES = {"high", "medium", "low"}


class ReviewerOutputSchema:
    """Validates Reviewer JSON output against the required schema."""

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Reviewer output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # decision: required, must be "approve" or "request_changes"
        decision = data.get("decision")
        if not isinstance(decision, str) or decision not in _VALID_DECISIONS:
            return False, (
                f"decision must be one of {sorted(_VALID_DECISIONS)}, "
                f"got: {decision!r}"
            )

        # reason: required, non-empty string
        reason = data.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, "reason must be a non-empty string"

        # issues_found: required, list (can be empty)
        issues = data.get("issues_found")
        if not isinstance(issues, list):
            return False, "issues_found must be a list"
        for i, item in enumerate(issues):
            if not isinstance(item, dict):
                return False, f"issues_found[{i}] must be an object"
            sev = item.get("severity")
            if not isinstance(sev, str) or sev not in _VALID_SEVERITIES:
                return False, (
                    f"issues_found[{i}].severity must be one of "
                    f"{sorted(_VALID_SEVERITIES)}, got: {sev!r}"
                )
            desc = item.get("description")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"issues_found[{i}].description must be a non-empty string"

        # confidence: required, must be "high", "medium", or "low"
        confidence = data.get("confidence")
        if not isinstance(confidence, str) or confidence not in _VALID_CONFIDENCES:
            return False, (
                f"confidence must be one of {sorted(_VALID_CONFIDENCES)}, "
                f"got: {confidence!r}"
            )

        return True, ""


# ── Builder output schema validation (Phase 6D) ──────────────

_VALID_FILE_ACTIONS = {"create", "modify", "delete"}
_VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
_VALID_DOC_ACTIONS = {"create", "modify"}  # documentation never deletes files
_VALID_ACTION_TYPES = {"file", "shell", "git"}


class BuilderOutputSchema:
    """Validates Builder JSON output against the execution proposal schema.

    Phase 6D core fields are required.  Phase 6E-A execution proposal fields
    are optional (validated only when present) for backward compatibility.
    """

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Builder plan-only output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # change_summary: required, non-empty string
        cs = data.get("change_summary")
        if not isinstance(cs, str) or not cs.strip():
            return False, "change_summary must be a non-empty string"

        # proposed_files: required, list with >= 1 item
        pf = data.get("proposed_files")
        if not isinstance(pf, list) or len(pf) == 0:
            return False, "proposed_files must be a non-empty list"
        for i, item in enumerate(pf):
            if not isinstance(item, dict):
                return False, f"proposed_files[{i}] must be an object"
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                return False, f"proposed_files[{i}].path must be a non-empty string"
            action = item.get("action")
            if not isinstance(action, str) or action not in _VALID_FILE_ACTIONS:
                return False, (
                    f"proposed_files[{i}].action must be one of "
                    f"{sorted(_VALID_FILE_ACTIONS)}, got: {action!r}"
                )
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                return False, f"proposed_files[{i}].reason must be a non-empty string"

        # change_steps: required, list with >= 1 item
        cs_list = data.get("change_steps")
        if not isinstance(cs_list, list) or len(cs_list) == 0:
            return False, "change_steps must be a non-empty list"
        for i, item in enumerate(cs_list):
            if not isinstance(item, dict):
                return False, f"change_steps[{i}] must be an object"
            if "step" not in item or not isinstance(item["step"], (int, float)):
                return False, f"change_steps[{i}].step must be an integer"
            if "description" not in item or not isinstance(item["description"], str):
                return False, f"change_steps[{i}].description must be a string"
            # target_file is optional

        # reasoning_summary: required, non-empty string
        rs = data.get("reasoning_summary")
        if not isinstance(rs, str) or not rs.strip():
            return False, "reasoning_summary must be a non-empty string"

        # validation_plan: required, list with >= 1 string item
        vp = data.get("validation_plan")
        if not isinstance(vp, list) or len(vp) == 0:
            return False, "validation_plan must be a non-empty list"
        for i, item in enumerate(vp):
            if not isinstance(item, str):
                return False, f"validation_plan[{i}] must be a string"

        # risk_notes: optional, list of strings (can be empty)
        rn = data.get("risk_notes")
        if rn is None:
            pass  # tolerate missing — treat as empty
        elif not isinstance(rn, list):
            return False, "risk_notes must be a list"
        else:
            for i, item in enumerate(rn):
                if not isinstance(item, str):
                    return False, f"risk_notes[{i}] must be a string"

        # ── Phase 6E-A optional execution proposal fields ──────────

        # proposed_commands: optional, list of command objects
        pc = data.get("proposed_commands")
        if pc is not None:
            if not isinstance(pc, list):
                return False, "proposed_commands must be a list"
            for i, item in enumerate(pc):
                if not isinstance(item, dict):
                    return False, f"proposed_commands[{i}] must be an object"
                cmd = item.get("command")
                if not isinstance(cmd, str) or not cmd.strip():
                    return False, f"proposed_commands[{i}].command must be a non-empty string"
                reason = item.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    return False, f"proposed_commands[{i}].reason must be a non-empty string"
                rl = item.get("risk_level")
                if rl is not None and (not isinstance(rl, str) or rl not in _VALID_RISK_LEVELS):
                    return False, (
                        f"proposed_commands[{i}].risk_level must be one of "
                        f"{sorted(_VALID_RISK_LEVELS)}, got: {rl!r}"
                    )

        # execution_steps: optional, list of step objects
        es = data.get("execution_steps")
        if es is not None:
            if not isinstance(es, list):
                return False, "execution_steps must be a list"
            for i, item in enumerate(es):
                if not isinstance(item, dict):
                    return False, f"execution_steps[{i}] must be an object"
                sn = item.get("step_number")
                if not isinstance(sn, (int, float)):
                    return False, f"execution_steps[{i}].step_number must be an integer"
                at = item.get("action_type")
                if not isinstance(at, str) or at not in _VALID_ACTION_TYPES:
                    return False, (
                        f"execution_steps[{i}].action_type must be one of "
                        f"{sorted(_VALID_ACTION_TYPES)}, got: {at!r}"
                    )
                tgt = item.get("target")
                if not isinstance(tgt, str) or not tgt.strip():
                    return False, f"execution_steps[{i}].target must be a non-empty string"
                desc = item.get("description")
                if not isinstance(desc, str) or not desc.strip():
                    return False, f"execution_steps[{i}].description must be a non-empty string"
                rl = item.get("risk_level")
                if rl is not None and (not isinstance(rl, str) or rl not in _VALID_RISK_LEVELS):
                    return False, (
                        f"execution_steps[{i}].risk_level must be one of "
                        f"{sorted(_VALID_RISK_LEVELS)}, got: {rl!r}"
                    )

        # risk_level: optional top-level risk
        top_risk = data.get("risk_level")
        if top_risk is not None and (not isinstance(top_risk, str) or top_risk not in _VALID_RISK_LEVELS):
            return False, (
                f"risk_level must be one of {sorted(_VALID_RISK_LEVELS)}, "
                f"got: {top_risk!r}"
            )

        # requires_approval: optional bool
        ra = data.get("requires_approval")
        if ra is not None and not isinstance(ra, bool):
            return False, "requires_approval must be a boolean"

        # approval_reasons: optional list of strings
        ar = data.get("approval_reasons")
        if ar is not None:
            if not isinstance(ar, list):
                return False, "approval_reasons must be a list"
            for i, item in enumerate(ar):
                if not isinstance(item, str):
                    return False, f"approval_reasons[{i}] must be a string"

        # estimated_impact: optional dict
        ei = data.get("estimated_impact")
        if ei is not None:
            if not isinstance(ei, dict):
                return False, "estimated_impact must be an object"
            fa = ei.get("files_affected")
            if fa is not None and not isinstance(fa, (int, float)):
                return False, "estimated_impact.files_affected must be an integer"
            cc = ei.get("commands_count")
            if cc is not None and not isinstance(cc, (int, float)):
                return False, "estimated_impact.commands_count must be an integer"
            rs_val = ei.get("risk_summary")
            if rs_val is not None and not isinstance(rs_val, str):
                return False, "estimated_impact.risk_summary must be a string"

        return True, ""


# ── QA output schema validation (static review) ──────────────

_VALID_QA_RESULTS = {"pass", "concerns", "fail"}
_VALID_QA_SEVERITIES = {"critical", "major", "minor", "info"}


class QaOutputSchema:
    """Validates QA JSON output against the static-review schema.

    QA performs static review of the Builder's proposal (no execution), so the
    schema captures findings, a per-criterion assessment, and an overall verdict.
    """

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the QA static-review output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # validation_scope: required, non-empty string
        vs = data.get("validation_scope")
        if not isinstance(vs, str) or not vs.strip():
            return False, "validation_scope must be a non-empty string"

        # review_findings: required, list (can be empty)
        findings = data.get("review_findings")
        if not isinstance(findings, list):
            return False, "review_findings must be a list"
        for i, item in enumerate(findings):
            if not isinstance(item, dict):
                return False, f"review_findings[{i}] must be an object"
            sev = item.get("severity")
            if not isinstance(sev, str) or sev not in _VALID_QA_SEVERITIES:
                return False, (
                    f"review_findings[{i}].severity must be one of "
                    f"{sorted(_VALID_QA_SEVERITIES)}, got: {sev!r}"
                )
            desc = item.get("description")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"review_findings[{i}].description must be a non-empty string"

        # acceptance_criteria_assessment: required, list (can be empty)
        assessment = data.get("acceptance_criteria_assessment")
        if not isinstance(assessment, list):
            return False, "acceptance_criteria_assessment must be a list"
        for i, item in enumerate(assessment):
            if not isinstance(item, dict):
                return False, f"acceptance_criteria_assessment[{i}] must be an object"
            criterion = item.get("criterion")
            if not isinstance(criterion, str) or not criterion.strip():
                return False, f"acceptance_criteria_assessment[{i}].criterion must be a non-empty string"
            met = item.get("met")
            if not isinstance(met, bool):
                return False, f"acceptance_criteria_assessment[{i}].met must be a boolean"
            rationale = item.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                return False, f"acceptance_criteria_assessment[{i}].rationale must be a non-empty string"

        # result: required, must be "pass", "concerns", or "fail"
        result = data.get("result")
        if not isinstance(result, str) or result not in _VALID_QA_RESULTS:
            return False, (
                f"result must be one of {sorted(_VALID_QA_RESULTS)}, "
                f"got: {result!r}"
            )

        # summary: required, non-empty string
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return False, "summary must be a non-empty string"

        return True, ""


# ── Security Reviewer output schema validation (static security review) ──

_VALID_SEC_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_VALID_SEC_RISK = {"none", "low", "medium", "high", "critical"}
_VALID_SEC_VERDICTS = {"pass", "concerns", "fail"}


class SecurityReviewerOutputSchema:
    """Validates Security Reviewer JSON output against the static security-review schema.

    The Security Reviewer statically reviews the Builder's proposal for SEMANTIC
    security risks (no execution), complementing the rule-based RiskClassifier.
    """

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Security Reviewer output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # review_scope: required, non-empty string
        rs = data.get("review_scope")
        if not isinstance(rs, str) or not rs.strip():
            return False, "review_scope must be a non-empty string"

        # findings: required, list (can be empty)
        findings = data.get("findings")
        if not isinstance(findings, list):
            return False, "findings must be a list"
        for i, item in enumerate(findings):
            if not isinstance(item, dict):
                return False, f"findings[{i}] must be an object"
            sev = item.get("severity")
            if not isinstance(sev, str) or sev not in _VALID_SEC_SEVERITIES:
                return False, (
                    f"findings[{i}].severity must be one of "
                    f"{sorted(_VALID_SEC_SEVERITIES)}, got: {sev!r}"
                )
            category = item.get("category")
            if not isinstance(category, str) or not category.strip():
                return False, f"findings[{i}].category must be a non-empty string"
            desc = item.get("description")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"findings[{i}].description must be a non-empty string"

        # overall_risk: required, must be in _VALID_SEC_RISK
        overall_risk = data.get("overall_risk")
        if not isinstance(overall_risk, str) or overall_risk not in _VALID_SEC_RISK:
            return False, (
                f"overall_risk must be one of {sorted(_VALID_SEC_RISK)}, "
                f"got: {overall_risk!r}"
            )

        # verdict: required, must be in _VALID_SEC_VERDICTS
        verdict = data.get("verdict")
        if not isinstance(verdict, str) or verdict not in _VALID_SEC_VERDICTS:
            return False, (
                f"verdict must be one of {sorted(_VALID_SEC_VERDICTS)}, "
                f"got: {verdict!r}"
            )

        # summary: required, non-empty string
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return False, "summary must be a non-empty string"

        return True, ""


# ── Architect output schema validation (technical design) ──

# No enumerated fields in the Architect schema -> no _VALID_* constant sets needed.

class ArchitectOutputSchema:
    """Validates Architect JSON output against the technical-design schema.

    The Architect turns the Planner's plan into a technical design for the Builder
    (component boundaries, interfaces, key decisions, tradeoffs) — no code/files.
    """

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Architect output schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # design_summary: required, non-empty string
        ds = data.get("design_summary")
        if not isinstance(ds, str) or not ds.strip():
            return False, "design_summary must be a non-empty string"

        # components: required, list (can be empty)
        components = data.get("components")
        if not isinstance(components, list):
            return False, "components must be a list"
        for i, item in enumerate(components):
            if not isinstance(item, dict):
                return False, f"components[{i}] must be an object"
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                return False, f"components[{i}].name must be a non-empty string"
            resp = item.get("responsibility")
            if not isinstance(resp, str) or not resp.strip():
                return False, f"components[{i}].responsibility must be a non-empty string"
            interfaces = item.get("interfaces")  # optional
            if interfaces is not None and not isinstance(interfaces, str):
                return False, f"components[{i}].interfaces must be a string"

        # key_decisions: required, list (can be empty)
        decisions = data.get("key_decisions")
        if not isinstance(decisions, list):
            return False, "key_decisions must be a list"
        for i, item in enumerate(decisions):
            if not isinstance(item, dict):
                return False, f"key_decisions[{i}] must be an object"
            decision = item.get("decision")
            if not isinstance(decision, str) or not decision.strip():
                return False, f"key_decisions[{i}].decision must be a non-empty string"
            rationale = item.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                return False, f"key_decisions[{i}].rationale must be a non-empty string"
            alternatives = item.get("alternatives")  # optional
            if alternatives is not None and not isinstance(alternatives, str):
                return False, f"key_decisions[{i}].alternatives must be a string"

        # interfaces_or_contracts: required, list of non-empty strings (can be empty)
        contracts = data.get("interfaces_or_contracts")
        if not isinstance(contracts, list):
            return False, "interfaces_or_contracts must be a list"
        for i, item in enumerate(contracts):
            if not isinstance(item, str) or not item.strip():
                return False, f"interfaces_or_contracts[{i}] must be a non-empty string"

        # risks_tradeoffs: required, list of non-empty strings (can be empty)
        risks = data.get("risks_tradeoffs")
        if not isinstance(risks, list):
            return False, "risks_tradeoffs must be a list"
        for i, item in enumerate(risks):
            if not isinstance(item, str) or not item.strip():
                return False, f"risks_tradeoffs[{i}] must be a non-empty string"

        # summary: required, non-empty string
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return False, "summary must be a non-empty string"

        return True, ""


class DocumentationOutputSchema:
    """Validates Documentation JSON output — a Builder-COMPATIBLE doc proposal.

    The Documentation role is a "second Builder": it proposes documentation FILE
    changes (proposed_files with full content) that flow through the same execution
    chain. The shape mirrors the Builder's proposed_files so it is chain-compatible,
    with two deliberate tightenings over BuilderOutputSchema:
      - ``content`` is REQUIRED and validated non-empty (the scoped file executor needs
        file content to write create/modify actions; BuilderOutputSchema does NOT
        validate content, which can let empty-content proposals reach the executor).
      - ``action`` is restricted to create/modify (_VALID_DOC_ACTIONS) — docs never delete.
    """

    @staticmethod
    def validate(data: Any) -> tuple[bool, str]:
        """Check *data* conforms to the Documentation proposal schema.

        Returns ``(True, "")`` on success or ``(False, "<reason>")`` on failure.
        """
        if not isinstance(data, dict):
            return False, "Output must be a JSON object"

        # change_summary: required, non-empty string
        cs = data.get("change_summary")
        if not isinstance(cs, str) or not cs.strip():
            return False, "change_summary must be a non-empty string"

        # proposed_files: required, list with >= 1 item
        pf = data.get("proposed_files")
        if not isinstance(pf, list) or len(pf) == 0:
            return False, "proposed_files must be a non-empty list"
        for i, item in enumerate(pf):
            if not isinstance(item, dict):
                return False, f"proposed_files[{i}] must be an object"
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                return False, f"proposed_files[{i}].path must be a non-empty string"
            action = item.get("action")
            if not isinstance(action, str) or action not in _VALID_DOC_ACTIONS:
                return False, (
                    f"proposed_files[{i}].action must be one of "
                    f"{sorted(_VALID_DOC_ACTIONS)}, got: {action!r}"
                )
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                return False, f"proposed_files[{i}].reason must be a non-empty string"
            # content: REQUIRED, non-empty (the improvement over BuilderOutputSchema —
            # the scoped file executor needs content to write create/modify files).
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                return False, f"proposed_files[{i}].content must be a non-empty string"

        # change_steps: required, list with >= 1 item
        cs_list = data.get("change_steps")
        if not isinstance(cs_list, list) or len(cs_list) == 0:
            return False, "change_steps must be a non-empty list"
        for i, item in enumerate(cs_list):
            if not isinstance(item, dict):
                return False, f"change_steps[{i}] must be an object"
            if "step" not in item or not isinstance(item["step"], (int, float)):
                return False, f"change_steps[{i}].step must be an integer"
            desc = item.get("description")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"change_steps[{i}].description must be a non-empty string"

        # reasoning_summary: required, non-empty string
        rs = data.get("reasoning_summary")
        if not isinstance(rs, str) or not rs.strip():
            return False, "reasoning_summary must be a non-empty string"

        # validation_plan: required, list with >= 1 non-empty string item
        vp = data.get("validation_plan")
        if not isinstance(vp, list) or len(vp) == 0:
            return False, "validation_plan must be a non-empty list"
        for i, item in enumerate(vp):
            if not isinstance(item, str) or not item.strip():
                return False, f"validation_plan[{i}] must be a non-empty string"

        return True, ""


# ── Code-fence stripping ──────────────────────────────────────

_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL,
)

# Fallback: opening fence without closing (truncated response)
_FENCE_OPEN_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(.+)",
    re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output.

    Handles both ````` ```json ... ``` ````` and ````` ``` ... ``` `````.
    Also handles truncated responses where the closing fence is missing.
    """
    stripped = text.strip()
    # Try full fence first
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip()
    # Fallback: opening fence only (truncated by max_tokens)
    m = _FENCE_OPEN_RE.match(stripped)
    if m:
        return m.group(1).strip()
    return stripped


# ── Model Agent Executor ──────────────────────────────────────


def _load_role_model_config(role: AgentRole) -> dict | None:
    """Load role-model config from database.  Returns None if no row found."""
    try:
        from database import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT provider, model, enabled FROM role_model_settings WHERE role = ?",
                (role.value,),
            ).fetchone()
            if row:
                return {
                    "provider": row["provider"],
                    "model": row["model"],
                    "enabled": bool(row["enabled"]),
                }
            return None
        finally:
            conn.close()
    except Exception:
        return None


def _normalize_builder_proposal(data: dict) -> None:
    """Fill in missing Phase 6E-A execution proposal fields with computed defaults.

    Mutates *data* in-place.  Called after schema validation passes.
    """
    data.setdefault("proposed_commands", [])
    data.setdefault("execution_steps", [])

    # Auto-compute risk_level if not provided
    if "risk_level" not in data:
        max_risk = "low"
        # File deletions → high
        for f in data.get("proposed_files", []):
            if f.get("action") == "delete":
                max_risk = "high"
                break
        # Check proposed commands for dangerous patterns
        for cmd_item in data.get("proposed_commands", []):
            cmd_risk = cmd_item.get("risk_level", "low")
            if cmd_risk in ("high", "critical"):
                if _risk_rank(cmd_risk) > _risk_rank(max_risk):
                    max_risk = cmd_risk
        # At least medium if there are file modifications
        if max_risk == "low" and data.get("proposed_files"):
            max_risk = "medium"
        data["risk_level"] = max_risk

    # Auto-compute requires_approval
    if "requires_approval" not in data:
        data["requires_approval"] = data["risk_level"] in ("high", "critical")

    # Auto-compute approval_reasons
    if "approval_reasons" not in data:
        reasons: list[str] = []
        if data["risk_level"] in ("high", "critical"):
            reasons.append(f"Overall risk level: {data['risk_level']}")
        for f in data.get("proposed_files", []):
            if f.get("action") == "delete":
                reasons.append(f"File deletion: {f.get('path', '?')}")
        data["approval_reasons"] = reasons

    # Auto-compute estimated_impact
    if "estimated_impact" not in data:
        data["estimated_impact"] = {
            "files_affected": len(data.get("proposed_files", [])),
            "commands_count": len(data.get("proposed_commands", [])),
            "risk_summary": f"Risk: {data['risk_level']}, "
                            f"{len(data.get('proposed_files', []))} files, "
                            f"{len(data.get('proposed_commands', []))} commands",
        }


def _risk_rank(level: str) -> int:
    """Return numeric rank for a risk level string."""
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(level, 0)


class ModelAgentExecutor:
    """Executes agent roles using real LLM provider calls.

    Supports the **Planner**, **Builder** (plan-only), **QA** (static review),
    and **Reviewer** roles.

    Phase 6C: Provider/model are resolved from role_model_settings DB table.
    Phase 6D: Builder added in plan-only mode (outputs change plan, no tool execution).
    Phase 6E-A: Builder output normalized with execution proposal fields.
    QA-Real: QA added as a real static-review role (informs the Reviewer, never vetoes).
    """

    _SUPPORTED_ROLES = {AgentRole.PLANNER, AgentRole.ARCHITECT, AgentRole.BUILDER, AgentRole.QA, AgentRole.SECURITY_REVIEWER, AgentRole.REVIEWER, AgentRole.DOCUMENTATION}

    def __init__(self, registry: ProviderRegistry | None = None):
        self._registry = registry or get_registry()

    async def execute(
        self,
        role: AgentRole,
        task_context: dict,
    ) -> ExecutionResult:
        """Execute a single agent role via a real model call."""
        if role == AgentRole.PLANNER:
            return await self._execute_planner(task_context)
        if role == AgentRole.ARCHITECT:
            return await self._execute_architect(task_context, role)
        if role == AgentRole.BUILDER:
            return await self._execute_builder(task_context)
        if role == AgentRole.QA:
            return await self._execute_qa(task_context)
        if role == AgentRole.SECURITY_REVIEWER:
            return await self._execute_security_reviewer(task_context, role)
        if role == AgentRole.REVIEWER:
            return await self._execute_reviewer(task_context)
        if role == AgentRole.DOCUMENTATION:
            return await self._execute_documentation(task_context, role)
        raise NotImplementedError(
            f"ModelAgentExecutor does not yet support role: {role.value}. "
            f"Use MockAgentExecutor for {role.value}."
        )

    # ── Shared helpers ─────────────────────────────────────────

    def _resolve_provider(self, role: AgentRole):
        """Look up the provider/model for a role from role_model_settings DB.

        The role_model_settings table is the **sole runtime truth source** for
        model routing (Phase 6C).  definitions.py only provides system_prompt
        and role metadata.

        Raises ValueError if:
        - No DB config found for the role
        - Role is not enabled for real model calls
        - Provider is not registered
        - Provider has no API key configured
        - Model name is empty
        """
        defn = get_definition(role)  # for system_prompt and metadata only

        db_cfg = _load_role_model_config(role)
        if not db_cfg:
            raise ValueError(
                f"No model configuration found for role '{role.value}'. "
                f"Configure it in Settings → Role Model Configuration."
            )
        if not db_cfg["enabled"]:
            raise ValueError(
                f"Role '{role.value}' is not enabled for real model calls. "
                f"Enable it in Settings → Role Model Configuration."
            )

        provider_name = db_cfg["provider"]
        model_name = db_cfg["model"]

        if not provider_name or provider_name == "mock":
            raise ValueError(
                f"Role '{role.value}' is enabled but provider is '{provider_name}'. "
                f"Set a valid provider in Settings."
            )
        if not model_name:
            raise ValueError(
                f"Role '{role.value}' is enabled but model is empty. "
                f"Set a model name in Settings → Role Model Configuration."
            )

        provider = self._registry.get(provider_name)
        if provider is None:
            raise ValueError(
                f"Provider '{provider_name}' not registered for role '{role.value}'. "
                f"Check provider configuration in Settings."
            )
        if not getattr(provider, "api_key", ""):
            raise ValueError(
                f"Provider '{provider_name}' has no API key configured for role '{role.value}'. "
                f"Set the API key in Settings → Provider Settings."
            )
        return defn, provider, model_name

    async def _call_model(self, defn, provider, model_name: str, user_msg: str) -> tuple[str, dict]:
        """Build a CompletionRequest, call the provider, return (content, usage_dict)."""
        request = CompletionRequest(
            model=model_name,
            messages=[Message(role=MessageRole.user, content=user_msg)],
            system_prompt=build_enhanced_system_prompt(defn.system_prompt, defn.role),
            # Output-token ceiling. 8192 is supported by every seeded model
            # (Anthropic claude-3-5-haiku caps at 8192; Gemini 2.5 flash/pro
            # advertise 8192) — 16384 exceeded Haiku's API limit and would error
            # on a real call. This is an OUTPUT cap; input is bounded by the
            # model's context window, not this value.
            max_tokens=8192,
            temperature=0.3,
        )
        response = await provider.complete(request)
        usage_dict = {
            "provider": response.provider,
            "model": response.model,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            # TASK 乙: capture the provider finish/stop reason for audit + future
            # truncation detection. Metadata only — NOT injected into handoff.
            "finish_reason": getattr(response, "finish_reason", None),
        }
        return response.content, usage_dict

    def _parse_and_validate(
        self,
        raw_content: str,
        role_name: str,
        schema_cls,
    ) -> ExecutionResult | dict:
        """Strip fences, parse JSON, validate schema.

        Returns the parsed dict on success, or an ExecutionResult(success=False) on failure.
        """
        raw_text = strip_code_fences(raw_content)
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            preview = raw_text[:200] + "..." if len(raw_text) > 200 else raw_text
            return ExecutionResult(
                success=False,
                output={},
                error_message=(
                    f"{role_name} returned invalid JSON: {exc}. "
                    f"Response preview: {preview}"
                ),
            )

        valid, err = schema_cls.validate(parsed)
        if not valid:
            return ExecutionResult(
                success=False,
                output=parsed,
                error_message=f"{role_name} output schema validation failed: {err}",
            )
        return parsed

    # ── Planner execution ─────────────────────────────────────

    async def _execute_planner(self, task_context: dict) -> ExecutionResult:
        """Call a real LLM to produce a structured Planner output."""
        defn, provider, model_name = self._resolve_provider(AgentRole.PLANNER)
        user_msg = self._build_planner_user_message(task_context)
        raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Planner", PlannerOutputSchema)
        if isinstance(result, ExecutionResult):
            result.token_usage = usage  # preserve actual provider/model for logging
            return result  # validation failed

        # Normalize optional fields
        result.setdefault("risks", [])
        result.setdefault("dependencies", [])

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _build_planner_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context."""
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs")
        if prev:
            parts.append(f"Previous agent outputs: {json.dumps(prev, ensure_ascii=False, separators=(',',':'))}")
        return "\n".join(parts)

    # ── Builder execution (plan-only, Phase 6D) ─────────────

    async def _execute_builder(self, task_context: dict) -> ExecutionResult:
        """Call a real LLM to produce a structured Builder change plan.

        Phase 6D: Builder operates in plan-only mode. It outputs a structured
        change plan but does NOT execute any file modifications, shell commands,
        or git operations.
        """
        defn, provider, model_name = self._resolve_provider(AgentRole.BUILDER)
        user_msg = self._build_builder_user_message(task_context)
        raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Builder", BuilderOutputSchema)
        if isinstance(result, ExecutionResult):
            result.token_usage = usage  # preserve actual provider/model for logging
            return result  # validation failed

        # Normalize optional fields (Phase 6D core)
        result.setdefault("risk_notes", [])

        # Normalize Phase 6E-A execution proposal fields
        _normalize_builder_proposal(result)

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _build_builder_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for Builder."""
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner output:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("architect"):
            parts.append(f"\nArchitect design:\n{json.dumps(prev['architect'], ensure_ascii=False, separators=(',',':'))}")

        # Include rejection history if this is a retry
        rejection_history = ctx.get("rejection_history")
        if rejection_history:
            parts.append(
                f"\nPrevious rejection(s):\n{json.dumps(rejection_history, ensure_ascii=False, separators=(',',':'))}"
            )

        # B1-Builder: inject the CURRENT content of EXISTING files this change will likely
        # modify (selected from architect components/interfaces + planner). Builder gets ONLY
        # this block — NOT 甲's structure / 乙-3a's facade — because the Architect already read
        # structure+facade and digested them into the design (which the Builder sees in full
        # via the architect output above); the Builder's unique gap is the line-level CURRENT
        # content of the files it must edit. No prior file block here → already_included=set(),
        # used_chars=0 (draws from the full shared budget). Sandboxed read_scoped_file; graceful None.
        taskfile_block = ModelAgentExecutor._build_builder_taskfile_context(ctx, set(), 0)
        if taskfile_block:
            parts.append(taskfile_block)
        return "\n".join(parts)

    # ── Reviewer execution ────────────────────────────────────

    async def _execute_reviewer(self, task_context: dict) -> ExecutionResult:
        """Call a real LLM to produce a structured Reviewer output."""
        defn, provider, model_name = self._resolve_provider(AgentRole.REVIEWER)
        user_msg = self._build_reviewer_user_message(task_context)
        raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Reviewer", ReviewerOutputSchema)
        if isinstance(result, ExecutionResult):
            result.token_usage = usage  # preserve actual provider/model for logging
            return result  # validation failed

        # Normalize optional fields
        result.setdefault("issues_found", [])

        # Map decision to ReviewDecision for the orchestrator
        decision_str = result["decision"]
        if decision_str == "approve":
            decision = ReviewDecision.APPROVE
        else:
            decision = ReviewDecision.REQUEST_CHANGES

        return ExecutionResult(
            success=True,
            output=result,
            decision=decision,
            token_usage=usage,
        )

    @staticmethod
    def _build_reviewer_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for Reviewer."""
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner output:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("builder"):
            parts.append(f"\nBuilder output:\n{json.dumps(prev['builder'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("qa"):
            parts.append(f"\nQA output:\n{json.dumps(prev['qa'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("security_reviewer"):
            parts.append(f"\nSecurity Reviewer output:\n{json.dumps(prev['security_reviewer'], ensure_ascii=False, separators=(',',':'))}")

        # Include rejection history if this is a retry
        rejection_history = ctx.get("rejection_history")
        if rejection_history:
            parts.append(
                f"\nPrevious rejection(s):\n{json.dumps(rejection_history, ensure_ascii=False, separators=(',',':'))}"
            )

        return "\n".join(parts)

    # ── QA execution (static review) ──────────────────────────

    async def _execute_qa(self, task_context: dict) -> ExecutionResult:
        """Call a real LLM to produce a structured QA static-review verdict.

        Mirrors _execute_planner / _execute_reviewer: resolve provider/model,
        build the user message, call the model, parse-and-validate the JSON
        (system prompt comes from the QA definition, like the other roles).
        QA statically reviews the Builder's proposal against the Planner's
        acceptance criteria — no tool execution.

        VETO INVARIANT — QA INFORMS the Reviewer, it does NOT veto. The
        orchestrator fails a task on any step success=False (see
        orchestrator._execute_step / the run loop). To avoid QA unilaterally
        failing the task and bypassing the Reviewer gate, this method ALWAYS
        returns success=True: a malformed/failed QA response is degraded to a
        Reviewer-weighable result="concerns" verdict instead of an error.
        """
        try:
            defn, provider, model_name = self._resolve_provider(AgentRole.QA)
            user_msg = self._build_qa_user_message(task_context)
            raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)
        except Exception as exc:
            # Provider/config/runtime error — degrade to concerns, never veto.
            return ExecutionResult(
                success=True,
                output=self._qa_concerns_fallback(
                    f"QA could not complete a real-model review ({exc}); "
                    f"flagged as concerns for the Reviewer to weigh."
                ),
            )

        result = self._parse_and_validate(raw_content, "QA", QaOutputSchema)
        if isinstance(result, ExecutionResult):
            # Malformed JSON / schema validation failed. Degrade to concerns
            # rather than success=False, so QA informs and does not veto.
            return ExecutionResult(
                success=True,
                output=self._qa_concerns_fallback(
                    f"QA model output was malformed or failed schema validation "
                    f"({result.error_message}); flagged as concerns for the Reviewer to weigh."
                ),
                token_usage=usage,
            )

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _qa_concerns_fallback(note: str) -> dict:
        """A schema-valid QA verdict flagging 'concerns' with a single finding.

        Used when QA cannot produce a valid verdict (provider error or malformed
        output). Returned with success=True so QA informs the Reviewer rather
        than vetoing the task (see the VETO INVARIANT in _execute_qa).
        """
        return {
            "validation_scope": "QA static review could not be completed normally.",
            "review_findings": [{"severity": "major", "description": note}],
            "acceptance_criteria_assessment": [],
            "result": "concerns",
            "summary": note,
        }

    @staticmethod
    def _build_qa_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for QA.

        QA reviews the Builder's proposal against the Planner's acceptance
        criteria. Degrades gracefully — only includes previous outputs that
        are present, never crashes on a missing one.
        """
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner plan:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("builder"):
            parts.append(f"\nBuilder proposal:\n{json.dumps(prev['builder'], ensure_ascii=False, separators=(',',':'))}")
        return "\n".join(parts)

    # ── Security Reviewer execution (static security review, NOT yet pipeline-wired) ──

    async def _execute_security_reviewer(self, task_context: dict, role=None) -> ExecutionResult:
        """Call a real LLM to produce a structured security-review verdict.

        Mirrors _execute_qa (resolve -> build -> call -> parse/validate). The
        Security Reviewer statically reviews the Builder's proposal for SEMANTIC
        security risks (no tool execution), complementing the rule-based
        tools/safety.py RiskClassifier.

        The system prompt comes from the SECURITY_REVIEWER definition (like the
        other real roles); the execute() dispatch passes the role, and the unit
        test stubs _resolve_provider.

        VETO INVARIANT — like QA, it INFORMS the Reviewer and does NOT veto: this
        method ALWAYS returns success=True. A provider error or a malformed/
        schema-invalid response degrades to a Reviewer-weighable verdict="concerns"
        instead of an error that would fail the task.
        """
        try:
            defn, provider, model_name = self._resolve_provider(role)
            user_msg = self._build_security_reviewer_user_message(task_context)
            raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)
        except Exception as exc:
            # Provider/config/runtime error — degrade to concerns, never veto.
            return ExecutionResult(
                success=True,
                output=self._sec_concerns_fallback(
                    f"Security review could not be completed ({exc}); "
                    f"flagged as concerns for the Reviewer to weigh."
                ),
            )

        result = self._parse_and_validate(raw_content, "Security Reviewer", SecurityReviewerOutputSchema)
        if isinstance(result, ExecutionResult):
            # Malformed JSON / schema validation failed. Degrade to concerns
            # rather than success=False, so it informs and does not veto.
            return ExecutionResult(
                success=True,
                output=self._sec_concerns_fallback(
                    f"Security-review output was malformed or failed schema validation "
                    f"({result.error_message}); flagged as concerns for the Reviewer to weigh."
                ),
                token_usage=usage,
            )

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _sec_concerns_fallback(note: str) -> dict:
        """A schema-valid security verdict flagging 'concerns' with one finding.

        Used when the security review cannot produce a valid verdict (provider
        error or malformed output). Returned with success=True so it informs the
        Reviewer rather than vetoing the task (see the VETO INVARIANT).
        """
        return {
            "review_scope": "Security static review could not be completed normally.",
            "findings": [{"severity": "medium", "category": "other", "description": note}],
            "overall_risk": "medium",
            "verdict": "concerns",
            "summary": note,
        }

    @staticmethod
    def _build_security_reviewer_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for the Security Reviewer.

        Reviews the Builder's proposal (and the Planner plan) for security risks.
        Degrades gracefully — only includes previous outputs that are present.
        """
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner plan:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("builder"):
            parts.append(f"\nBuilder proposal:\n{json.dumps(prev['builder'], ensure_ascii=False, separators=(',',':'))}")
        return "\n".join(parts)

    # ── Architect execution (technical design) ──

    async def _execute_architect(self, task_context: dict, role=None) -> ExecutionResult:
        """Call a real LLM to produce a structured technical design.

        Mirrors _execute_security_reviewer (resolve -> build -> call -> parse/validate).
        The Architect turns the Planner's plan into a technical design (component
        boundaries, interfaces, key decisions) that INFORMS the Builder — no tool
        execution, no code/files. The system prompt comes from the ARCHITECT definition
        (like the other roles); the execute() dispatch passes the role, and the unit test
        stubs _resolve_provider.

        VETO INVARIANT — like QA / Security Reviewer, it INFORMS the next role and does
        NOT veto: this method ALWAYS returns success=True. A provider error or a
        malformed/schema-invalid response degrades to a minimal valid design instead of
        an error that would fail the task.
        """
        try:
            defn, provider, model_name = self._resolve_provider(role)
            user_msg = self._build_architect_user_message(task_context)
            raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)
        except Exception as exc:
            # Provider/config/runtime error — degrade to a minimal valid design, never veto.
            return ExecutionResult(
                success=True,
                output=self._arch_concerns_fallback(
                    f"Architecture design could not be completed ({exc}); "
                    f"the Builder should proceed from the Planner's plan with caution."
                ),
            )

        result = self._parse_and_validate(raw_content, "Architect", ArchitectOutputSchema)
        if isinstance(result, ExecutionResult):
            # Malformed JSON / schema validation failed. Degrade to a minimal valid
            # design rather than success=False, so it informs the Builder and does not veto.
            return ExecutionResult(
                success=True,
                output=self._arch_concerns_fallback(
                    f"Architecture design output was malformed or failed schema validation "
                    f"({result.error_message}); the Builder should proceed from the Planner's plan with caution."
                ),
                token_usage=usage,
            )

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _arch_concerns_fallback(note: str) -> dict:
        """A schema-valid Architect design noting the design step was unavailable.

        Used when the design cannot be produced (provider error or malformed output).
        Returned with success=True so it informs the Builder rather than vetoing the
        task (see the VETO INVARIANT in _execute_architect).
        """
        return {
            "design_summary": "Technical design could not be produced normally.",
            "components": [],
            "key_decisions": [],
            "interfaces_or_contracts": [],
            "risks_tradeoffs": [],
            "summary": note,
        }

    @staticmethod
    def _build_architect_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for the Architect.

        The Architect designs from the Planner's plan; it runs BEFORE the Builder, so
        it does NOT receive the Builder's proposal. Degrades gracefully — only includes
        the planner output if present.
        """
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner plan:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")

        # B1-Arch-甲: inject the real project file/directory structure (PATHS ONLY,
        # no contents) so the Architect designs against the actual layout. Sandboxed,
        # capped; silently degrades to text-only when no workspace_root resolves.
        structure_block = ModelAgentExecutor._build_architect_structure_context(ctx)
        if structure_block:
            parts.append(structure_block)

        # B1-Arch-乙-3a: inject the CONTENT of a few FIXED facade files (README,
        # manifests, entry-point source) AFTER the structure block. Sandboxed via
        # read_scoped_file, multi-layer-capped; silently degrades when unavailable.
        # Returns the included-path set + chars used so 乙-3b can dedup + share budget.
        facade_block, facade_paths, facade_used = ModelAgentExecutor._build_architect_facade_context(ctx)
        if facade_block:
            parts.append(facade_block)

        # B1-Arch-乙-3b: inject the CONTENT of a few ADDITIONAL files THIS task is
        # likely to modify (selected by task-relevance), deduped against 乙-3a's set and
        # drawing from the SAME shared facade char budget. Sandboxed; graceful None.
        taskfile_block = ModelAgentExecutor._build_architect_taskfile_context(ctx, facade_paths, facade_used)
        if taskfile_block:
            parts.append(taskfile_block)
        return "\n".join(parts)

    # Caps for B1-Arch-甲 injected project structure (paths only) — keep the
    # listing well within the model's max_tokens budget (16384).
    _ARCH_TREE_MAX_ENTRIES = 1000
    _ARCH_TREE_MAX_TOTAL_CHARS = 12_000

    @staticmethod
    def _build_architect_structure_context(ctx: dict) -> str | None:
        """Inject the project's file/directory structure (PATHS ONLY) for the Architect.

        Lists the workspace tree via the sandboxed ``list_scoped_tree``
        (workspace-scoped, sensitive-pruned, symlink-safe, capped) and returns a
        labeled block of RELATIVE paths. PATHS ONLY — never file contents (reading
        contents is a separate, later decision). Returns ``None`` when no
        workspace_root resolves or the listing is empty (graceful: the Architect
        designs on text only). Never raises.
        """
        # Lazy import keeps model_executor's module-level import graph unchanged.
        from agents.scoped_file_reader import list_scoped_tree, resolve_workspace_root

        workspace_root = resolve_workspace_root(ctx.get("project_id"))
        if not workspace_root:
            return None

        paths, truncated = list_scoped_tree(
            workspace_root,
            ModelAgentExecutor._ARCH_TREE_MAX_ENTRIES,
            ModelAgentExecutor._ARCH_TREE_MAX_TOTAL_CHARS,
        )
        if not paths:
            return None

        suffix = ", truncated" if truncated else ""
        header = f"\nProject structure (file paths, {len(paths)} shown{suffix}):\n"
        return header + "\n".join(paths)

    # B1-Arch-乙-3a facade-file CONTENT caps. Read a small fixed set of "facade"
    # files (README, dependency manifests, entry-point source) to ground the design.
    # NOTE: max_tokens=16384 is the OUTPUT cap; these bound input cost/latency/signal.
    _ARCH_FACADE_MAX_FILES = 6        # at most 6 facade files included
    _ARCH_FACADE_PER_FILE_CAP = 6_000   # chars injected per facade file (truncate-with-note)
    _ARCH_FACADE_TOTAL_CAP = 18_000     # chars across all facade files
    # Bounded read ceiling passed to read_scoped_file as max_bytes: it DENIES (not
    # truncates) files above max_bytes, so to truncate-with-note (per the locked
    # decision) we read up to this ceiling — smaller than the 100KB default so we
    # never read a giant file whole — then slice to the per-file cap below.
    _ARCH_FACADE_READ_CEILING = 64_000

    # Try-in-order, include-if-exists, deduplicated facade candidates (decisions locked).
    _ARCH_FACADE_CANDIDATES = (
        "README.md", "README.rst", "README.txt",
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
        "package.json", "tsconfig.json",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "main.py", "app.py", "__main__.py", "src/main.py",
        "index.ts", "index.js", "src/index.ts", "src/index.js", "src/main.ts",
    )

    @staticmethod
    def _build_architect_facade_context(ctx: dict) -> "tuple[str | None, set[str], int]":
        """Inject the CONTENT of a small fixed set of facade files for the Architect.

        Reads README + dependency manifests + entry-point source via the sandboxed
        ``read_scoped_file`` (workspace-scoped, sensitive-denied, symlink + size
        checks) — it NEVER uses the unsandboxed ReadFileTool. Multi-layer caps run
        together: at most ``_ARCH_FACADE_MAX_FILES`` files, each truncated to
        ``_ARCH_FACADE_PER_FILE_CAP`` chars, total bounded by ``_ARCH_FACADE_TOTAL_CAP``.

        Returns ``(block, included_paths, used_chars)``:
        - block: the labeled block string, or ``None`` when no workspace_root resolves
          or nothing is found (graceful: the Architect designs on text + structure only).
        - included_paths: the set of relative paths actually included (so 乙-3b can dedup).
        - used_chars: total chars consumed (so 乙-3b can draw from the SHARED budget).
        Returns ``(None, set(), 0)`` on any no-op. The block string and per-file/total
        cap behavior are byte-identical to before — only the return shape changed.
        Never raises. PATHS ONLY is 甲; this is the first role to read CONTENT.
        """
        # Lazy import keeps model_executor's module-level import graph unchanged.
        from agents.scoped_file_reader import read_scoped_file, resolve_workspace_root
        from agents.secret_redactor import redact_secrets, log_redaction_hits

        workspace_root = resolve_workspace_root(ctx.get("project_id"))
        if not workspace_root:
            return None, set(), 0

        sections: list[str] = []
        included_paths: set[str] = set()
        redaction_hits: list[dict] = []
        included = 0
        used = 0
        budget_hit = False
        for path in ModelAgentExecutor._ARCH_FACADE_CANDIDATES:
            if included >= ModelAgentExecutor._ARCH_FACADE_MAX_FILES:
                break
            # Bounded read; read_scoped_file denies missing/sensitive/symlink/>ceiling.
            ok, content = read_scoped_file(
                path, workspace_root,
                max_bytes=ModelAgentExecutor._ARCH_FACADE_READ_CEILING,
            )
            if not ok:
                continue  # missing / sensitive / oversized / outside sandbox → skip

            # B1-甲: redact hardcoded secrets in the read content BEFORE the cap/budget
            # math, so the budget reflects what is actually injected. Redaction preserves
            # line structure and is byte-identical on clean files (no hits → unchanged).
            content, _hits = redact_secrets(content, path)
            redaction_hits.extend(_hits)

            truncated_file = False
            # Per-file cap: slice to the injection cap with a note.
            if len(content) > ModelAgentExecutor._ARCH_FACADE_PER_FILE_CAP:
                content = content[: ModelAgentExecutor._ARCH_FACADE_PER_FILE_CAP]
                truncated_file = True

            # Total-char budget guard across all facade files.
            remaining = ModelAgentExecutor._ARCH_FACADE_TOTAL_CAP - used
            if remaining <= 0:
                budget_hit = True
                break
            if len(content) > remaining:
                content = content[:remaining]
                truncated_file = True
                budget_hit = True

            header = f"\n--- {path}" + (" (truncated)" if truncated_file else "") + " ---\n"
            sections.append(header + content)
            used += len(content)
            included += 1
            included_paths.add(path)
            if budget_hit:
                break

        if not sections:
            return None, set(), 0
        log_redaction_hits(redaction_hits, context="architect-facade")
        block = "\nKey project files (content):" + "".join(sections)
        if budget_hit:
            block += "\n[...additional file content truncated to respect the context budget...]"
        return block, included_paths, used

    # B1-Arch-乙-3b task-relevant file CONTENT caps. A NEW selector that matches the
    # task's free-text signals against REAL workspace paths to add files THIS task is
    # likely to modify (the module defining the class being changed, etc.) — files
    # 乙-3a's fixed facade list does not cover. It draws from the SHARED
    # _ARCH_FACADE_TOTAL_CAP (no new budget), using only what 乙-3a left.
    _ARCH_TASKFILE_MAX_FILES = 3          # precise supplements, not broad coverage (tighter than facade's 6)
    _ARCH_TASKFILE_PER_FILE_CAP = 6_000   # same per-file cap as facade
    _ARCH_TASKFILE_READ_CEILING = 64_000  # same bounded read ceiling as facade
    _ARCH_TASKFILE_MIN_SCORE = 2          # require a filename-level signal (see keep condition)
    # Source-file extensions 乙-3b considers matchable (others skipped for now).
    _ARCH_TASKFILE_SOURCE_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb")

    @staticmethod
    def _build_architect_taskfile_context(ctx: dict, already_included: "set[str]", used_chars: int) -> "str | None":
        """Inject CONTENT of up to a few files THIS task is likely to modify (乙-3b).

        Builds the ARCHITECT-side signal (title/description + planner goal_summary/
        task_breakdown descriptions/acceptance_criteria), then delegates scoring +
        keep-threshold + ranking + shared-budget reading to the role-agnostic
        ``_select_and_read_taskfiles``. Direction is path->prose over the REAL
        ``list_scoped_tree`` universe, so it can only surface files that ACTUALLY EXIST;
        it dedups against ``already_included`` (乙-3a) and draws from the SHARED budget.
        Returns ``None`` on any no-op. Never raises.
        """
        try:
            from agents.scoped_file_reader import list_scoped_tree, resolve_workspace_root

            workspace_root = resolve_workspace_root(ctx.get("project_id"))
            if not workspace_root:
                return None
            paths, _truncated = list_scoped_tree(
                workspace_root,
                ModelAgentExecutor._ARCH_TREE_MAX_ENTRIES,
                ModelAgentExecutor._ARCH_TREE_MAX_TOTAL_CHARS,
            )
            if not paths:
                return None

            # seg2 (role-specific signal) — UNCHANGED from the original 乙-3b monolith:
            # title/description + planner goal_summary/task_breakdown/acceptance_criteria.
            sig_parts: list[str] = []
            title = ctx.get("title")
            if isinstance(title, str):
                sig_parts.append(title)
            desc = ctx.get("description")
            if isinstance(desc, str):
                sig_parts.append(desc)
            prev = ctx.get("previous_outputs", {})
            planner = prev.get("planner", {}) if isinstance(prev, dict) else {}
            if isinstance(planner, dict):
                gs = planner.get("goal_summary")
                if isinstance(gs, str):
                    sig_parts.append(gs)
                tb = planner.get("task_breakdown")
                if isinstance(tb, list):
                    for item in tb:
                        if isinstance(item, dict):
                            d = item.get("description")
                            if isinstance(d, str):
                                sig_parts.append(d)
                ac = planner.get("acceptance_criteria")
                if isinstance(ac, list):
                    for c in ac:
                        if isinstance(c, str):
                            sig_parts.append(c)
            signal = " ".join(sig_parts).lower()

            return ModelAgentExecutor._select_and_read_taskfiles(
                workspace_root, paths, signal, already_included, used_chars,
                block_label="Task-relevant project files (content)",
            )
        except Exception:
            return None  # defensive: never raise (matches 甲 / 乙-3a posture)

    @staticmethod
    def _select_and_read_taskfiles(
        workspace_root: str,
        paths: "list[str]",
        signal: str,
        already_included: "set[str]",
        used_chars: int,
        *,
        block_label: str,
    ) -> "str | None":
        """Role-AGNOSTIC task-file SELECTOR + READER core (shared by 乙-3b architect + B1 builder).

        Inputs: a resolved ``workspace_root``, the real path universe ``paths`` (from
        ``list_scoped_tree``), a lowercased ``signal`` built by the caller (the ONLY
        role-specific input), the ``already_included`` set to dedup against, and
        ``used_chars`` consumed by an earlier block (shared budget). Scores real source
        files by path-token -> ``signal`` substring (basename+3 / stem+2 / parent-stem+2
        / symbol-guess+1), keeps ``score >= _ARCH_TASKFILE_MIN_SCORE`` (a filename-level
        signal must hit — symbol-guess alone is dropped, the sole coincidental-substring
        false-positive source), ranks (score DESC, path len ASC, alpha), takes the top
        ``_ARCH_TASKFILE_MAX_FILES``, and reads them via the SANDBOXED ``read_scoped_file``
        (NEVER ReadFileTool) within the SHARED remaining budget
        (``_ARCH_FACADE_TOTAL_CAP`` - ``used_chars``). Returns a block headed by
        ``block_label``, or ``None`` on any no-op. Never raises.

        A not-ok read (sensitive/oversized/symlink/MISSING — e.g. a not-yet-created file)
        is skipped via ``continue``, so a caller's signal may name a file that does not
        exist and it is silently filtered by existence.
        """
        try:
            import os
            import re
            from agents.scoped_file_reader import read_scoped_file
            from agents.secret_redactor import redact_secrets, log_redaction_hits

            if not signal or not signal.strip():
                return None

            # Score real, non-already-included source files: path tokens -> signal substring.
            scored: list[tuple[int, str]] = []
            for p in paths:
                if p in already_included:
                    continue
                ext = os.path.splitext(p)[1].lower()
                if ext not in ModelAgentExecutor._ARCH_TASKFILE_SOURCE_EXTS:
                    continue
                posix = p.replace("\\", "/")
                basename = posix.rsplit("/", 1)[-1]            # e.g. models.py
                stem = os.path.splitext(basename)[0]           # e.g. models
                parent_qual = os.path.splitext(posix)[0]       # e.g. src/models
                # Heuristic primary-symbol guess: snake/kebab -> PascalCase (low weight).
                sym_parts = [w for w in re.split(r"[_\-]", stem) if w]
                symbol_guess = "".join(w[:1].upper() + w[1:] for w in sym_parts)
                score = 0
                if basename.lower() in signal:
                    score += 3                                  # full basename = strongest
                if len(stem) >= 3 and stem.lower() in signal:
                    score += 2                                  # stem (skip 1-2 char noise)
                if parent_qual.lower() != stem.lower() and parent_qual.lower() in signal:
                    score += 2                                  # parent-qualified stem
                if len(symbol_guess) >= 3 and symbol_guess.lower() in signal:
                    score += 1                                  # symbol-guess HINT, low weight
                # Require a FILENAME-level signal, not the symbol-guess (weight 1) alone:
                # symbol-guess is the only single-source weight-1 signal and the sole source
                # of coincidental-substring false positives (e.g. "Init" ⊂ "minitaskqueue").
                # score>=2 guarantees at least one of basename/stem/parent-stem hit. True
                # symbol reverse-lookup is a later B3 enhancement carrying its own >=2 weight.
                if score >= ModelAgentExecutor._ARCH_TASKFILE_MIN_SCORE:
                    scored.append((score, p))

            if not scored:
                return None

            # Rank: score DESC, then shorter path, then alphabetical (deterministic).
            scored.sort(key=lambda sp: (-sp[0], len(sp[1]), sp[1]))
            selected = [p for _, p in scored[: ModelAgentExecutor._ARCH_TASKFILE_MAX_FILES]]

            # Read within the SHARED remaining budget left by any earlier block.
            remaining_total = ModelAgentExecutor._ARCH_FACADE_TOTAL_CAP - used_chars
            if remaining_total <= 0:
                return None  # earlier block already used the whole shared budget

            sections: list[str] = []
            redaction_hits: list[dict] = []
            used_in_block = 0
            budget_hit = False
            for path in selected:
                ok, content = read_scoped_file(
                    path, workspace_root,
                    max_bytes=ModelAgentExecutor._ARCH_TASKFILE_READ_CEILING,
                )
                if not ok:
                    continue  # sensitive/oversized/symlink/MISSING (e.g. create-target) → skip
                # B1-甲: redact hardcoded secrets BEFORE cap/budget math (covers BOTH the
                # architect 乙-3b and builder callers via this shared core). Preserves line
                # structure; byte-identical on clean files.
                content, _hits = redact_secrets(content, path)
                redaction_hits.extend(_hits)
                truncated_file = False
                if len(content) > ModelAgentExecutor._ARCH_TASKFILE_PER_FILE_CAP:
                    content = content[: ModelAgentExecutor._ARCH_TASKFILE_PER_FILE_CAP]
                    truncated_file = True
                rem = remaining_total - used_in_block
                if rem <= 0:
                    budget_hit = True
                    break
                if len(content) > rem:
                    content = content[:rem]
                    truncated_file = True
                    budget_hit = True
                header = f"\n--- {path}" + (" (truncated)" if truncated_file else "") + " ---\n"
                sections.append(header + content)
                used_in_block += len(content)
                if budget_hit:
                    break

            if not sections:
                return None
            log_redaction_hits(redaction_hits, context=block_label)
            block = f"\n{block_label}:" + "".join(sections)
            if budget_hit:
                block += "\n[...additional task-relevant file content truncated to respect the context budget...]"
            return block
        except Exception:
            return None  # defensive: never raise (matches 甲 / 乙-3a posture)

    @staticmethod
    def _build_builder_taskfile_context(ctx: dict, already_included: "set[str]", used_chars: int) -> "str | None":
        """Inject CONTENT of EXISTING files this change will likely MODIFY (B1 builder).

        Mirrors the architect taskfile helper but with a BUILDER-SPECIFIC signal source:
        PRIMARY = the architect's STRUCTURED fields — component names + their interfaces,
        plus interfaces_or_contracts (component names are usually module/class names that
        map to real files, and the interface/contract strings often name the files/paths
        to touch); SECONDARY = planner goal_summary + task_breakdown descriptions + the
        task title/description.

        DELIBERATELY EXCLUDED from the signal (reasoning prose with NO file-locating value):
        architect design_summary / summary / risks_tradeoffs / key_decisions
        (decision/rationale/alternatives), and planner acceptance_criteria / risks.
        Including them would dilute the strong component-name/interface signal and worsen
        substring false positives — builder's architect-output signal is noisier than the
        architect's planner source, so we precision-select fields rather than dumping the
        whole architect output into the signal.

        SEMANTICS: injects ONLY the CURRENT content of EXISTING files. Files the builder
        will CREATE don't exist yet → read_scoped_file returns not-ok → skipped by the
        shared reader (no create/modify branching; "file exists or not" is the filter).
        This grounds MODIFY-class proposals in the real current code and is a correct
        no-op for pure-create files. Never raises.
        """
        try:
            from agents.scoped_file_reader import list_scoped_tree, resolve_workspace_root

            workspace_root = resolve_workspace_root(ctx.get("project_id"))
            if not workspace_root:
                return None
            paths, _truncated = list_scoped_tree(
                workspace_root,
                ModelAgentExecutor._ARCH_TREE_MAX_ENTRIES,
                ModelAgentExecutor._ARCH_TREE_MAX_TOTAL_CHARS,
            )
            if not paths:
                return None

            sig_parts: list[str] = []
            prev = ctx.get("previous_outputs", {})
            prev = prev if isinstance(prev, dict) else {}
            # PRIMARY: architect structured fields (component names + interfaces + contracts).
            arch = prev.get("architect", {})
            if isinstance(arch, dict):
                comps = arch.get("components")
                if isinstance(comps, list):
                    for c in comps:
                        if isinstance(c, dict):
                            name = c.get("name")
                            if isinstance(name, str):
                                sig_parts.append(name)
                            iface = c.get("interfaces")
                            if isinstance(iface, str):
                                sig_parts.append(iface)
                ioc = arch.get("interfaces_or_contracts")
                if isinstance(ioc, list):
                    for s in ioc:
                        if isinstance(s, str):
                            sig_parts.append(s)
            # SECONDARY: planner goal_summary + task_breakdown descriptions.
            planner = prev.get("planner", {})
            if isinstance(planner, dict):
                gs = planner.get("goal_summary")
                if isinstance(gs, str):
                    sig_parts.append(gs)
                tb = planner.get("task_breakdown")
                if isinstance(tb, list):
                    for item in tb:
                        if isinstance(item, dict):
                            d = item.get("description")
                            if isinstance(d, str):
                                sig_parts.append(d)
            # Task framing.
            title = ctx.get("title")
            if isinstance(title, str):
                sig_parts.append(title)
            desc = ctx.get("description")
            if isinstance(desc, str):
                sig_parts.append(desc)
            # EXCLUDED (reasoning prose, no file-locating value, adds substring noise):
            # arch design_summary / summary / risks_tradeoffs / key_decisions; planner
            # acceptance_criteria / risks. See docstring for the rationale.
            signal = " ".join(sig_parts).lower()

            return ModelAgentExecutor._select_and_read_taskfiles(
                workspace_root, paths, signal, already_included, used_chars,
                block_label="Existing files this change will likely modify (content)",
            )
        except Exception:
            return None

    # ── Documentation execution (a "second Builder": proposes doc files) ──

    async def _execute_documentation(self, task_context: dict, role=None) -> ExecutionResult:
        """Call a real LLM to propose documentation file changes (a Builder-shaped proposal).

        The Documentation role is modeled on the BUILDER, not the read-only verdict roles:
        on success it produces proposed_files (with full content) that DOC-3 will turn into
        an execution proposal flowing the existing approval / dry-run / execute chain.

        HYBRID semantics (veto-safe failure path + Builder-like success path):
        - ALWAYS returns success=True, so a documentation failure never fails an
          already-approved task (Documentation runs AFTER the Reviewer in DOC-3).
        - On a provider/resolution error OR malformed/schema-invalid output, it degrades
          to _doc_skip_fallback — a marker output WITHOUT proposed_files ({"skipped": true,
          ...}). DOC-3 will create a proposal ONLY when the output actually carries valid
          proposed_files, so a skipped doc step produces no (broken/empty) proposal.
        - On success (valid output with proposed_files), it returns the parsed proposal.

        The system prompt comes from the DOCUMENTATION definition (like the other roles);
        the execute() dispatch passes the role, and the unit test stubs _resolve_provider.
        """
        try:
            defn, provider, model_name = self._resolve_provider(role)
            user_msg = self._build_documentation_user_message(task_context)
            raw_content, usage = await self._call_model(defn, provider, model_name, user_msg)
        except Exception as exc:
            # Provider/config/runtime error — skip documentation, never fail the task.
            return ExecutionResult(
                success=True,
                output=self._doc_skip_fallback(
                    f"Documentation could not be produced ({exc}); no documentation proposed."
                ),
            )

        result = self._parse_and_validate(raw_content, "Documentation", DocumentationOutputSchema)
        if isinstance(result, ExecutionResult):
            # Malformed JSON / schema validation failed (e.g. empty content). Skip rather
            # than emit a broken proposal — success=True so the task is not failed.
            return ExecutionResult(
                success=True,
                output=self._doc_skip_fallback(
                    f"Documentation output was malformed or failed schema validation "
                    f"({result.error_message}); no documentation proposed."
                ),
                token_usage=usage,
            )

        return ExecutionResult(success=True, output=result, token_usage=usage)

    @staticmethod
    def _doc_skip_fallback(note: str) -> dict:
        """A 'no documentation proposed' marker — deliberately has NO proposed_files.

        Used when documentation cannot be produced (provider error or malformed output).
        Returned with success=True so it never fails the task. Because it carries no
        proposed_files, the DOC-3 proposal-creation step (which will gate on the presence
        of valid proposed_files) creates no proposal for a skipped doc step — avoiding a
        broken/empty execution proposal. It is intentionally NOT a DocumentationOutputSchema-
        valid proposal (that schema requires proposed_files >= 1).
        """
        return {"skipped": True, "reason": note, "change_summary": note}

    @staticmethod
    def _build_documentation_user_message(ctx: dict) -> str:
        """Assemble the user-facing prompt from task context for Documentation.

        Documentation runs after the work is done, so it documents what was built: it
        includes the Planner's plan, the Architect's design (if present), and the
        Builder's proposal / implemented changes. Degrades gracefully — only includes
        whichever previous outputs are present.
        """
        parts = [
            f"Task: {ctx.get('title', 'Untitled')}",
            f"Description: {ctx.get('description', 'No description provided')}",
            f"Priority: {ctx.get('priority', 'medium')}",
        ]
        prev = ctx.get("previous_outputs", {})
        if prev.get("planner"):
            parts.append(f"\nPlanner plan:\n{json.dumps(prev['planner'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("architect"):
            parts.append(f"\nArchitect design:\n{json.dumps(prev['architect'], ensure_ascii=False, separators=(',',':'))}")
        if prev.get("builder"):
            parts.append(f"\nBuilder proposal (implemented changes):\n{json.dumps(prev['builder'], ensure_ascii=False, separators=(',',':'))}")

        # B1: pre-fetch the CURRENT on-disk content of the files the Builder will
        # MODIFY, so Documentation describes what actually changes. Sandboxed,
        # read-only, modify-targets ONLY; silently degrades when unavailable.
        file_block = ModelAgentExecutor._build_doc_modify_file_context(ctx)
        if file_block:
            parts.append(file_block)
        return "\n".join(parts)

    # Caps for B1 pre-fetched file content. Keep the injected context well within
    # the model's max_tokens budget (16384) — read at most N modify targets and
    # cap the total injected characters.
    _DOC_READ_MAX_FILES = 10
    _DOC_READ_TOTAL_CAP = 24_000  # chars across all injected files

    @staticmethod
    def _build_doc_modify_file_context(ctx: dict) -> str | None:
        """Pre-fetch current on-disk content of the Builder's modify targets (B1).

        HARDCODED MINIMAL READ POLICY: read ONLY ``proposed_files`` entries whose
        action == "modify"; nothing else (``create`` targets don't exist yet; no
        directory listing; no project scan). Every read goes through the sandboxed
        ``read_scoped_file`` (workspace-scoped, sensitive deny-list, symlink + size
        checks) — it NEVER uses the unsandboxed ReadFileTool.

        Returns a labeled prompt block, or ``None`` when there is nothing to inject
        (no Builder output, no modify targets, no resolvable workspace_root, or
        every read denied). Never raises — Documentation still works on text only.
        """
        prev = ctx.get("previous_outputs", {})
        builder = prev.get("builder")
        if not isinstance(builder, dict):
            return None
        proposed = builder.get("proposed_files")
        if not isinstance(proposed, list):
            return None

        modify_targets: list[str] = []
        for f in proposed:
            if not isinstance(f, dict):
                continue
            action = f.get("action") or f.get("operation")
            if action != "modify":
                continue
            path = f.get("path")
            if isinstance(path, str) and path.strip():
                modify_targets.append(path)
        if not modify_targets:
            return None

        # Lazy import keeps model_executor's module-level import graph unchanged.
        from agents.scoped_file_reader import read_scoped_file, resolve_workspace_root
        from agents.secret_redactor import redact_secrets, log_redaction_hits

        workspace_root = resolve_workspace_root(ctx.get("project_id"))
        if not workspace_root:
            return None  # graceful: no file content injected, role works on text

        sections: list[str] = []
        doc_redaction_hits: list[dict] = []
        used = 0
        for path in modify_targets[: ModelAgentExecutor._DOC_READ_MAX_FILES]:
            ok, result = read_scoped_file(path, workspace_root)
            if not ok:
                continue  # denied / missing / oversized / outside sandbox → skip
            # B1-甲: redact hardcoded secrets BEFORE the cap. This cap is a total-budget
            # slice (snippet[:remaining]); if it ran first, a secret could be split across
            # the cap boundary, leaving the redactor unable to match the truncated token
            # and leaking a partial secret. read -> redact -> cap -> append (same order as
            # the other three B1 injection points). Byte-identical on clean files.
            result, _doc_hits = redact_secrets(result, path)
            doc_redaction_hits.extend(_doc_hits)
            remaining = ModelAgentExecutor._DOC_READ_TOTAL_CAP - used
            if remaining <= 0:
                sections.append("\n[...additional files omitted to respect the context budget...]")
                break
            snippet = result
            truncated = False
            if len(snippet) > remaining:
                snippet = snippet[:remaining]
                truncated = True
            used += len(snippet)
            header = (
                f"\n--- Current content of {path}"
                + (" (truncated)" if truncated else "")
                + " ---"
            )
            sections.append(header + "\n" + snippet)
            if truncated:
                sections.append("\n[...truncated to respect the context budget...]")
                break

        if not sections:
            return None
        log_redaction_hits(doc_redaction_hits, context="documentation")
        return (
            "\nCurrent content of files to be documented "
            "(modify targets, read from the project workspace):"
            + "".join(sections)
        )
