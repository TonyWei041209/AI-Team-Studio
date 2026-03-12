"""Pre-execution validation for Builder execution proposals (Phase 6E-A).

Validates proposed file paths and commands WITHOUT executing anything.
Reuses existing risk classification infrastructure from safety.py and
the command whitelist from shell_executor.py.
"""

from __future__ import annotations

import re
from typing import Any

from tools.safety import (
    FILE_DANGEROUS_PATTERNS,
    RiskClassifier,
    RiskLevel,
    SHELL_DANGEROUS_PATTERNS,
)
from tools.shell_executor import COMMAND_WHITELIST


# ── Path validation patterns ─────────────────────────────────

_SYSTEM_DIR_PATTERNS = [
    re.compile(r"^/etc(/|$)", re.IGNORECASE),
    re.compile(r"^/usr(/|$)", re.IGNORECASE),
    re.compile(r"^/bin(/|$)", re.IGNORECASE),
    re.compile(r"^/sbin(/|$)", re.IGNORECASE),
    re.compile(r"^C:\\Windows", re.IGNORECASE),
    re.compile(r"^C:\\Program Files", re.IGNORECASE),
    re.compile(r"^C:\\ProgramData", re.IGNORECASE),
]

_TRAVERSAL_PATTERN = re.compile(r"\.\.[/\\]")


class ProposalValidator:
    """Validates Builder execution proposals without executing anything."""

    def __init__(self) -> None:
        self._classifier = RiskClassifier()

    def validate_file_paths(
        self,
        proposed_files: list[dict[str, Any]],
        working_dir: str = "",
    ) -> list[dict[str, Any]]:
        """Validate proposed file paths for safety.

        Returns a list of ``{path, action, valid, reason}`` dicts.
        Does NOT perform any file I/O.
        """
        results: list[dict[str, Any]] = []
        for item in proposed_files:
            path = item.get("path", "")
            action = item.get("action", "unknown")
            entry: dict[str, Any] = {
                "path": path,
                "action": action,
                "valid": True,
                "reason": "ok",
            }

            if not path or not path.strip():
                entry["valid"] = False
                entry["reason"] = "Empty file path"
                results.append(entry)
                continue

            # Check path traversal
            if _TRAVERSAL_PATTERN.search(path):
                entry["valid"] = False
                entry["reason"] = "Path traversal detected (../)"
                results.append(entry)
                continue

            # Check system directories
            for pat in _SYSTEM_DIR_PATTERNS:
                if pat.search(path):
                    entry["valid"] = False
                    entry["reason"] = f"System directory: {path}"
                    break

            # Check sensitive dotfiles via FILE_DANGEROUS_PATTERNS
            if entry["valid"]:
                for rule in FILE_DANGEROUS_PATTERNS:
                    if re.search(rule.pattern, path, re.IGNORECASE):
                        entry["valid"] = False
                        entry["reason"] = rule.description
                        break

            results.append(entry)
        return results

    def classify_commands(
        self,
        proposed_commands: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Classify proposed commands by risk level.

        Returns a list of ``{command, risk_level, reason, whitelisted}`` dicts.
        Does NOT execute any commands.
        """
        results: list[dict[str, Any]] = []
        for item in proposed_commands:
            command = item.get("command", "")
            entry: dict[str, Any] = {
                "command": command,
                "risk_level": "low",
                "reason": "ok",
                "whitelisted": False,
            }

            if not command.strip():
                entry["risk_level"] = "low"
                entry["reason"] = "Empty command"
                results.append(entry)
                continue

            # Extract base command
            parts = command.strip().split()
            base_cmd = parts[0].split("/")[-1].split("\\")[-1] if parts else ""

            # Check whitelist
            entry["whitelisted"] = base_cmd in COMMAND_WHITELIST

            # Scan for dangerous patterns
            risk_level, reason = self._classifier.classify("shell", {"command": command})
            if risk_level != RiskLevel.SAFE:
                entry["risk_level"] = risk_level.value
                entry["reason"] = reason
            elif not entry["whitelisted"]:
                entry["risk_level"] = "high"
                entry["reason"] = f"Command '{base_cmd}' not in whitelist"
            else:
                entry["risk_level"] = "low"
                entry["reason"] = "Whitelisted command"

            results.append(entry)
        return results

    def generate_risk_report(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Generate a comprehensive risk report from a Builder proposal.

        Returns a dict with overall_risk, file_risks, command_risks,
        approval_required, and reasons.  Does NOT execute anything.
        """
        proposed_files = proposal.get("proposed_files", [])
        proposed_commands = proposal.get("proposed_commands", [])

        file_risks = self.validate_file_paths(proposed_files)
        command_risks = self.classify_commands(proposed_commands)

        reasons: list[str] = []
        max_risk = "low"

        # Assess file risks
        for fr in file_risks:
            if not fr["valid"]:
                max_risk = self._max_risk(max_risk, "high")
                reasons.append(f"File risk: {fr['reason']} ({fr['path']})")
            elif fr["action"] == "delete":
                max_risk = self._max_risk(max_risk, "high")
                reasons.append(f"File deletion proposed: {fr['path']}")

        # Assess command risks
        for cr in command_risks:
            max_risk = self._max_risk(max_risk, cr["risk_level"])
            if cr["risk_level"] in ("high", "critical"):
                reasons.append(f"Command risk: {cr['reason']} ({cr['command']})")

        # If no proposed commands and no deletions, keep low/medium
        if not proposed_commands and not any(
            f.get("action") == "delete" for f in proposed_files
        ):
            if max_risk == "low":
                # Still at least medium for any file modifications
                if any(f.get("action") in ("create", "modify") for f in proposed_files):
                    max_risk = "medium"

        approval_required = max_risk in ("high", "critical")
        if approval_required and not reasons:
            reasons.append(f"Overall risk level: {max_risk}")

        return {
            "overall_risk": max_risk,
            "file_risks": file_risks,
            "command_risks": command_risks,
            "approval_required": approval_required,
            "reasons": reasons,
        }

    @staticmethod
    def _max_risk(a: str, b: str) -> str:
        """Return the higher of two risk level strings."""
        rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return a if rank.get(a, 0) >= rank.get(b, 0) else b
