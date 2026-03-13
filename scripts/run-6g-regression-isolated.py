#!/usr/bin/env python3
"""Phase 6G self-contained regression runner.

Starts a temporary server with isolated DB and port,
runs all 6G acceptance tests in sequence, then tears down.

Covers:
  - 6G-A action policy service  (39 checks)
  - 6G-A action-plan API        (21 checks)
  Total baseline: 60 checks

Usage:
    python scripts/run-6g-regression-isolated.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
PORT = 9870

# Service-level tests (no server needed)
SERVICE_SUITES = [
    "tests/phase6ga_action_policy_acceptance.py",
]

# API-level tests (need server)
API_SUITES = [
    "tests/phase6ga_api_acceptance.py",
]


def main():
    db_file = tempfile.mktemp(suffix=".db", prefix="6g_regression_")
    env = os.environ.copy()
    env["RUNTIME_DB"] = db_file
    env["RUNTIME_PORT"] = str(PORT)

    print("=" * 60)
    print("  Phase 6G Regression Suite (isolated)")
    print("=" * 60)
    print(f"  DB:   {db_file}")
    print(f"  Port: {PORT}")
    print()

    passed = 0
    failed = 0
    failed_names: list[str] = []

    # ── Service-level tests (no server needed) ──
    print("  --- Service-level tests ---")
    for suite in SERVICE_SUITES:
        name = os.path.splitext(os.path.basename(suite))[0]
        print(f"  {name}...", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, suite)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            summary = lines[-2] if len(lines) >= 2 else ""
            print(f"PASS  ({summary.strip()})")
            passed += 1
        else:
            lines = result.stdout.strip().split("\n")
            summary = lines[-2] if len(lines) >= 2 else ""
            print(f"FAIL  ({summary.strip()})")
            failed += 1
            failed_names.append(name)
    print()

    # ── API-level tests (need server) ──
    print("  --- API-level tests ---")

    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=RUNTIME_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

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

    for suite in API_SUITES:
        name = os.path.splitext(os.path.basename(suite))[0]
        print(f"  {name}...", end=" ", flush=True)
        result = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, suite)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            summary = lines[-2] if len(lines) >= 2 else ""
            print(f"PASS  ({summary.strip()})")
            passed += 1
        else:
            lines = result.stdout.strip().split("\n")
            summary = lines[-2] if len(lines) >= 2 else ""
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
    print(f"  6G Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  6G baseline: ALL PASS (2 suites / 60 checks)")
        sys.exit(0)


if __name__ == "__main__":
    main()
