#!/usr/bin/env python3
"""Phase 8A self-contained regression runner.

Starts a temporary server with isolated DB and port,
runs all Phase 8A acceptance tests in sequence, then tears down.

Covers:
  Group A (service-level):
  - 8A-1 command eligibility gate   (~23 checks)
  - 8A-2 command executor           (~31 checks)

  Total in runner: ~54 checks (2 suites)

Usage:
    python scripts/run-8a-regression-isolated.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
PORT = 9871  # Different from Phase 7 runner (9870)

GROUP_A_SUITES = [
    "tests/phase8a_command_eligibility_acceptance.py",
    "tests/phase8a_command_executor_acceptance.py",
]


def main():
    db_file = tempfile.mktemp(suffix=".db", prefix="8a_regression_")
    workspace_dir = tempfile.mkdtemp(prefix="8a_regression_workspace_")
    env = os.environ.copy()
    env["RUNTIME_DB"] = db_file
    env["RUNTIME_PORT"] = str(PORT)

    print("=" * 60)
    print("  Phase 8A Regression Suite (isolated)")
    print("=" * 60)
    print(f"  DB:        {db_file}")
    print(f"  Workspace: {workspace_dir}")
    print(f"  Port:      {PORT}")
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

    passed = 0
    failed = 0
    failed_names: list[str] = []

    # Group A: service-level tests
    print("  -- Group A: service-level --")
    for suite in GROUP_A_SUITES:
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
    try:
        import shutil
        shutil.rmtree(workspace_dir, ignore_errors=True)
    except OSError:
        pass

    total = passed + failed
    print()
    print("-" * 60)
    print(f"  8A Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  8A baseline: ALL PASS (2 suites / ~54 checks)")
        sys.exit(0)


if __name__ == "__main__":
    main()
