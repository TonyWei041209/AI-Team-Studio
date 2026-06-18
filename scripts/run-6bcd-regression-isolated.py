#!/usr/bin/env python3
"""Phase 6B-D self-contained regression runner.

Starts a temporary server with isolated DB and port, runs the QA/role-invariant
acceptance suites (phase 6B-Round2, 6C, 6D) in sequence, then tears down.

These suites assert QA/role activation invariants (re-baselined when QA became a
real static-review role). They had no CI runner; this folds them into the
unified regression gate so they cannot silently drift.

Covers:
  - phase6b_round2_acceptance  (Reviewer/QA real-model integration + role defs)
  - phase6c_acceptance         (role_model_settings config-driven routing)
  - phase6d_acceptance         (Builder plan-only + QA real-role wiring)

  Total in runner: 3 suites

Usage:
    python scripts/run-6bcd-regression-isolated.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
PORT = 9873  # Distinct from other isolated runners (6E=9850, 8A=9871, 8B=9872)

TEST_SUITES = [
    "tests/phase6b_round2_acceptance.py",
    "tests/phase6c_acceptance.py",
    "tests/phase6d_acceptance.py",
]


def main():
    db_file = tempfile.mktemp(suffix=".db", prefix="6bcd_regression_")
    env = os.environ.copy()
    env["RUNTIME_DB"] = db_file
    env["RUNTIME_PORT"] = str(PORT)

    print("=" * 60)
    print("  Phase 6B-D Regression Suite (isolated)")
    print("=" * 60)
    print(f"  DB:   {db_file}")
    print(f"  Port: {PORT}")
    print()

    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=RUNTIME_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server
    print("  Starting server...", end=" ", flush=True)
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/projects")
            break
        except Exception:
            pass
    else:
        proc.kill()
        print("FAILED (timeout)")
        sys.exit(1)
    print("OK")
    print()

    env["TEST_API_BASE"] = f"http://127.0.0.1:{PORT}/api"

    passed = 0
    failed = 0
    failed_names: list[str] = []

    for suite in TEST_SUITES:
        name = os.path.splitext(os.path.basename(suite))[0]
        print(f"  {name}...", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, suite)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Pass/fail is the suite's exit code (each suite sys.exit(0/1)). The
        # display summary is the suite's penultimate line — works for both the
        # "TOTAL: N | PASS: N | FAIL: N" (6b_round2) and "Phase 6X Acceptance:
        # N passed, N failed, N total" (6c/6d) formats.
        lines = result.stdout.strip().split("\n")
        summary = lines[-2] if len(lines) >= 2 else ""
        if result.returncode == 0:
            print(f"PASS  ({summary.strip()})")
            passed += 1
        else:
            print(f"FAIL  ({summary.strip()})")
            failed += 1
            failed_names.append(name)

    # Cleanup
    proc.kill()
    try:
        os.unlink(db_file)
    except OSError:
        pass

    total = passed + failed
    print()
    print("-" * 60)
    print(f"  6B-D Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  6B-D baseline: ALL PASS (3 suites)")
        sys.exit(0)


if __name__ == "__main__":
    main()
