"""Content-level secret redactor for the B1 file-content injection layer.

Scans file CONTENT (already read via the sandboxed ``read_scoped_file``) for likely
hardcoded secrets and REDACTS the secret VALUES in-place before the content is
injected into a role's prompt (architect facade 乙-3a, architect taskfile 乙-3b,
builder taskfile). This closes the B1 exposure surface: source files with hardcoded
keys being read into prompts and sent to the provider.

LOCKED design decisions:
  1. REDACT-replace (value -> ``[REDACTED-SECRET]``), never whole-file-reject.
  2. Pure REGEX patterns, two tiers, conservative-first (prefer false-negatives).
     Entropy detection and no-prefix secret coverage are deliberately OUT (future
     enhancement) to keep false-positives ~zero — mirrors the 乙-3b "precise-first"
     philosophy.
  3. Metadata-only side-log (path/line/pattern_type/confidence) — NEVER the value.
  4. Lives ABOVE the sandbox: it does NOT touch read_scoped_file / scoped_file_reader /
     _validate_file_path / _is_sensitive_path. Redaction is a separate layer on top of
     sandboxed reads.

INVARIANT — line structure is preserved: only matched secret VALUES are replaced with
a newline-free marker (PEM bodies are rebuilt line-for-line), so the number of lines
and line boundaries of the content are UNCHANGED. This keeps side-log line numbers
mapped to the real file and keeps a builder's view of line positions correct.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

REDACTION_MARKER = "[REDACTED-SECRET]"

# A leading (?<![A-Za-z0-9_-]) anchor avoids matching a distinctive token embedded
# mid-identifier (e.g. "sk-..." inside "task-...") — a false-positive-reduction
# refinement consistent with the conservative-first philosophy.
_BOUND = r"(?<![A-Za-z0-9_-])"

# ── TIER 1: high-confidence distinctive prefixes/formats (near-zero false-positive). ──
# (pattern_type, compiled_regex, mode). mode "whole" redacts the whole match;
# "after_prefix" keeps group(1) and redacts the rest.
_TIER1 = [
    ("aws_access_key_id", re.compile(_BOUND + r"AKIA[0-9A-Z]{16}"), "whole"),
    ("github_token", re.compile(_BOUND + r"gh[poasu]_[A-Za-z0-9]{36,}"), "whole"),
    ("slack_token", re.compile(_BOUND + r"xox[baprs]-[A-Za-z0-9-]{10,}"), "whole"),
    ("anthropic_key", re.compile(_BOUND + r"sk-ant-[A-Za-z0-9_-]{20,}"), "whole"),
    ("openai_key", re.compile(_BOUND + r"sk-[A-Za-z0-9_-]{20,}"), "whole"),
    ("google_api_key", re.compile(_BOUND + r"AIza[0-9A-Za-z_-]{35}"), "whole"),
    ("bearer_token", re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{20,})"), "after_prefix"),
]

# ── PEM private keys (multi-line) — redact the body, preserve line count. ──
_PEM_RE = re.compile(
    r"(-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----)"
    r"(.*?)"
    r"(-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----)",
    re.DOTALL,
)

# ── TIER 2: variable-name + assignment (gated on value shape). ──
# Value is single-line ([^"'\n]+) so a match cannot span / change line boundaries.
_TIER2_RE = re.compile(
    r"""(?ix)
    (api[_-]?key|apikey|secret[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret
     |auth[_-]?token|secret|token|password|passwd|pwd)   # 1: key name
    (\s*[=:]\s*)                                          # 2: assignment op
    (["'])                                               # 3: opening quote
    ([^"'\n]+)                                            # 4: value (single-line)
    (["'])                                               # 5: closing quote
    """,
)
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(your[_-]?.*|x{3,}|changeme|placeholder|example|test|dummy|none|null"
    r"|true|false|<.*>|\$\{.*\}|\.\.\.)$"
)
_TIER2_MIN_LEN = 16


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if len(set(v)) == 1:          # all the same char (e.g. "aaaaaaaaaaaaaaaa")
        return True
    return bool(_PLACEHOLDER_RE.match(v))


def _redact_pem(content: str, source_path: str, hits: list) -> str:
    def repl(m):
        begin, body, end = m.group(1), m.group(2), m.group(3)
        line = m.string.count("\n", 0, m.start()) + 1
        hits.append({"path": source_path, "line": line,
                     "pattern_type": "pem_private_key", "confidence": "high"})
        # Replace each body line's CONTENT with the marker; keep every newline so the
        # block's line count is preserved exactly.
        segs = re.split(r"(\n)", body)
        rebuilt = "".join(
            (REDACTION_MARKER if (i % 2 == 0 and seg.strip()) else seg)
            for i, seg in enumerate(segs)
        )
        return begin + rebuilt + end
    return _PEM_RE.sub(repl, content)


def redact_secrets(content: str, source_path: str) -> "tuple[str, list[dict]]":
    """Redact likely hardcoded secret VALUES in *content*; return (redacted, hits).

    hits: list of metadata dicts {path, line, pattern_type, confidence} — NEVER the
    matched value. Never raises: on any unexpected error returns (content, []) —
    fail-open on the redactor, since a crash here would block legitimate reads and the
    file-level sensitive-deny in read_scoped_file is the backstop. Line structure is
    preserved (only newline-free markers substituted; PEM bodies rebuilt line-for-line).
    """
    try:
        if not isinstance(content, str) or not content:
            return content, []
        hits: list[dict] = []
        redacted = content

        # 1. PEM blocks first (multi-line).
        redacted = _redact_pem(redacted, source_path, hits)

        # 2. TIER 1 single-line distinctive tokens.
        for ptype, rx, mode in _TIER1:
            def repl(m, ptype=ptype, mode=mode):
                line = m.string.count("\n", 0, m.start()) + 1
                hits.append({"path": source_path, "line": line,
                             "pattern_type": ptype, "confidence": "high"})
                if mode == "after_prefix":
                    return m.group(1) + REDACTION_MARKER
                return REDACTION_MARKER
            redacted = rx.sub(repl, redacted)

        # 3. TIER 2 assignment (gated on value shape — len>=16 and not a placeholder).
        def t2(m):
            value = m.group(4)
            if len(value) < _TIER2_MIN_LEN or _is_placeholder(value):
                return m.group(0)  # leave short values / placeholders untouched
            line = m.string.count("\n", 0, m.start()) + 1
            hits.append({"path": source_path, "line": line,
                         "pattern_type": "assignment:" + m.group(1).lower(),
                         "confidence": "assignment"})
            # Redact ONLY the captured value (group 4); keep name/op/quotes verbatim.
            return m.group(1) + m.group(2) + m.group(3) + REDACTION_MARKER + m.group(5)
        redacted = _TIER2_RE.sub(t2, redacted)

        return redacted, hits
    except Exception:
        return content, []


def log_redaction_hits(hits, *, context: str = "") -> None:
    """Emit metadata-only side-log entries for redaction hits. NEVER logs the value.

    Uses the project's standard ``logging`` mechanism. Never raises.
    """
    for h in hits or []:
        try:
            logger.warning(
                "[secret-redactor] redacted suspected secret "
                "context=%s path=%s line=%s type=%s confidence=%s",
                context, h.get("path"), h.get("line"),
                h.get("pattern_type"), h.get("confidence"),
            )
        except Exception:
            pass
