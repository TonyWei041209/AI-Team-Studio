"""B1-d test: Documentation role reads modify-target file content (sandboxed).

Verifies that _build_documentation_user_message pre-fetches the CURRENT on-disk
content of the Builder's proposed_files where action == "modify", via the
sandboxed reader, and injects it into the model prompt — while:
  - NOT injecting create-targets (they don't exist yet),
  - holding the sandbox (a ../ modify target outside the workspace is NOT read),
  - degrading gracefully when no workspace_root resolves,
  - leaving the role's success + proposal output unchanged (behavior-neutral).

Hermetic: a throwaway RUNTIME_DB + a temp workspace; a FakeProvider returns a
canned valid proposal and _resolve_provider is stubbed (no network/API key).

    python tests/b1_documentation_reads_test.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

# Throwaway DB BEFORE importing the runtime (database resolves its path at import).
os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_doc_reads_")
)

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from dataclasses import replace  # noqa: E402
from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition, DOCUMENTATION_SYSTEM_PROMPT  # noqa: E402
from models import AgentRole  # noqa: E402
from providers.base import CompletionResponse, TokenUsage  # noqa: E402
from database import get_connection, init_db  # noqa: E402

init_db()

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        msg = f"  [FAIL] {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


class FakeProvider:
    def __init__(self, canned_content):
        self._canned = canned_content
        self.api_key = "fake-test-key"
        self.last_request = None

    async def complete(self, request):
        self.last_request = request
        return CompletionResponse(
            content=self._canned,
            model="fake-doc-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
        )


VALID_DOC = {
    "change_summary": "Document the utils change",
    "proposed_files": [
        {
            "path": "docs/utils.md",
            "action": "create",
            "reason": "document the helper",
            "content": "# Utils\n\nDescribes the helper.\n",
        }
    ],
    "change_steps": [{"step": 1, "description": "Write docs/utils.md"}],
    "reasoning_summary": "Grounded in the modified utils.py",
    "validation_plan": ["A human checks docs/utils.md matches utils.py"],
}


def run_doc(task_context, canned=None):
    """Invoke _execute_documentation with a fake provider (stubbed resolver)."""
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned if canned is not None else json.dumps(VALID_DOC))
    doc_defn = replace(get_definition(AgentRole.QA), system_prompt=DOCUMENTATION_SYSTEM_PROMPT)
    executor._resolve_provider = lambda role: (doc_defn, fake, "fake-doc-model")
    result = asyncio.run(executor._execute_documentation(task_context))
    return result, fake


def _make_project(local_repo_path: str) -> str:
    """Insert a project row pointing at local_repo_path; return project_id."""
    project_id = str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (project_id, f"b1-{project_id[:8]}", local_repo_path, "main", "", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return project_id


KNOWN_CONTENT = "def helper():\n    return 'ORIGINAL-MARKER-7f3a'\n"


# ══════════════════════════════════════════════════════════════
# (a) modify target inside workspace → current content injected;
#     create target NOT injected; role still success=True w/ proposal
# ══════════════════════════════════════════════════════════════
print("\n[a] modify-target content injected; create-target not injected")
ws = tempfile.mkdtemp(prefix="b1_doc_ws_")
try:
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    with open(os.path.join(ws, "src", "utils.py"), "w", encoding="utf-8") as f:
        f.write(KNOWN_CONTENT)

    project_id = _make_project(ws)
    ctx = {
        "title": "Refactor utils",
        "description": "Improve helper",
        "priority": "medium",
        "project_id": project_id,
        "previous_outputs": {
            "planner": {"goal_summary": "Refactor", "acceptance_criteria": ["helper works"]},
            "builder": {
                "change_summary": "edit utils + add new",
                "proposed_files": [
                    {"path": "src/utils.py", "action": "modify", "reason": "improve", "content": "new"},
                    {"path": "src/new.py", "action": "create", "reason": "add", "content": "new file"},
                ],
            },
        },
    }
    res, fake = run_doc(ctx)
    um = fake.last_request.messages[0].content

    check("a: success is True", res.success, getattr(res, "error_message", ""))
    check("a: valid proposal passed through (not skipped)", res.output.get("skipped") is not True)
    check("a: proposed_files present", len(res.output.get("proposed_files", [])) == 1)
    check("a: injected-content section header present",
          "Current content of files to be documented" in um)
    check("a: modify-target header present", "--- Current content of src/utils.py ---" in um)
    check("a: actual on-disk content injected", "ORIGINAL-MARKER-7f3a" in um, um[-300:])
    check("a: create-target NOT injected (no header for src/new.py)",
          "--- Current content of src/new.py ---" not in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (b) sandbox holds: a ../ modify target OUTSIDE the workspace is NOT read
# ══════════════════════════════════════════════════════════════
print("\n[b] ../ modify target outside workspace is NOT injected")
parent = tempfile.mkdtemp(prefix="b1_doc_esc_")
try:
    ws_b = os.path.join(parent, "workspace")
    os.makedirs(ws_b, exist_ok=True)
    # A secret just outside the workspace the model must never see.
    with open(os.path.join(parent, "outside.txt"), "w", encoding="utf-8") as f:
        f.write("ESCAPE-SECRET-DO-NOT-LEAK\n")

    project_id = _make_project(ws_b)
    ctx = {
        "title": "Sneaky",
        "description": "x",
        "priority": "low",
        "project_id": project_id,
        "previous_outputs": {
            "builder": {
                "change_summary": "edit outside",
                "proposed_files": [
                    {"path": "../outside.txt", "action": "modify", "reason": "x", "content": "y"},
                ],
            },
        },
    }
    res, fake = run_doc(ctx)
    um = fake.last_request.messages[0].content

    check("b: success is True", res.success)
    check("b: escape secret NOT leaked into prompt", "ESCAPE-SECRET-DO-NOT-LEAK" not in um)
    check("b: no injected-content section (every read denied)",
          "Current content of files to be documented" not in um)
finally:
    shutil.rmtree(parent, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (c) no resolvable workspace_root → graceful: no file block, still success
# ══════════════════════════════════════════════════════════════
print("\n[c] no workspace_root (no project_id) → graceful degradation")
ctx_no_ws = {
    "title": "No project",
    "description": "x",
    "priority": "low",
    # no project_id
    "previous_outputs": {
        "builder": {
            "change_summary": "edit",
            "proposed_files": [
                {"path": "src/utils.py", "action": "modify", "reason": "x", "content": "y"},
            ],
        },
    },
}
res, fake = run_doc(ctx_no_ws)
um = fake.last_request.messages[0].content
check("c: success is True", res.success)
check("c: no injected-content section without workspace_root",
      "Current content of files to be documented" not in um)
check("c: Builder proposal still present (text-only path intact)", "Builder proposal" in um)


# ══════════════════════════════════════════════════════════════
# (d) builder present but NO modify targets → no file block (behavior-neutral)
# ══════════════════════════════════════════════════════════════
print("\n[d] builder with only create-targets → no file block")
ws_d = tempfile.mkdtemp(prefix="b1_doc_create_")
try:
    project_id = _make_project(ws_d)
    ctx = {
        "title": "Creates only",
        "description": "x",
        "priority": "low",
        "project_id": project_id,
        "previous_outputs": {
            "builder": {
                "change_summary": "create only",
                "proposed_files": [
                    {"path": "src/new.py", "action": "create", "reason": "x", "content": "z"},
                ],
            },
        },
    }
    res, fake = run_doc(ctx)
    um = fake.last_request.messages[0].content
    check("d: success is True", res.success)
    check("d: no injected-content section when no modify targets",
          "Current content of files to be documented" not in um)
finally:
    shutil.rmtree(ws_d, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 documentation reads: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
