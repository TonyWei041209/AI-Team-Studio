"""B1-Builder test: existing-file CONTENT selection for the Builder (方向 A).

Asserts on a synthetic MiniTaskQueue-like workspace + ctx whose ARCHITECT output's
components/interfaces reference the real files:
  (a) existing implementation files (src/models.py, src/queue_manager.py) ARE selected
      via the architect-component/interface signal, with their real current content;
  (b) a component naming a NON-EXISTENT file (PriorityScheduler -> src/priority_scheduler.py,
      not created) is NOT in the output (scored, but read-skipped by existence);
  (c) reasoning-only fields are excluded: src/config.py is named ONLY in architect
      key_decisions + risks_tradeoffs and is NOT selected;
  (d) the block label is "Existing files this change will likely modify (content)" and
      appears AFTER the architect block; builder injects NO structure/facade blocks.

Hermetic: throwaway RUNTIME_DB + temp workspace via a projects row; NO provider/app/real-DB.

    python tests/b1_builder_taskfile_test.py
"""
import os
import re
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_builder_tf_")
)
_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
if os.path.abspath(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, os.path.abspath(_RUNTIME_DIR))

from agents.model_executor import ModelAgentExecutor as M  # noqa: E402
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


def _make_project(ws: str) -> str:
    pid = str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (pid, f"b1-{pid[:8]}", ws, "main", "", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _write(ws: str, rel: str, content: str) -> None:
    full = os.path.join(ws, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def _selected(block):
    return re.findall(r"--- (.+?)(?: \(truncated\))? ---", block) if block else []


# Architect output: components/interfaces reference REAL files; a non-existent file is
# named by a component; config.py is named ONLY in excluded reasoning fields.
ARCH = {
    "design_summary": "Add task prioritization via a Priority enum and per-priority queues.",
    "components": [
        {"name": "Task", "responsibility": "task model", "interfaces": "Task in src/models.py gains a priority field"},
        {"name": "TaskQueue", "responsibility": "queue", "interfaces": "TaskQueue.dequeue in src/queue_manager.py becomes priority-ordered"},
        {"name": "TaskStatus", "responsibility": "status enum"},
        {"name": "PriorityScheduler", "responsibility": "new scheduler", "interfaces": "new module src/priority_scheduler.py"},
    ],
    "key_decisions": [
        {"decision": "store the priority default in src/config.py", "rationale": "central config", "alternatives": "env var"},
    ],
    "interfaces_or_contracts": [
        "src/models.py exposes Task.priority: Priority",
        "src/queue_manager.py TaskQueue.dequeue returns the highest-priority task first",
    ],
    "risks_tradeoffs": ["src/config.py may need a new flag for default priority"],
    "summary": "Builder edits the model and the queue to support priority.",
}
PLANNER = {
    "goal_summary": "Implement task prioritization (high/normal/low).",
    "task_breakdown": [{"step": 1, "description": "add priority support", "role": "builder"}],
    "acceptance_criteria": ["high-priority tasks dequeued first"],
}

print("\n[A] builder selects existing modify-targets via architect signal")
ws = tempfile.mkdtemp(prefix="b1_btf_a_")
try:
    _write(ws, "README.md", "# MiniTaskQueue\n")
    _write(ws, "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    _write(ws, "pyproject.toml", "[project]\nname = 'minitaskqueue'\n")
    _write(ws, "src/__init__.py", '"""package"""\n')
    _write(ws, "src/models.py", "class Task:  # MODELS-MARKER\n    pass\n")
    _write(ws, "src/queue_manager.py", "class TaskQueue:  # QUEUE-MARKER\n    def dequeue(self):\n        ...\n")
    _write(ws, "src/config.py", "DEFAULT_PRIORITY = 'normal'  # CONFIG-MARKER\n")  # exists, but named only in excluded fields
    _write(ws, "tests/test_queue.py", "def test_x():\n    pass\n")
    # NOTE: src/priority_scheduler.py is deliberately NOT created.

    pid = _make_project(ws)
    ctx = {
        "title": "MiniTaskQueue priority feature", "description": "add task priority",
        "priority": "medium", "project_id": pid,
        "previous_outputs": {"planner": PLANNER, "architect": ARCH},
    }

    tblock = M._build_builder_taskfile_context(ctx, set(), 0)
    sel = _selected(tblock)

    check("A: src/models.py selected (architect-component/interface signal)", "src/models.py" in sel, str(sel))
    check("A: src/queue_manager.py selected", "src/queue_manager.py" in sel, str(sel))
    check("A: real current content injected (model + queue markers)",
          bool(tblock) and "MODELS-MARKER" in tblock and "QUEUE-MARKER" in tblock)
    check("A(b): non-existent src/priority_scheduler.py NOT in output (scored, read-skipped)",
          "src/priority_scheduler.py" not in sel, str(sel))
    check("A(b): its content never appears", not (tblock and "priority_scheduler" in tblock and "--- src/priority_scheduler.py ---" in tblock))
    check("A(c): src/config.py NOT selected (named only in excluded key_decisions/risks_tradeoffs)",
          "src/config.py" not in sel, str(sel))
    check("A(c): config content (CONFIG-MARKER) absent from block", not (tblock and "CONFIG-MARKER" in tblock))
    check("A: src/__init__.py NOT selected (min-score threshold)", "src/__init__.py" not in sel, str(sel))

    # (d) block label + order via the full builder user message
    um = M._build_builder_user_message(ctx)
    label = "Existing files this change will likely modify (content)"
    check("A(d): builder block label present", label in um)
    check("A(d): label appears AFTER the Architect block",
          "Architect design" in um and um.index("Architect design") < um.index(label))
    check("A(d): builder injects NO 甲 structure block", "Project structure (file paths" not in um)
    check("A(d): builder injects NO 乙-3a facade block", "Key project files (content)" not in um)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 builder taskfile: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
