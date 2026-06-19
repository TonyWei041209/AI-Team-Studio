#!/usr/bin/env python3
"""Model-executor unit-test runner (no server).

Runs the hermetic model_executor unit tests directly — they stub _resolve_provider
and need no network, API key, or DB (a throwaway RUNTIME_DB is set defensively).
Folds both model_executor unit tests into the unified regression gate (they
previously had no CI runner).

Covers: qa_real_step2 (_execute_qa), sr_step1 (_execute_security_reviewer),
ar_step1 (_execute_architect).
Total: 3 suites.

Usage:
    python scripts/run-unit-regression.py
"""

import os
import subprocess
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEST_SUITES = [
    "tests/qa_real_step2_execute_qa_test.py",
    "tests/sr_step1_execute_security_reviewer_test.py",
    "tests/ar_step1_execute_architect_test.py",
]


def main():
    print("=" * 60)
    print("  Model-Executor Unit Tests (no server)")
    print("=" * 60)
    print()

    passed = 0
    failed = 0
    failed_names: list[str] = []

    for suite in TEST_SUITES:
        name = os.path.splitext(os.path.basename(suite))[0]
        print(f"  {name}...", end=" ", flush=True)
        env = os.environ.copy()
        db_file = tempfile.mktemp(suffix=".db", prefix="unit_regression_")
        env["RUNTIME_DB"] = db_file
        result = subprocess.run(
            [sys.executable, os.path.join(PROJECT_ROOT, suite)],
            env=env, capture_output=True, text=True, timeout=120,
        )
        try:
            os.unlink(db_file)
        except OSError:
            pass
        lines = result.stdout.strip().split("\n")
        summary = lines[-2] if len(lines) >= 2 else ""
        if result.returncode == 0:
            print(f"PASS  ({summary.strip()})")
            passed += 1
        else:
            print(f"FAIL  ({summary.strip()})")
            failed += 1
            failed_names.append(name)

    total = passed + failed
    print()
    print("-" * 60)
    print(f"  Unit Total: {total} suites  Pass: {passed}  Fail: {failed}")
    print("-" * 60)

    if failed:
        print()
        print("  Failed suites:")
        for n in failed_names:
            print(f"    - {n}")
        sys.exit(1)
    else:
        print()
        print("  Unit baseline: ALL PASS (3 suites)")
        sys.exit(0)


if __name__ == "__main__":
    main()
