#!/usr/bin/env python3
"""Model-executor unit-test runner (no server).

Runs the hermetic model_executor unit tests directly — they stub _resolve_provider
and need no network, API key, or DB (a throwaway RUNTIME_DB is set defensively).
Folds both model_executor unit tests into the unified regression gate (they
previously had no CI runner).

Covers: qa_real_step2 (_execute_qa), sr_step1 (_execute_security_reviewer),
ar_step1 (_execute_architect), doc_step1 (_execute_documentation),
b1_scoped_file_reader (sandboxed role file-reading, B1-b),
b1_documentation_reads (Documentation reads modify-targets, B1-d),
b1_scoped_tree (sandboxed directory listing, B1-Arch-甲-1),
b1_architect_structure (Architect sees dir structure, B1-Arch-甲-3),
b1_architect_facade (Architect reads facade-file content, B1-Arch-乙-3a),
b1_architect_taskfile (Architect reads task-relevant file content, B1-Arch-乙-3b),
b1_builder_taskfile (Builder reads existing modify-target content, B1-Builder),
secret_redactor (content-level secret redaction at the B1 injection layer, TASK 甲),
v23_persist_raw_output (raw_output + finish_reason persistence, V23, TASK 乙),
doc_secret_redaction (secret redaction wired into the documentation read path),
failed_path_persist (raw_output + finish_reason on the FAILED path — V23 gap closed),
builder_json_retry (builder malformed-JSON inner retry, json-parse-only/finish_reason-gated),
max_tokens_clamp (per-model output-token clamp: desired 16384 capped at ModelInfo.max_tokens),
planner_json_retry / reviewer_json_retry (shared _call_parse_retry generalized to the other
two pipeline-blocking roles — planner + reviewer — alongside builder),
sandboxed_file_tools (ReadFileTool/ListDirectoryTool routed through scoped containment,
fail-closed; closes the live /api/tools/execute arbitrary-path-read hole),
tool_loop (B3 step 1: _call_model_with_tools A2 text-protocol read-only tool loop —
action discriminator, fence/prose-tolerant parsing, allowed_tools + read-only enforcement,
max_rounds bound, per-result size budget, token accumulation, never-raises; NOT yet wired
to any role),
architect_tools (B3 step 2: _execute_architect wired to _call_model_with_tools advertising
read_file — pre-fetch augmented not replaced, veto-safe preserved on exhaustion + exception,
end-of-loop observability SUMMARY LogEvent),
tool_loop_summary_persist (B3 step 3.5: tool-loop SUMMARY persisted to the queryable log_events
table — executor returns structured tool_loop_stats, architect attaches them, orchestrator writes
a greppable "tool-loop SUMMARY" row; single-shot roles emit none),
builder_tools (B3 step 4: builder wired to the tool loop via the extracted _parse_retry_core —
advertises read_file only, write_file/shell blocked, blocking semantics preserved, stats on
success AND failure, inner(tool)/outer(parse-retry) composition),
reviewer_architect_context (B2 #1: reviewer now receives the Architect's design — optional
"Architect design:" block in planner->architect->builder order — and REVIEWER_SYSTEM_PROMPT
gained the architect+SR inputs and a build-vs-design consistency / scope-creep rule),
rejection_feedback_removed (C-series pre-work ITEM 1: the dead rejection_feedback key is
removed; mock builder retry detection repointed to the live rejection_history channel),
v24_attempt_number (C-series pre-work ITEM 2: additive nullable attempt_number on agent_runs
records the rejection round per run — first pass 0, re-run tail 1/2 — V23-style migration).
Total: 27 suites.

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
    "tests/doc_step1_execute_documentation_test.py",
    "tests/b1_scoped_file_reader_test.py",
    "tests/b1_documentation_reads_test.py",
    "tests/b1_scoped_tree_test.py",
    "tests/b1_architect_structure_test.py",
    "tests/b1_architect_facade_test.py",
    "tests/b1_architect_taskfile_test.py",
    "tests/b1_builder_taskfile_test.py",
    "tests/secret_redactor_test.py",
    "tests/v23_persist_raw_output_test.py",
    "tests/doc_secret_redaction_test.py",
    "tests/failed_path_persist_test.py",
    "tests/builder_json_retry_test.py",
    "tests/max_tokens_clamp_test.py",
    "tests/planner_json_retry_test.py",
    "tests/reviewer_json_retry_test.py",
    "tests/sandboxed_file_tools_test.py",
    "tests/tool_loop_test.py",
    "tests/architect_tools_test.py",
    "tests/tool_loop_summary_persist_test.py",
    "tests/builder_tools_test.py",
    "tests/reviewer_architect_context_test.py",
    "tests/rejection_feedback_removed_test.py",
    "tests/v24_attempt_number_test.py",
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
        print("  Unit baseline: ALL PASS (27 suites)")
        sys.exit(0)


if __name__ == "__main__":
    main()
