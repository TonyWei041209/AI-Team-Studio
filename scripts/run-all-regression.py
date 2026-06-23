#!/usr/bin/env python3
"""Unified cross-phase regression runner.

Sequentially invokes all phase-level regression runners
(6B-D → 6E → 6F → 6G → 7 → 8A → 8B → Roles → Unit) and produces a consolidated
summary report.

Each runner is executed as an independent subprocess. This script does not
modify any runner, service code, or test infrastructure.

Usage:
    python scripts/run-all-regression.py
"""

import os
import re
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixed execution order — matches phase progression
RUNNERS = [
    {
        "name": "Phase 6B-D",
        "script": "scripts/run-6bcd-regression-isolated.py",
        "expected_suites": 3,
        "expected_checks": "~172",
    },
    {
        "name": "Phase 6E",
        "script": "scripts/run-6e-regression-isolated.py",
        "expected_suites": 4,
        "expected_checks": "~277",
    },
    {
        "name": "Phase 6F",
        "script": "scripts/run-6f-regression-isolated.py",
        "expected_suites": 3,
        "expected_checks": "~100",
    },
    {
        "name": "Phase 6G",
        "script": "scripts/run-6g-regression-isolated.py",
        "expected_suites": 2,
        "expected_checks": "~60",
    },
    {
        "name": "Phase 7",
        "script": "scripts/run-7-regression-isolated.py",
        "expected_suites": 9,
        "expected_checks": "~219",
    },
    {
        "name": "Phase 8A",
        "script": "scripts/run-8a-regression-isolated.py",
        "expected_suites": 2,
        "expected_checks": "~54",
    },
    {
        "name": "Phase 8B",
        "script": "scripts/run-8b-regression-isolated.py",
        "expected_suites": 1,
        "expected_checks": "~38",
    },
    {
        "name": "Roles",
        "script": "scripts/run-roles-regression-isolated.py",
        "expected_suites": 4,
        "expected_checks": "~90",
    },
    {
        "name": "Unit",
        "script": "scripts/run-unit-regression.py",
        "expected_suites": 23,
        "expected_checks": "~545",
    },
]

# Pattern to extract summary line from runner output
# Matches: "  XX Total: N suites  Pass: N  Fail: N" or "  XX Total: N  Pass: N  Fail: N"
_SUMMARY_RE = re.compile(
    r"Total:\s*(\d+)\s*(?:suites\s+)?Pass:\s*(\d+)\s+Fail:\s*(\d+)"
)


def _run_single(runner: dict) -> dict:
    """Run a single phase regression runner and capture results."""
    script_path = os.path.join(PROJECT_ROOT, runner["script"])
    name = runner["name"]

    result = {
        "name": name,
        "exit_code": -1,
        "suites_total": 0,
        "suites_pass": 0,
        "suites_fail": 0,
        "parsed": False,
        "error": None,
    }

    if not os.path.isfile(script_path):
        result["error"] = f"Runner script not found: {script_path}"
        return result

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes max per runner
        )
        elapsed = time.monotonic() - start
        result["exit_code"] = proc.returncode
        result["duration_s"] = round(elapsed, 1)

        # Parse summary from stdout
        for line in proc.stdout.splitlines():
            m = _SUMMARY_RE.search(line)
            if m:
                result["suites_total"] = int(m.group(1))
                result["suites_pass"] = int(m.group(2))
                result["suites_fail"] = int(m.group(3))
                result["parsed"] = True
                break

        if not result["parsed"] and proc.returncode != 0:
            # Capture last few lines of output for diagnostics
            lines = (proc.stdout + proc.stderr).strip().splitlines()
            tail = lines[-5:] if len(lines) >= 5 else lines
            result["error"] = "Failed to parse summary; tail: " + " | ".join(tail)

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        result["duration_s"] = round(elapsed, 1)
        result["error"] = "Runner timed out after 600s"

    except Exception as e:
        result["error"] = str(e)

    return result


def main():
    print("=" * 64)
    print("  AI Team Studio — Unified Cross-Phase Regression")
    print("=" * 64)
    print()

    results: list[dict] = []
    overall_pass = True

    for runner in RUNNERS:
        name = runner["name"]
        print(f"  [{name}] running...", end=" ", flush=True)
        r = _run_single(runner)
        results.append(r)

        if r["exit_code"] == 0 and r["parsed"]:
            print(
                f"PASS  "
                f"({r['suites_total']} suites, "
                f"{r['suites_pass']} pass, "
                f"{r['suites_fail']} fail, "
                f"{r.get('duration_s', '?')}s)"
            )
        else:
            overall_pass = False
            err_hint = r.get("error", f"exit code {r['exit_code']}")
            print(f"FAIL  ({err_hint})")

    # Summary table
    print()
    print("-" * 64)
    print(f"  {'Phase':<12} {'Suites':>7} {'Pass':>6} {'Fail':>6} {'Exit':>6} {'Time':>7}")
    print("-" * 64)

    total_suites = 0
    total_pass = 0
    total_fail = 0

    for r in results:
        status_mark = "OK" if r["exit_code"] == 0 else "FAIL"
        duration = f"{r.get('duration_s', '?')}s"
        print(
            f"  {r['name']:<12} {r['suites_total']:>7} "
            f"{r['suites_pass']:>6} {r['suites_fail']:>6} "
            f"{r['exit_code']:>6} {duration:>7}"
        )
        total_suites += r["suites_total"]
        total_pass += r["suites_pass"]
        total_fail += r["suites_fail"]

    print("-" * 64)
    overall_status = "ALL PASS" if overall_pass else "HAS FAILURES"
    print(
        f"  {'TOTAL':<12} {total_suites:>7} "
        f"{total_pass:>6} {total_fail:>6} "
        f"{'':>6} {overall_status:>7}"
    )
    print("-" * 64)

    if not overall_pass:
        print()
        print("  Failed runners:")
        for r in results:
            if r["exit_code"] != 0:
                fallback = f"exit {r['exit_code']}"
                print(f"    - {r['name']}: {r.get('error', fallback)}")
        print()
        sys.exit(1)
    else:
        print()
        print(f"  Baseline verified: {total_suites} suites / {total_pass} pass / 0 fail")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
