"""TASK 甲 tests: content-level secret redactor (unit) + B1 injection-layer wiring.

Unit (pure, no DB/workspace): every TIER-1 pattern redacts to [REDACTED-SECRET] with
the line-count-preservation invariant and the raw secret absent; TIER-2 real
assignments redact while placeholders/short values do not; clean code is BYTE-IDENTICAL
(zero false positives, incl. a UUID + a base64-ish non-secret); hits metadata carries
path/line/pattern_type/confidence and NEVER the value.

Integration (hermetic; throwaway DB + temp workspace): a TIER-1 secret in a workspace
file is read via the architect 乙-3a facade path AND the builder taskfile path, and the
injected block contains [REDACTED-SECRET], not the raw secret.

NOTE: every secret-shaped fixture below is ASSEMBLED FROM FRAGMENTS via ``_S(...)`` at
runtime, so the committed source file contains NO literal secret (this avoids GitHub
push-protection false positives on test fixtures). The redactor still receives the
fully-assembled token at runtime, so detection is exercised exactly.

    python tests/secret_redactor_test.py
"""
import os
import re
import shutil
import sys
import tempfile
import json
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="secret_redactor_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.secret_redactor import redact_secrets, REDACTION_MARKER  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  -- {detail}" if detail else ""))


def _S(*parts):
    """Assemble a secret-shaped token from fragments at runtime — the committed source
    holds no literal secret (avoids push-protection); the redactor sees the full token."""
    return "".join(parts)


# Fragment-assembled fakes (no literal secret in the committed blob).
AWS_FAKE = _S("AK", "IA", "ABCDEFGHIJKLMNOP")                       # AKIA + 16
GH_FAKE = _S("gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")    # ghp_ + 36
SLACK_FAKE = _S("xo", "xb-", "123456789012-abcdefghijklmno")        # xoxb- + ...
ANTHROPIC_FAKE = _S("sk-", "ant-", "api03AbCdEf1234567890XyZ12345")  # sk-ant- + 20+
OPENAI_FAKE = _S("sk-", "AbCdEf1234567890XyZ12345")                 # sk- + 20+
GOOGLE_FAKE = _S("AI", "za", "a" * 35)                             # AIza + 35
BEARER_TOKEN = _S("abcdefghij", "1234567890XYZ")                    # generic 20+ token
TIER2_REAL = _S("A1b2C3d4E5", "f6G7h8X9j0")                        # len 20 assignment value


# ══════════════════════════════════════════════════════════════
# (a) TIER-1 patterns: redacted, line-count preserved, raw absent
# ══════════════════════════════════════════════════════════════
print("\n[a] TIER-1 distinctive tokens")
TIER1 = [
    ("aws_access_key_id", AWS_FAKE, f'AWS_KEY = "{AWS_FAKE}"'),
    ("github_token", GH_FAKE, f'tok = "{GH_FAKE}"'),
    ("slack_token", SLACK_FAKE, f'slack = "{SLACK_FAKE}"'),
    ("anthropic_key", ANTHROPIC_FAKE, f'k = "{ANTHROPIC_FAKE}"'),
    ("openai_key", OPENAI_FAKE, f'k = "{OPENAI_FAKE}"'),
    ("google_api_key", GOOGLE_FAKE, f'g = "{GOOGLE_FAKE}"'),
    ("bearer_token", BEARER_TOKEN, f'h = "Authorization: Bearer {BEARER_TOKEN}"'),
]
for ptype, token, line in TIER1:
    content = f"line1\n{line}\nline3\n"
    red, hits = redact_secrets(content, f"f_{ptype}.py")
    check(f"{ptype}: marker present", REDACTION_MARKER in red, red)
    check(f"{ptype}: raw token absent", token not in red, red)
    check(f"{ptype}: line count preserved", content.count("\n") == red.count("\n"),
          f"{content.count(chr(10))} vs {red.count(chr(10))}")
    check(f"{ptype}: a hit recorded", len(hits) >= 1, str(hits))

# PEM (multi-line) — body redacted line-for-line, line count preserved
_BEGIN = _S("-----BEGIN ", "RSA PRIVATE KEY", "-----")
_END = _S("-----END ", "RSA PRIVATE KEY", "-----")
_BODY = _S("MIIEowIBAAKCAQEA", "1234567890abcdef")
pem = f"before\n{_BEGIN}\n{_BODY}\nGHIJKLMNOPQRSTUVWXYZ0123456789ab\n{_END}\nafter\n"
red, hits = redact_secrets(pem, "key.pem")
check("PEM: line count preserved", pem.count("\n") == red.count("\n"),
      f"{pem.count(chr(10))} vs {red.count(chr(10))}")
check("PEM: BEGIN/END markers kept", _BEGIN in red and _END in red)
check("PEM: body redacted", REDACTION_MARKER in red and _BODY not in red, red)
check("PEM: hit type pem_private_key", any(h["pattern_type"] == "pem_private_key" for h in hits), str(hits))


# ══════════════════════════════════════════════════════════════
# (b) TIER-2 assignment: real redacted, placeholders not
# ══════════════════════════════════════════════════════════════
print("\n[b] TIER-2 assignment gating")
real_line = f'api_key = "{TIER2_REAL}"\n'
red, hits = redact_secrets(real_line, "c.py")
check("real api_key value redacted", REDACTION_MARKER in red and TIER2_REAL not in red, red)
check("real api_key keeps name+quotes", red.startswith('api_key = "') and red.rstrip().endswith('"'), red)
check("real api_key confidence=assignment", any(h["confidence"] == "assignment" for h in hits), str(hits))

for label, line in [
    ("your-placeholder", 'api_key = "your-key-here"'),
    ("xxx", 'token = "xxx"'),
    ("env-interp", 'password = "${ENV_PW}"'),
    ("test", 'secret = "test"'),
    ("template-angle", 'token = "<your_token_here>"'),
    ("short-value", 'api_key = "short123"'),  # len < 16
]:
    red, hits = redact_secrets(line + "\n", "c.py")
    check(f"placeholder NOT redacted: {label}", REDACTION_MARKER not in red and red == line + "\n", red)


# ══════════════════════════════════════════════════════════════
# (c) clean code → BYTE-IDENTICAL (zero false positives)
# ══════════════════════════════════════════════════════════════
print("\n[c] clean code byte-identical")
clean = (
    "# a normal module — no secrets\n"
    "import uuid\n"
    'ID = "550e8400-e29b-41d4-a716-446655440000"  # a UUID, not a secret\n'
    'data = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"  # base64-ish, non-secret var\n'
    # NB: this value contains the substring "sk-..."(20+) inside "task-..."; the redactor's
    # lookbehind must leave it untouched. Assembled via _S so the committed blob holds no
    # contiguous "sk-[20+]" run (push-protection safe); runtime value is the full word.
    "TASK_NAME = '" + _S("task-relevant-file-sele", "ction-component-helper") + "'\n"
    "def add(a, b):\n"
    "    return a + b\n"
)
red, hits = redact_secrets(clean, "clean.py")
check("clean: byte-identical output", red == clean)
check("clean: no hits", hits == [], str(hits))
check("clean: marker absent", REDACTION_MARKER not in red)


# ══════════════════════════════════════════════════════════════
# (d) hits metadata shape + NEVER the value
# ══════════════════════════════════════════════════════════════
print("\n[d] hits metadata (path/line/type/confidence; never the value)")
red, hits = redact_secrets(f'x\ny\nAWS = "{AWS_FAKE}"\n', "meta.py")
check("d: at least one hit", len(hits) >= 1, str(hits))
h0 = hits[0] if hits else {}
check("d: has path", h0.get("path") == "meta.py")
check("d: has line (3)", h0.get("line") == 3, str(h0))
check("d: has pattern_type", isinstance(h0.get("pattern_type"), str) and h0.get("pattern_type"))
check("d: has confidence", h0.get("confidence") in ("high", "assignment"))
check("d: hits NEVER contain the raw value", AWS_FAKE not in json.dumps(hits), json.dumps(hits))


# ══════════════════════════════════════════════════════════════
# (e) integration: redaction at facade (乙-3a) + builder injection points
# ══════════════════════════════════════════════════════════════
print("\n[e] integration: redaction at the B1 injection layer")
from agents.model_executor import ModelAgentExecutor as M  # noqa: E402
from database import get_connection, init_db  # noqa: E402
init_db()


def _make_project(ws):
    pid = str(_uuid.uuid4()); now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "sec", ws, "main", "", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid


def _write(ws, rel, content):
    full = os.path.join(ws, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


# facade path: secret lives in main.py (a facade candidate)
ws = tempfile.mkdtemp(prefix="sec_facade_")
try:
    _write(ws, "README.md", "# Proj\n")
    _write(ws, "main.py", f'API = "{AWS_FAKE}"\nfrom fastapi import FastAPI\napp = FastAPI()\n')
    pid = _make_project(ws)
    ctx = {"title": "t", "description": "d", "priority": "low", "project_id": pid, "previous_outputs": {}}
    fblock, fpaths, fused = M._build_architect_facade_context(ctx)
    check("e-facade: main.py included", "main.py" in fpaths, str(fpaths))
    check("e-facade: marker injected", bool(fblock) and REDACTION_MARKER in fblock)
    check("e-facade: raw AWS secret NOT in block", bool(fblock) and AWS_FAKE not in fblock)
finally:
    shutil.rmtree(ws, ignore_errors=True)

# builder path: secret in src/models.py, named by architect interfaces
ws = tempfile.mkdtemp(prefix="sec_builder_")
try:
    _write(ws, "src/models.py", f'AWS_KEY = "{AWS_FAKE}"\nclass Task:\n    pass\n')
    pid = _make_project(ws)
    ctx = {"title": "t", "description": "d", "priority": "low", "project_id": pid,
           "previous_outputs": {"architect": {
               "components": [{"name": "Task", "responsibility": "model", "interfaces": "Task in src/models.py"}],
               "interfaces_or_contracts": ["src/models.py Task model"],
               "key_decisions": [], "risks_tradeoffs": [], "design_summary": "d", "summary": "s",
           }}}
    bblock = M._build_builder_taskfile_context(ctx, set(), 0)
    check("e-builder: src/models.py selected", bool(bblock) and "--- src/models.py ---" in bblock, str(bblock))
    check("e-builder: marker injected", bool(bblock) and REDACTION_MARKER in bblock)
    check("e-builder: raw AWS secret NOT in block", bool(bblock) and AWS_FAKE not in bblock)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  secret_redactor: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
