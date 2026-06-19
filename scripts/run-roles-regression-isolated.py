#!/usr/bin/env python3
"""Role/pipeline acceptance regression runner (isolated, fresh server PER suite).

Runs the role-count/order/registry/participants/readiness acceptance suites that
assert the pipeline role set (planner, builder, qa, security_reviewer, reviewer).
These had no CI runner and were re-baselined for the 4->5 role-count change.

Each suite gets its OWN fresh temporary server + isolated DB: some of these suites
call _reset_db / mutate the schema directly, so per-suite isolation prevents
cross-contamination (this differs from the per-phase runners, which share one
server across API-only suites). The fresh server + RUNTIME_DB + TEST_API_BASE env
supports both HTTP-driven suites (phase3, phase15_3) and direct-DB suites
(phase15_1, phase19_4).

Covers: phase3 (pipeline order), phase15_1 (role registry), phase15_3
(participants), phase19_4 (readiness).  Total: 4 suites.

Usage:
    python scripts/run-roles-regression-isolated.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
BASE_PORT = 9880

TEST_SUITES = [
    "tests/phase3_acceptance.py",
    "tests/phase15_1_role_registry_test.py",
    "tests/phase15_3_participants_test.py",
    "tests/phase19_4_readiness_test.py",
]


def run_suite(suite, port):
    """Run one suite against a fresh isolated server + temp DB. Returns (code, summary)."""
    db_file = tempfile.mktemp(suffix=".db", prefix="roles_regression_")
    env = os.environ.copy()
    env["RUNTIME_DB"] = db_file
    env["RUNTIME_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=RUNTIME_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    ok = False
    for _ in range(80):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/projects")
            ok = True
            break
        except Exception:
            pass
    if not ok:
        proc.kill()
        return 99, "server failed to start"
    env["TEST_API_BASE"] = f"http://127.0.0.1:{port}/api"
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, suite)],
            env=env, capture_output=True, text=True, timeout=180,
        )
        code, out = r.returncode, r.stdout
    finally:
        proc.kill()
        try:
            os.unlink(db_file)
        except OSError:
            pass
    lines = out.strip().split("\n")
    summary = lines[-2] if len(lines) >= 2 else ""
    return code, summary.strip()


def main():
    print("=" * 60)
    print("  Role/Pipeline Acceptance Regression Suite (isolated)")
    print("=" * 60)
    print()

    passed = 0
    failed = 0
    failed_names: list[str] = []

    for i, suite in enumerate(TEST_SUITES):
        name = os.path.splitext(os.path.basename(suite))[0]
        print(f"  {name}...", end=" ", flush=True)
        code, summary = run_suite(suite, BASE_PORT + i)
        if code == 0:
            print(f"PASS  ({summary})")
            passed += 1
        else:
            print(f"FAIL  ({summary})")
            failed += 1
            failed_names.append(name)

    total = passed + failed
    print()
    print("-" * 60)
    print(f"  Roles Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  Roles baseline: ALL PASS (4 suites)")
        sys.exit(0)


if __name__ == "__main__":
    main()
