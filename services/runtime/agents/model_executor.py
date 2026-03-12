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


# ── Code-fence stripping ──────────────────────────────────────

_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL,
)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output.

    Handles both ````` ```json ... ``` ````` and ````` ``` ... ``` `````.
    """
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


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

    Currently supports the **Planner**, **Builder** (plan-only), and **Reviewer** roles.
    QA will raise ``NotImplementedError``.

    Phase 6C: Provider/model are resolved from role_model_settings DB table.
    Phase 6D: Builder added in plan-only mode (outputs change plan, no tool execution).
    Phase 6E-A: Builder output normalized with execution proposal fields.
    """

    _SUPPORTED_ROLES = {AgentRole.PLANNER, AgentRole.BUILDER, AgentRole.REVIEWER}

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
        if role == AgentRole.BUILDER:
            return await self._execute_builder(task_context)
        if role == AgentRole.REVIEWER:
            return await self._execute_reviewer(task_context)
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

    async def _call_model(self, defn, provider, model_name: str, user_msg: str) -> str:
        """Build a CompletionRequest, call the provider, return raw content."""
        request = CompletionRequest(
            model=model_name,
            messages=[Message(role=MessageRole.user, content=user_msg)],
            system_prompt=defn.system_prompt,
            max_tokens=4096,
            temperature=0.3,
        )
        response = await provider.complete(request)
        return response.content

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
        raw_content = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Planner", PlannerOutputSchema)
        if isinstance(result, ExecutionResult):
            return result  # validation failed

        # Normalize optional fields
        result.setdefault("risks", [])
        result.setdefault("dependencies", [])

        return ExecutionResult(success=True, output=result)

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
            parts.append(f"Previous agent outputs: {json.dumps(prev, indent=2)}")
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
        raw_content = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Builder", BuilderOutputSchema)
        if isinstance(result, ExecutionResult):
            return result  # validation failed

        # Normalize optional fields (Phase 6D core)
        result.setdefault("risk_notes", [])

        # Normalize Phase 6E-A execution proposal fields
        _normalize_builder_proposal(result)

        return ExecutionResult(success=True, output=result)

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
            parts.append(f"\nPlanner output:\n{json.dumps(prev['planner'], indent=2)}")

        # Include rejection history if this is a retry
        rejection_history = ctx.get("rejection_history")
        if rejection_history:
            parts.append(
                f"\nPrevious rejection(s):\n{json.dumps(rejection_history, indent=2)}"
            )

        return "\n".join(parts)

    # ── Reviewer execution ────────────────────────────────────

    async def _execute_reviewer(self, task_context: dict) -> ExecutionResult:
        """Call a real LLM to produce a structured Reviewer output."""
        defn, provider, model_name = self._resolve_provider(AgentRole.REVIEWER)
        user_msg = self._build_reviewer_user_message(task_context)
        raw_content = await self._call_model(defn, provider, model_name, user_msg)

        result = self._parse_and_validate(raw_content, "Reviewer", ReviewerOutputSchema)
        if isinstance(result, ExecutionResult):
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
            parts.append(f"\nPlanner output:\n{json.dumps(prev['planner'], indent=2)}")
        if prev.get("builder"):
            parts.append(f"\nBuilder output:\n{json.dumps(prev['builder'], indent=2)}")
        if prev.get("qa"):
            parts.append(f"\nQA output:\n{json.dumps(prev['qa'], indent=2)}")

        # Include rejection history if this is a retry
        rejection_history = ctx.get("rejection_history")
        if rejection_history:
            parts.append(
                f"\nPrevious rejection(s):\n{json.dumps(rejection_history, indent=2)}"
            )

        return "\n".join(parts)
