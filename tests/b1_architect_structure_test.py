"""B1-Arch-甲-3 test: Architect sees the project directory structure (paths only).

Verifies _build_architect_user_message injects the sandboxed project structure
(file PATHS only, NO contents) for the Architect, while:
  - excluding sensitive paths (.git/.env) from the listing,
  - degrading gracefully when no workspace_root resolves (no project_id / no row),
  - leaving the Architect's success + design output unchanged (behavior-neutral).

Hermetic: throwaway RUNTIME_DB + temp workspace; FakeProvider returns a canned
valid design and _resolve_provider is stubbed (no network/API key).

    python tests/b1_architect_structure_test.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_arch_struct_")
)

_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from dataclasses import replace  # noqa: E402
from agents.model_executor import ModelAgentExecutor  # noqa: E402
from agents.definitions import get_definition, ARCHITECT_SYSTEM_PROMPT  # noqa: E402
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
            model="fake-arch-model",
            provider="fake",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=20),
        )


VALID_ARCH = {
    "design_summary": "Thin adapter over the existing auth module",
    "components": [{"name": "Adapter", "responsibility": "wraps the client"}],
    "key_decisions": [{"decision": "reuse auth router", "rationale": "least change"}],
    "interfaces_or_contracts": ["GET /auth/callback"],
    "risks_tradeoffs": ["token refresh"],
    "summary": "Builder implements the adapter behind the existing router.",
}

PLANNER = {
    "goal_summary": "Add OAuth",
    "task_breakdown": [{"step": 1, "description": "wire oauth", "role": "builder"}],
    "acceptance_criteria": ["login works"],
}


def run_arch(task_context, canned=None):
    executor = ModelAgentExecutor()
    fake = FakeProvider(canned if canned is not None else json.dumps(VALID_ARCH))
    arch_defn = replace(get_definition(AgentRole.QA), system_prompt=ARCHITECT_SYSTEM_PROMPT)
    executor._resolve_provider = lambda role: (arch_defn, fake, "fake-arch-model")
    result = asyncio.run(executor._execute_architect(task_context))
    return result, fake


def _make_project(local_repo_path: str) -> str:
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


# ══════════════════════════════════════════════════════════════
# (a) structure injected; known path present; sensitive absent; success=True
# ══════════════════════════════════════════════════════════════
print("\n[a] project structure injected (paths only); sensitive excluded")
ws = tempfile.mkdtemp(prefix="b1_arch_ws_")
try:
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    with open(os.path.join(ws, "src", "app.py"), "w", encoding="utf-8") as f:
        f.write("print('hi')\n")
    with open(os.path.join(ws, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Proj\n")
    # Sensitive: must NOT appear in the listing.
    os.makedirs(os.path.join(ws, ".git"), exist_ok=True)
    with open(os.path.join(ws, ".git", "config"), "w", encoding="utf-8") as f:
        f.write("[core]\n")
    with open(os.path.join(ws, ".env"), "w", encoding="utf-8") as f:
        f.write("API_KEY=secret\n")

    project_id = _make_project(ws)
    ctx = {
        "title": "Add OAuth",
        "description": "Google sign-in",
        "priority": "high",
        "project_id": project_id,
        "previous_outputs": {"planner": PLANNER},
    }
    res, fake = run_arch(ctx)
    um = fake.last_request.messages[0].content

    check("a: success is True", res.success, getattr(res, "error_message", ""))
    check("a: valid design passed through", res.output.get("design_summary", "").startswith("Thin adapter"))
    check("a: 'Project structure' section present", "Project structure" in um)
    check("a: known in-workspace path present (src/app.py)", "src/app.py" in um, um[-300:])
    check("a: README.md present", "README.md" in um)
    check("a: sensitive .git path ABSENT from message", ".git" not in um, um)
    check("a: sensitive .env path ABSENT from message", ".env" not in um, um)
    check("a: Planner plan still present", "Planner plan" in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (b) no project_id → graceful: no structure block, still success
# ══════════════════════════════════════════════════════════════
print("\n[b] no project_id → graceful (no structure, still success)")
ctx_no_pid = {
    "title": "No project",
    "description": "x",
    "priority": "low",
    "previous_outputs": {"planner": PLANNER},
}
res, fake = run_arch(ctx_no_pid)
um = fake.last_request.messages[0].content
check("b: success is True", res.success)
check("b: no 'Project structure' without project_id", "Project structure" not in um)
check("b: Planner plan still present (text path intact)", "Planner plan" in um)


# ══════════════════════════════════════════════════════════════
# (c) project_id present but no projects row → graceful (resolve None)
# ══════════════════════════════════════════════════════════════
print("\n[c] project_id with no matching row → graceful")
ctx_bad_pid = {
    "title": "Ghost project",
    "description": "x",
    "priority": "low",
    "project_id": str(_uuid.uuid4()),  # not inserted
    "previous_outputs": {"planner": PLANNER},
}
res, fake = run_arch(ctx_bad_pid)
um = fake.last_request.messages[0].content
check("c: success is True", res.success)
check("c: no 'Project structure' for unknown project", "Project structure" not in um)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 architect structure: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
