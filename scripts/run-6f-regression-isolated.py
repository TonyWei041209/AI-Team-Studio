#!/usr/bin/env python3
"""Phase 6F self-contained regression runner.

Starts a temporary server with isolated DB and port,
runs all 6F acceptance tests in sequence, then tears down.

Covers:
  - 6F-A service-level dry-run     (43 checks)
  - 6F-A/B API dry-run + read      (33 checks)
  - 6F-C audit trail integration   (24 checks)
  Total baseline: 100 checks

Usage:
    python scripts/run-6f-regression-isolated.py
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_DIR = os.path.join(PROJECT_ROOT, "services", "runtime")
PORT = 9860

TEST_SUITES = [
    "tests/phase6fa_dry_run_acceptance.py",
    "tests/phase6fa_api_acceptance.py",
    "tests/phase6fc_audit_acceptance.py",
]


def main():
    db_file = tempfile.mktemp(suffix=".db", prefix="6f_regression_")
    env = os.environ.copy()
    env["RUNTIME_DB"] = db_file
    env["RUNTIME_PORT"] = str(PORT)

    print("=" * 60)
    print("  Phase 6F Regression Suite (isolated)")
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
        if result.returncode == 0:
            # Extract pass count from last line
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
    print(f"  6F Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  6F baseline: ALL PASS (3 suites / 100 checks)")
        sys.exit(0)


if __name__ == "__main__":
    main()
