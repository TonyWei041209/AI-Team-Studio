"""B1-Arch-乙-3b test: task-relevant file CONTENT selection for the Architect.

Asserts: (a) the real implementation files (src/models.py, src/queue_manager.py)
ARE selected; (b) src/__init__.py is NOT selected (min-score threshold excludes
symbol-guess-only matches, e.g. "Init" ⊂ "minitaskqueue"); (c) README/main/
pyproject are NOT re-included by 乙-3b (dedup against 乙-3a); (d) block order is
planner → structure → facade → task-relevant; (e) 乙-3b draws ONLY from the budget
乙-3a left (shared _ARCH_FACADE_TOTAL_CAP) — when 乙-3a exhausts it, 乙-3b injects
nothing rather than opening a fresh budget.

Hermetic: throwaway RUNTIME_DB + temp workspace; NO provider calls / app / real DB.

    python tests/b1_architect_taskfile_test.py
"""
import os
import re
import shutil
import sys
import tempfile
import uuid as _uuid
from datetime import datetime, timezone

os.environ.setdefault(
    "RUNTIME_DB", tempfile.mktemp(suffix=".db", prefix="b1_taskfile_")
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


def _selected(tblock):
    return re.findall(r"--- (.+?)(?: \(truncated\))? ---", tblock) if tblock else []


PLANNER = {
    "goal_summary": "Implement task prioritization in MiniTaskQueue.",
    "task_breakdown": [
        {"step": 1, "description": "Add a priority field to the Task model in src/models.py", "role": "builder"},
        {"step": 2, "description": "Refactor TaskQueue.dequeue in src/queue_manager.py to honor priority", "role": "builder"},
        {"step": 3, "description": "Expose a priority filter endpoint in main.py", "role": "builder"},
    ],
    "acceptance_criteria": ["high-priority tasks are dequeued first"],
}


# ══════════════════════════════════════════════════════════════
# [A] selection + min-score threshold + dedup + block order
# ══════════════════════════════════════════════════════════════
print("\n[A] selection / min-score threshold / dedup / order")
ws = tempfile.mkdtemp(prefix="b1_tf_a_")
try:
    _write(ws, "README.md", "# MiniTaskQueue\nA tiny in-memory task queue.\n")
    _write(ws, "pyproject.toml", "[project]\nname = 'minitaskqueue'\n")
    _write(ws, "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    _write(ws, "src/__init__.py", '"""package"""\n')
    _write(ws, "src/models.py", "class Task:\n    pass\n")
    _write(ws, "src/queue_manager.py", "class TaskQueue:\n    def dequeue(self):\n        ...\n")
    _write(ws, "tests/test_queue.py", "def test_x():\n    pass\n")

    pid = _make_project(ws)
    ctx = {
        "title": "MiniTaskQueue", "description": "add task priority", "priority": "medium",
        "project_id": pid, "previous_outputs": {"planner": PLANNER},
    }

    fblock, fpaths, fused = M._build_architect_facade_context(ctx)
    tblock = M._build_architect_taskfile_context(ctx, fpaths, fused)
    sel = _selected(tblock)

    check("A: src/models.py selected", "src/models.py" in sel, str(sel))
    check("A: src/queue_manager.py selected", "src/queue_manager.py" in sel, str(sel))
    check("A: src/__init__.py NOT selected (min-score threshold excludes symbol-only)",
          "src/__init__.py" not in sel, str(sel))
    check("A: README/main/pyproject NOT re-included by 乙-3b (dedup vs 乙-3a)",
          all(f not in sel for f in ("README.md", "main.py", "pyproject.toml")), str(sel))

    um = M._build_architect_user_message(ctx)
    order = [b for b in ["Planner plan", "Project structure",
                         "Key project files (content)", "Task-relevant project files (content)"]
             if b in um]
    check("A: block order planner→structure→facade→task-relevant",
          order == ["Planner plan", "Project structure",
                    "Key project files (content)", "Task-relevant project files (content)"], str(order))
    for f in ("README.md", "main.py", "pyproject.toml"):
        check(f"A: '--- {f} ---' appears once (facade only)", um.count(f"--- {f} ---") == 1)
    for f in ("src/models.py", "src/queue_manager.py"):
        check(f"A: '--- {f} ---' appears once (乙-3b)", um.count(f"--- {f} ---") == 1)
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# [B] shared budget: 乙-3a exhausts the cap → 乙-3b injects nothing (no fresh budget)
# ══════════════════════════════════════════════════════════════
print("\n[B] 乙-3b draws only from the budget 乙-3a left (shared cap)")
ws = tempfile.mkdtemp(prefix="b1_tf_b_")
try:
    # Three facade files of 6000 chars each → 乙-3a fills the whole 18000 shared budget.
    _write(ws, "README.md", "R" * 6000)
    _write(ws, "pyproject.toml", "P" * 6000)
    _write(ws, "package.json", "K" * 6000)
    # A task-relevant file that WOULD score >=2 if any budget remained.
    _write(ws, "src/models.py", "class Task:\n    pass\n")

    pid = _make_project(ws)
    ctx = {
        "title": "x", "description": "y", "priority": "low",
        "project_id": pid, "previous_outputs": {"planner": {
            "goal_summary": "edit src/models.py",
            "task_breakdown": [{"step": 1, "description": "change the Task model in src/models.py", "role": "builder"}],
            "acceptance_criteria": [],
        }},
    }

    fblock, fpaths, fused = M._build_architect_facade_context(ctx)
    check("B: 乙-3a consumed the full shared budget", fused >= M._ARCH_FACADE_TOTAL_CAP, f"used={fused}")
    tblock = M._build_architect_taskfile_context(ctx, fpaths, fused)
    check("B: 乙-3b injects nothing when budget exhausted (no fresh budget)",
          tblock is None, str(_selected(tblock)))
finally:
    shutil.rmtree(ws, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  B1 architect taskfile: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)
if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
print("\n  ALL PASS")
sys.exit(0)
