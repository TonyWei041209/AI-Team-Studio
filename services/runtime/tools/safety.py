"""Risk classification for tool invocations.

Every tool call is classified into a RiskLevel before execution.
HIGH and CRITICAL levels trigger the approval gate and block execution
until a human approves the action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


# ── Risk levels ───────────────────────────────────────────────


class RiskLevel(str, Enum):
    SAFE = "safe"            # read-only, no side effects
    LOW = "low"              # minor side effects (e.g. write a small file)
    HIGH = "high"            # significant side effects — needs approval
    CRITICAL = "critical"    # never auto-execute — always needs approval


# ── Risk rules ────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskRule:
    """A single pattern-based risk detection rule.

    Attributes
    ----------
    pattern      Regex to match against the scannable text.
    level        Risk level when this rule matches.
    description  Human-readable explanation of the risk.
    applies_to   Tool name this rule applies to, or ``"*"`` for all tools.
    """

    pattern: str
    level: RiskLevel
    description: str
    applies_to: str


# ── Built-in rules ────────────────────────────────────────────


SHELL_DANGEROUS_PATTERNS: list[RiskRule] = [
    # ── Critical: never auto-execute ──
    RiskRule(r"\brm\s+-r", RiskLevel.CRITICAL, "Recursive delete command", "shell"),
    RiskRule(r"\bgit\s+reset\b", RiskLevel.CRITICAL, "Git reset", "shell"),
    RiskRule(r"\bgit\s+push\s+.*--force\b", RiskLevel.CRITICAL, "Git force push", "shell"),
    RiskRule(r"\bgit\s+push\s+-f\b", RiskLevel.CRITICAL, "Git force push (-f)", "shell"),
    RiskRule(r"\bsudo\b", RiskLevel.CRITICAL, "Superuser command", "shell"),
    RiskRule(r"\bmkfs\b", RiskLevel.CRITICAL, "Filesystem format", "shell"),
    RiskRule(r"\bdd\b\s+", RiskLevel.CRITICAL, "Low-level disk write", "shell"),
    RiskRule(r"\bcurl\b.*\|\s*\bsh\b", RiskLevel.CRITICAL, "Pipe URL to shell", "shell"),
    RiskRule(r"\bwget\b.*\|\s*\bsh\b", RiskLevel.CRITICAL, "Pipe URL to shell", "shell"),
    RiskRule(r">\s*/dev/", RiskLevel.CRITICAL, "Write to device", "shell"),
    RiskRule(r"\bformat\s+[a-zA-Z]:", RiskLevel.CRITICAL, "Format drive (Windows)", "shell"),
    RiskRule(r"\bdel\s+/[sS]", RiskLevel.CRITICAL, "Recursive delete (Windows)", "shell"),
    RiskRule(r"\brd\s+/[sS]", RiskLevel.CRITICAL, "Recursive rmdir (Windows)", "shell"),
    # ── High: needs approval ──
    RiskRule(r"\brm\b", RiskLevel.HIGH, "Delete command", "shell"),
    RiskRule(r"\bgit\s+clean\b", RiskLevel.HIGH, "Git clean", "shell"),
    RiskRule(r"\bgit\s+checkout\s+--\s", RiskLevel.HIGH, "Git discard changes", "shell"),
    RiskRule(r"\bchmod\b", RiskLevel.HIGH, "Permission change", "shell"),
    RiskRule(r"\bchown\b", RiskLevel.HIGH, "Ownership change", "shell"),
    RiskRule(r"\bdel\b", RiskLevel.HIGH, "Delete command (Windows)", "shell"),
]

FILE_DANGEROUS_PATTERNS: list[RiskRule] = [
    RiskRule(r"\.\.[/\\]", RiskLevel.HIGH, "Path traversal attempt", "write_file"),
    RiskRule(
        r"(\.env|\.ssh|\.gitconfig|\.npmrc|\.pypirc)",
        RiskLevel.HIGH,
        "Write to sensitive dotfile",
        "write_file",
    ),
    RiskRule(
        r"(/etc/|C:\\Windows\\|C:\\Program Files)",
        RiskLevel.CRITICAL,
        "Write to system directory",
        "write_file",
    ),
]

ALL_RULES: list[RiskRule] = SHELL_DANGEROUS_PATTERNS + FILE_DANGEROUS_PATTERNS


# ── Classifier ────────────────────────────────────────────────


class RiskClassifier:
    """Classifies the risk level of a tool invocation by scanning params."""

    def __init__(self, rules: list[RiskRule] | None = None) -> None:
        self._rules = rules if rules is not None else ALL_RULES

    def classify(self, tool_name: str, params: dict) -> tuple[RiskLevel, str]:
        """Return ``(risk_level, reason)`` for a tool invocation.

        The scannable text is extracted from the relevant parameter
        (``command`` for shell, ``path`` for file tools, etc.).
        Returns ``(SAFE, "")`` when no rules match.
        """
        text = self._extract_scannable_text(tool_name, params)

        highest_level = RiskLevel.SAFE
        highest_reason = ""

        for rule in self._rules:
            if rule.applies_to != "*" and rule.applies_to != tool_name:
                continue
            if re.search(rule.pattern, text, re.IGNORECASE):
                if self._level_rank(rule.level) > self._level_rank(highest_level):
                    highest_level = rule.level
                    highest_reason = rule.description

        return highest_level, highest_reason

    def needs_approval(self, risk_level: RiskLevel) -> bool:
        """Return ``True`` if *risk_level* requires human approval."""
        return risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    # ── Internals ─────────────────────────────────────────────

    @staticmethod
    def _extract_scannable_text(tool_name: str, params: dict) -> str:
        """Extract the text to scan for risk patterns."""
        if tool_name == "shell":
            return params.get("command", "")
        elif tool_name in ("write_file", "read_file", "list_directory"):
            return params.get("path", "")
        else:
            # Fallback: concatenate all string values
            return " ".join(str(v) for v in params.values())

    @staticmethod
    def _level_rank(level: RiskLevel) -> int:
        return {"safe": 0, "low": 1, "high": 2, "critical": 3}[level.value]
