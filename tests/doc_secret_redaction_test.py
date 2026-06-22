"""Documentation secret-redaction wiring test (closes the last B1 exposure gap).

Asserts that _build_doc_modify_file_context (documentation's OWN read path for the
Builder's modify-target content) now redacts secrets via the shared redactor:
  (1) a modify-target file containing a TIER-1 secret -> injected block has
      [REDACTED-SECRET] and NOT the raw secret;
  (2) a clean modify-target file -> injected BYTE-IDENTICAL (zero false positive);
  (3) end-to-end: the redacted block appears in _build_documentation_user_message.

Hermetic: throwaway RUNTIME_DB + temp workspace via a projects row; no provider/app/real-DB.
The secret is ASSEMBLED FROM FRAGMENTS via _S(...) so the committed source holds no
literal secret (push-protection safe), exactly as the TASK 甲 test does.

    python tests/doc_secret_redaction_test.py
"""
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault("RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="doc_secret_"))
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor as M  # noqa: E402
from agents.secret_redactor import REDACTION_MARKER  # noqa: E402
from database import get_connection, init_db  # noqa: E402

init_db()

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
    return "".join(parts)


def _make_project(ws):
    pid = str(_uuid.uuid4()); now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO projects (id,name,local_repo_path,default_branch,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (pid, "doc", ws, "main", "", now, now))
        conn.commit()
    finally:
        conn.close()
    return pid


def _write(ws, rel, content):
    full = os.path.join(ws, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def _builder_ctx(pid, target_path):
    return {
        "title": "t", "description": "d", "priority": "low", "project_id": pid,
        "previous_outputs": {"builder": {
            "change_summary": "edit", "proposed_files": [
                {"path": target_path, "action": "modify", "reason": "x", "content": "new content"},
            ],
        }},
    }


AWS_FAKE = _S("AK", "IA", "ABCDEFGHIJKLMNOP")  # no literal secret in the committed source


# ══════════════════════════════════════════════════════════════
# (1) secret in a modify-target → redacted in the documentation block
# ══════════════════════════════════════════════════════════════
print("\n[1] secret modify-target redacted")
ws = tempfile.mkdtemp(prefix="doc_sec_")
try:
    _write(ws, "src/models.py", f'AWS_KEY = "{AWS_FAKE}"\nclass Task:\n    pass\n')
    pid = _make_project(ws)
    block = M._build_doc_modify_file_context(_builder_ctx(pid, "src/models.py"))
    check("1: block produced", bool(block), str(block))
    check("1: header for the modify target present", bool(block) and "--- Current content of src/models.py ---" in block)
    check("1: [REDACTED-SECRET] marker injected", bool(block) and REDACTION_MARKER in block)
    check("1: raw AWS secret NOT in block", bool(block) and AWS_FAKE not in block)
    check("1: clean code around the secret survives", bool(block) and "class Task:" in block)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (2) clean modify-target → injected BYTE-IDENTICAL (zero false positive)
# ══════════════════════════════════════════════════════════════
print("\n[2] clean modify-target byte-identical (no false positive)")
ws = tempfile.mkdtemp(prefix="doc_clean_")
try:
    clean = "def helper():\n    return 'CLEAN-DOC-MARKER'\n"
    _write(ws, "src/utils.py", clean)
    pid = _make_project(ws)
    block = M._build_doc_modify_file_context(_builder_ctx(pid, "src/utils.py"))
    check("2: block produced", bool(block))
    check("2: clean content injected verbatim (byte-identical)", bool(block) and clean in block, str(block))
    check("2: no redaction marker on clean content", bool(block) and REDACTION_MARKER not in block)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (3) end-to-end: redacted block flows into the documentation user message
# ══════════════════════════════════════════════════════════════
print("\n[3] redaction visible end-to-end in the documentation prompt")
ws = tempfile.mkdtemp(prefix="doc_e2e_")
try:
    _write(ws, "src/models.py", f'AWS_KEY = "{AWS_FAKE}"\nclass Task:\n    pass\n')
    pid = _make_project(ws)
    um = M._build_documentation_user_message(_builder_ctx(pid, "src/models.py"))
    check("3: documentation content section present", "Current content of files to be documented" in um)
    check("3: [REDACTED-SECRET] in the documentation prompt", REDACTION_MARKER in um)
    check("3: raw secret NOT in the documentation prompt", AWS_FAKE not in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
print()
print("-" * 60)
total = PASS + FAIL
print(f"  doc secret redaction: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
