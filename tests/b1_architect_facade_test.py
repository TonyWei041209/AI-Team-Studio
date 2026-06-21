"""B1-Arch-乙-3a test: Architect reads CONTENT of a small fixed set of facade files.

Verifies _build_architect_facade_context injects the content of README + manifests
+ entry-point source (via the sandboxed read_scoped_file) while:
  - NOT reading sensitive files (.env content must never appear),
  - truncating a large facade file to the per-file cap (with a "(truncated)" marker),
  - bounding the whole block by the total-char cap (with a budget note),
  - skipping missing candidates gracefully,
  - degrading to no-content when no workspace_root resolves,
  - leaving the Architect's success + design output unchanged (behavior-neutral).

Hermetic: throwaway RUNTIME_DB + temp workspace; FakeProvider returns a canned
valid design and _resolve_provider is stubbed (no network/API key).

    python tests/b1_architect_facade_test.py
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
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_arch_facade_")
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
            content=self._canned, model="fake-arch-model", provider="fake",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=20),
        )


VALID_ARCH = {
    "design_summary": "Thin adapter over the existing module",
    "components": [{"name": "Adapter", "responsibility": "wraps the client"}],
    "key_decisions": [{"decision": "reuse router", "rationale": "least change"}],
    "interfaces_or_contracts": ["GET /x"],
    "risks_tradeoffs": ["token refresh"],
    "summary": "Builder implements the adapter.",
}
PLANNER = {
    "goal_summary": "Add feature",
    "task_breakdown": [{"step": 1, "description": "do", "role": "builder"}],
    "acceptance_criteria": ["works"],
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


def _write(ws, rel, content):
    full = os.path.join(ws, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


# ══════════════════════════════════════════════════════════════
# (a) facade content injected; .env secret NOT read; missing skipped
# ══════════════════════════════════════════════════════════════
print("\n[a] facade content injected; .env NOT read; missing skipped")
ws = tempfile.mkdtemp(prefix="b1_facade_a_")
try:
    _write(ws, "README.md", "# MyProj\nREADME-MARKER-aaa\n")
    _write(ws, "package.json", '{"name": "PKG-MARKER-bbb"}\n')
    _write(ws, "main.py", "print('MAIN-MARKER-ccc')\n")
    _write(ws, ".env", "API_KEY=ENV-SECRET-zzz\n")  # sensitive — must NOT be read

    project_id = _make_project(ws)
    ctx = {"title": "T", "description": "D", "priority": "high",
           "project_id": project_id, "previous_outputs": {"planner": PLANNER}}
    res, fake = run_arch(ctx)
    um = fake.last_request.messages[0].content

    check("a: success is True", res.success, getattr(res, "error_message", ""))
    check("a: valid design passed through", res.output.get("design_summary", "").startswith("Thin adapter"))
    check("a: 'Key project files (content)' header present", "Key project files (content)" in um)
    check("a: README content injected", "README-MARKER-aaa" in um)
    check("a: package.json content injected", "PKG-MARKER-bbb" in um)
    check("a: main.py (entry-point source) content injected", "MAIN-MARKER-ccc" in um)
    check("a: README header present", "--- README.md ---" in um)
    check("a: .env SECRET content NOT present (sandbox holds for content reads)",
          "ENV-SECRET-zzz" not in um, "secret leaked!")
    check("a: missing facade (Cargo.toml) skipped", "--- Cargo.toml ---" not in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (b) per-file cap truncates a large README (truncation marker; bounded)
# ══════════════════════════════════════════════════════════════
print("\n[b] per-file cap truncates a large README")
ws = tempfile.mkdtemp(prefix="b1_facade_b_")
try:
    big_readme = "RDME-START-marker\n" + ("A" * 10_000) + "\nRDME-END-marker-zzz\n"
    _write(ws, "README.md", big_readme)
    _write(ws, "package.json", '{"name": "PKG-small-bbb"}\n')

    project_id = _make_project(ws)
    ctx = {"title": "T", "description": "D", "priority": "low",
           "project_id": project_id, "previous_outputs": {"planner": PLANNER}}
    res, fake = run_arch(ctx)
    um = fake.last_request.messages[0].content

    check("b: success is True", res.success)
    check("b: README header marked (truncated)", "--- README.md (truncated) ---" in um)
    check("b: README start present", "RDME-START-marker" in um)
    check("b: README end TRUNCATED OFF (beyond per-file cap)", "RDME-END-marker-zzz" not in um)
    check("b: smaller package.json still fully injected", "PKG-small-bbb" in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (c) total cap bounds the block (budget note appears; later file dropped)
# ══════════════════════════════════════════════════════════════
print("\n[c] total cap bounds the whole facade block")
ws = tempfile.mkdtemp(prefix="b1_facade_c_")
try:
    _write(ws, "README.md", "RDME6K-start\n" + ("A" * 7000))
    _write(ws, "pyproject.toml", "PYPROJ6K-start\n" + ("B" * 7000))
    _write(ws, "package.json", "PKG6K-start\n" + ("C" * 7000))
    _write(ws, "main.py", "MAIN-START-BLOCKED\n" + ("D" * 7000))

    project_id = _make_project(ws)
    ctx = {"title": "T", "description": "D", "priority": "low",
           "project_id": project_id, "previous_outputs": {"planner": PLANNER}}
    res, fake = run_arch(ctx)
    um = fake.last_request.messages[0].content

    check("c: success is True", res.success)
    check("c: total-cap budget note present",
          "additional file content truncated to respect the context budget" in um)
    check("c: README included", "RDME6K-start" in um)
    check("c: pyproject included", "PYPROJ6K-start" in um)
    check("c: package included", "PKG6K-start" in um)
    check("c: main.py dropped by total cap (not included)", "MAIN-START-BLOCKED" not in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# (d) graceful: no project_id, and a project_id with no row → no content block
# ══════════════════════════════════════════════════════════════
print("\n[d] graceful degradation without a resolvable workspace_root")
ctx_no_pid = {"title": "T", "description": "D", "priority": "low",
              "previous_outputs": {"planner": PLANNER}}
res, fake = run_arch(ctx_no_pid)
um = fake.last_request.messages[0].content
check("d: success is True (no project_id)", res.success)
check("d: no facade block without project_id", "Key project files (content)" not in um)
check("d: Planner plan still present", "Planner plan" in um)

ctx_ghost = {"title": "T", "description": "D", "priority": "low",
             "project_id": str(_uuid.uuid4()), "previous_outputs": {"planner": PLANNER}}
res, fake = run_arch(ctx_ghost)
um = fake.last_request.messages[0].content
check("d: success is True (unknown project)", res.success)
check("d: no facade block for unknown project", "Key project files (content)" not in um)


# ══════════════════════════════════════════════════════════════
# (e) prompt carries the privacy-mitigation + content lines
# ══════════════════════════════════════════════════════════════
print("\n[e] ARCHITECT_SYSTEM_PROMPT carries content + privacy lines")
check("e: prompt allows seeing key-file CONTENT",
      "CONTENT of a few key project files" in ARCHITECT_SYSTEM_PROMPT)
check("e: prompt has the privacy-mitigation line (don't reproduce secrets)",
      "do NOT reproduce those values in your design output" in ARCHITECT_SYSTEM_PROMPT)
check("e: prompt KEEPS anti-fabrication core",
      "MUST NOT fabricate existing code structure" in ARCHITECT_SYSTEM_PROMPT)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 architect facade: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
