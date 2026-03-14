#!/usr/bin/env python3
"""Phase 7C-3 acceptance: rollback API endpoint.

Runs against a live isolated server.

Usage:
    python tests/phase7c3_rollback_api_acceptance.py
"""

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid as _uuid
from datetime import datetime, timezone

PASS = 0
FAIL = 0
PORT = 9885
BASE = f"http://127.0.0.1:{PORT}"
SERVER_PROC = None
TEMP_DB = None
WORKSPACE = None


def check(label: str, condition: bool, detail: str = ""):
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


def _content_hash(data: str) -> str:
    canonical = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            return e.code, json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return e.code, {"raw": body_bytes.decode("utf-8", errors="replace")}


def _build_full_chain(snapshot_data: dict, workspace: str) -> dict:
    """Build chain via API calls and return IDs."""
    import sqlite3

    db_path = TEMP_DB
    if not db_path:
        raise RuntimeError("TEMP_DB not set")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    now = datetime.now(timezone.utc).isoformat()
    pid = str(_uuid.uuid4())
    tid = str(_uuid.uuid4())
    prid = str(_uuid.uuid4())
    aid = str(_uuid.uuid4())
    sid = str(_uuid.uuid4())
    rid = str(_uuid.uuid4())
    drid = str(_uuid.uuid4())

    snap_json = json.dumps(snapshot_data)
    ch = _content_hash(snap_json)

    try:
        conn.execute(
            """INSERT INTO projects (id, name, local_repo_path, default_branch,
               description, created_at, updated_at) VALUES (?,?,?,?,?,?,?)""",
            (pid, f"test-{pid[:8]}", workspace, "main", "", now, now),
        )
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, description, status,
               priority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)""",
            (tid, pid, "t", "", "in_progress", "medium", now, now),
        )
        conn.execute(
            """INSERT INTO execution_proposals
               (id, task_id, run_id, role, proposal_data, risk_level,
                requires_approval, approval_reasons, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (prid, tid, None, "builder", snap_json, "low", 1, "[]", "approved", now, now),
        )
        conn.execute(
            """INSERT INTO approval_requests
               (id, task_id, run_id, action_type, action_payload,
                status, proposal_id, created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (aid, tid, None, "proposal:builder", "{}", "approved", prid, now),
        )
        conn.execute(
            """INSERT INTO execution_snapshots
               (id, proposal_id, approval_id, task_id, snapshot_data,
                content_hash, risk_level, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sid, prid, aid, tid, snap_json, ch, "low", "frozen", now),
        )
        conn.execute(
            """INSERT INTO execution_requests
               (id, task_id, proposal_id, approval_id, snapshot_id,
                snapshot_content_hash, risk_level, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, tid, prid, aid, sid, ch, "low", "confirmed", now, now),
        )
        dr_data = json.dumps({
            "mode": "dry_run", "summary": "ok",
            "planned_file_actions": [], "planned_command_actions": [],
            "warnings": [],
        })
        conn.execute(
            """INSERT INTO execution_results
               (id, execution_request_id, task_id, snapshot_id,
                snapshot_content_hash, mode, status, result_data,
                started_at, completed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (drid, rid, tid, sid, ch, "dry_run", "completed", dr_data, now, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    return {"request_id": rid, "task_id": tid, "dry_run_id": drid}


def start_server():
    global SERVER_PROC, TEMP_DB, WORKSPACE

    TEMP_DB = tempfile.mktemp(prefix="7c3_api_", suffix=".db")
    WORKSPACE = tempfile.mkdtemp(prefix="7c3_ws_")

    # Pre-initialize DB so both test and server share the same schema
    runtime_dir = os.path.join(os.path.dirname(__file__), "..", "services", "runtime")
    if os.path.abspath(runtime_dir) not in sys.path:
        sys.path.insert(0, os.path.abspath(runtime_dir))
    os.environ["RUNTIME_DB"] = TEMP_DB
    from database import init_db
    init_db()

    env = {**os.environ, "RUNTIME_DB": TEMP_DB}
    runtime = runtime_dir
    SERVER_PROC = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--port", str(PORT), "--host", "127.0.0.1", "--log-level", "warning"],
        cwd=runtime,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(40):
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=0.5)
            s.close()
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Server did not start")


def stop_server():
    global SERVER_PROC
    if SERVER_PROC:
        SERVER_PROC.terminate()
        SERVER_PROC.wait(timeout=5)
        SERVER_PROC = None
    if TEMP_DB and os.path.exists(TEMP_DB):
        os.unlink(TEMP_DB)
    if WORKSPACE and os.path.isdir(WORKSPACE):
        shutil.rmtree(WORKSPACE, ignore_errors=True)


# ── Start ─────────────────────────────────────────────────
print(f"\n  Starting server on port {PORT}...")
start_server()
print("  Server started.\n")

try:
    # ══════════════════════════════════════════════════════════
    # S1: result not found → 404
    # ══════════════════════════════════════════════════════════
    print("[S1]  Result not found → 404")
    status, body = _api("POST", "/api/execution-results/nonexistent/rollback")
    check("404 returned", status == 404, f"got {status}")

    # ══════════════════════════════════════════════════════════
    # S2: dry_run result → 409
    # ══════════════════════════════════════════════════════════
    print("\n[S2]  dry_run result → 409")
    os.makedirs(os.path.join(WORKSPACE, "src"), exist_ok=True)
    ids2 = _build_full_chain(
        {"proposed_files": [
            {"path": "src/a.py", "operation": "create", "content": "x"},
        ], "proposed_commands": []},
        WORKSPACE,
    )
    status, body = _api("POST", f"/api/execution-results/{ids2['dry_run_id']}/rollback")
    check("409 returned", status == 409, f"got {status}")
    detail = body.get("detail", {})
    check("detail has message", "message" in detail if isinstance(detail, dict) else False,
          f"got: {detail}")

    # ══════════════════════════════════════════════════════════
    # S3: Normal rollback (modify) → 200 + restored
    # ══════════════════════════════════════════════════════════
    print("\n[S3]  Normal rollback modify → 200 + restored")
    ws3 = tempfile.mkdtemp(prefix="7c3_s3_")
    os.makedirs(os.path.join(ws3, "src"), exist_ok=True)
    with open(os.path.join(ws3, "src", "app.py"), "w", encoding="utf-8") as f:
        f.write("original")

    ids3 = _build_full_chain(
        {"proposed_files": [
            {"path": "src/app.py", "operation": "modify", "content": "modified"},
        ], "proposed_commands": []},
        ws3,
    )
    # Execute first
    es, ebody = _api("POST", f"/api/execution-requests/{ids3['request_id']}/execute")
    check("execute succeeded", es == 200 and ebody.get("status") == "completed",
          f"status={es}, result_status={ebody.get('status')}")

    real_run_id = ebody["id"]

    # Rollback
    rs, rbody = _api("POST", f"/api/execution-results/{real_run_id}/rollback")
    check("200 returned", rs == 200, f"got {rs}")
    check("mode is rollback", rbody.get("mode") == "rollback")
    check("status is completed", rbody.get("status") == "completed")
    check("is_new is true", rbody.get("is_new") is True)
    check("has all top-level fields", all(
        k in rbody for k in ["id", "execution_request_id", "task_id",
                              "snapshot_id", "mode", "status", "result_data"]))

    rd = rbody.get("result_data", {})
    rrs = rd.get("rollback_results", [])
    check("one rollback result", len(rrs) == 1)
    if rrs:
        check("rollback status restored", rrs[0]["status"] == "restored")

    # Verify file restored
    with open(os.path.join(ws3, "src", "app.py"), "r") as f:
        check("file content restored", f.read() == "original")

    shutil.rmtree(ws3, ignore_errors=True)

    # ══════════════════════════════════════════════════════════
    # S4: Normal rollback (create) → 200 + deleted
    # ══════════════════════════════════════════════════════════
    print("\n[S4]  Normal rollback create → 200 + deleted")
    ws4 = tempfile.mkdtemp(prefix="7c3_s4_")
    os.makedirs(os.path.join(ws4, "src"), exist_ok=True)

    ids4 = _build_full_chain(
        {"proposed_files": [
            {"path": "src/new.py", "operation": "create", "content": "brand new"},
        ], "proposed_commands": []},
        ws4,
    )
    es4, eb4 = _api("POST", f"/api/execution-requests/{ids4['request_id']}/execute")
    check("execute succeeded", es4 == 200 and eb4.get("status") == "completed")
    check("file was created", os.path.exists(os.path.join(ws4, "src", "new.py")))

    rs4, rb4 = _api("POST", f"/api/execution-results/{eb4['id']}/rollback")
    check("200 returned", rs4 == 200)
    rd4 = rb4.get("result_data", {})
    rrs4 = rd4.get("rollback_results", [])
    if rrs4:
        check("rollback status deleted", rrs4[0]["status"] == "deleted")
    check("file deleted", not os.path.exists(os.path.join(ws4, "src", "new.py")))

    shutil.rmtree(ws4, ignore_errors=True)

    # ══════════════════════════════════════════════════════════
    # S5: Idempotent repeat → 200 + is_new=false + same id
    # ══════════════════════════════════════════════════════════
    print("\n[S5]  Idempotent repeat")
    # Use S3's rollback result (already created)
    rs5, rb5 = _api("POST", f"/api/execution-results/{real_run_id}/rollback")
    check("200 on repeat", rs5 == 200, f"got {rs5}")
    check("is_new is false", rb5.get("is_new") is False)
    check("same id as first", rb5.get("id") == rbody.get("id"))

    # ══════════════════════════════════════════════════════════
    # S6: Best-effort partial failure → 200 + status=failed
    # ══════════════════════════════════════════════════════════
    print("\n[S6]  Best-effort partial failure")
    ws6 = tempfile.mkdtemp(prefix="7c3_s6_")
    os.makedirs(os.path.join(ws6, "src"), exist_ok=True)
    with open(os.path.join(ws6, "src", "a.py"), "w", encoding="utf-8") as f:
        f.write("orig a")
    with open(os.path.join(ws6, "src", "b.py"), "w", encoding="utf-8") as f:
        f.write("orig b")

    ids6 = _build_full_chain(
        {"proposed_files": [
            {"path": "src/a.py", "operation": "modify", "content": "new a"},
            {"path": "src/b.py", "operation": "modify", "content": "new b"},
        ], "proposed_commands": []},
        ws6,
    )
    es6, eb6 = _api("POST", f"/api/execution-requests/{ids6['request_id']}/execute")

    # Delete backup for a.py to simulate missing
    import sqlite3
    db_path = TEMP_DB
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM execution_file_backups WHERE execution_result_id = ? AND path = ?",
        (eb6["id"], "src/a.py"),
    )
    conn.commit()
    conn.close()

    rs6, rb6 = _api("POST", f"/api/execution-results/{eb6['id']}/rollback")
    check("200 returned (not 500)", rs6 == 200, f"got {rs6}")
    check("status is failed", rb6.get("status") == "failed")

    rd6 = rb6.get("result_data", {})
    rrs6 = rd6.get("rollback_results", [])
    a_rb = next((r for r in rrs6 if r["path"] == "src/a.py"), None)
    b_rb = next((r for r in rrs6 if r["path"] == "src/b.py"), None)
    check("a.py failed (no backup)", a_rb and a_rb["status"] == "failed")
    check("b.py restored", b_rb and b_rb["status"] == "restored")

    shutil.rmtree(ws6, ignore_errors=True)

finally:
    stop_server()


# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
total = PASS + FAIL
print()
print("-" * 60)
print(f"  7C-3 rollback API: {total} checks  Pass: {PASS}  Fail: {FAIL}")
print("-" * 60)

if FAIL:
    print(f"\n  {FAIL} FAILED")
    sys.exit(1)
else:
    print("\n  ALL PASS")
    sys.exit(0)
