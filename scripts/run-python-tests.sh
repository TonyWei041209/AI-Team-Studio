#!/usr/bin/env bash
# ── Run all Python acceptance tests ──────────────────────────────
# Compatible with Git Bash on Windows + Linux/macOS.
# Respects TEST_API_BASE env var (default: http://127.0.0.1:9800/api).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
ERRORS=""

TEST_FILES=(
  "tests/phase2_acceptance.py"
  "tests/phase3_acceptance.py"
  "tests/phase4a_acceptance.py"
  "tests/phase4b_acceptance.py"
  "tests/phase5_api_workflow_acceptance.py"
)

echo "========================================"
echo " Python Acceptance Tests"
echo "========================================"
echo "API base: ${TEST_API_BASE:-http://127.0.0.1:9800/api}"
echo ""

for tf in "${TEST_FILES[@]}"; do
  name="$(basename "$tf" .py)"
  printf "  %-45s" "$name"
  if python "$PROJECT_ROOT/$tf" > /dev/null 2>&1; then
    echo "PASS"
    PASS=$((PASS + 1))
  else
    echo "FAIL"
    FAIL=$((FAIL + 1))
    ERRORS="$ERRORS  - $name\n"
  fi
done

echo ""
echo "----------------------------------------"
echo "  Total: $((PASS + FAIL))  Pass: $PASS  Fail: $FAIL"
echo "----------------------------------------"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Failed suites:"
  printf "$ERRORS"
  exit 1
fi

exit 0
