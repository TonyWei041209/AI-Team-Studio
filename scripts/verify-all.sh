#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# Phase 5.5 — Validation Pipeline: Full verification suite
# ══════════════════════════════════════════════════════════════════
# Runs all verification steps with an isolated runtime instance:
#   1. Python acceptance tests (against isolated runtime)
#   2. Frontend build (tsc + vite)
#   3. Tauri cargo check
#   4. Browser E2E (Playwright, against isolated runtime)
#
# Environment isolation:
#   - Dedicated test port (default 9899), fail-fast if occupied
#   - Unique temporary database per run, deleted on exit
#   - Only kills the runtime PID this script started
#
# Compatible with Git Bash on Windows + Linux/macOS.
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TEST_PORT="${VERIFY_PORT:-9899}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)_$$"
TEST_DB="$PROJECT_ROOT/data/test_verify_${TIMESTAMP}.db"

RUNTIME_PID=""
STEPS_PASS=0
STEPS_FAIL=0
STEP_RESULTS=""

# ── Cleanup: only kill what we started ───────────────────────────
cleanup() {
  echo ""
  echo "── Cleanup ──────────────────────────────────────────────"
  if [ -n "$RUNTIME_PID" ]; then
    # Cross-platform kill: Git Bash on Windows needs taskkill for child processes
    if [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]]; then
      taskkill //F //PID "$RUNTIME_PID" //T >/dev/null 2>&1 || true
    else
      kill "$RUNTIME_PID" 2>/dev/null || true
      wait "$RUNTIME_PID" 2>/dev/null || true
    fi
    echo "  Stopped test runtime (PID $RUNTIME_PID)"
  fi
  if [ -f "$TEST_DB" ]; then
    rm -f "$TEST_DB" "${TEST_DB}-wal" "${TEST_DB}-shm"
    echo "  Removed test database: $(basename "$TEST_DB")"
  fi
  echo ""
}
trap cleanup EXIT

# ── Helper: record step result ───────────────────────────────────
record_step() {
  local name="$1" result="$2"
  if [ "$result" = "PASS" ]; then
    STEPS_PASS=$((STEPS_PASS + 1))
  else
    STEPS_FAIL=$((STEPS_FAIL + 1))
  fi
  STEP_RESULTS="${STEP_RESULTS}$(printf '  %-35s %s' "$name" "$result")\n"
}

# ── Locate cargo ─────────────────────────────────────────────────
find_cargo() {
  if [ -n "${CARGO_HOME:-}" ] && [ -x "${CARGO_HOME}/bin/cargo" ]; then
    echo "${CARGO_HOME}/bin/cargo"; return
  fi
  local home_dir="${USERPROFILE:-$HOME}"
  if [ -x "${home_dir}/.cargo/bin/cargo" ]; then
    echo "${home_dir}/.cargo/bin/cargo"; return
  fi
  if command -v cargo >/dev/null 2>&1; then
    command -v cargo; return
  fi
  echo ""
}

# ══════════════════════════════════════════════════════════════════
echo "══════════════════════════════════════════════════════════"
echo " AI Team Studio — Full Verification Pipeline"
echo " Phase 5.5 — Validation Infrastructure"
echo "══════════════════════════════════════════════════════════"
echo "  Port:     $TEST_PORT"
echo "  Database: $(basename "$TEST_DB")"
echo ""

# ── Pre-flight: check port availability ──────────────────────────
echo "── Pre-flight: checking port $TEST_PORT ───────────────────"
# Use curl to probe the port; if it connects, port is occupied
if curl -s --connect-timeout 2 "http://127.0.0.1:$TEST_PORT/" >/dev/null 2>&1; then
  echo "[FAIL] Port $TEST_PORT is already in use. Cannot proceed."
  echo "  Set VERIFY_PORT=<port> to use a different port."
  exit 1
fi
echo "  Port $TEST_PORT is available."
echo ""

# ── Start isolated runtime ───────────────────────────────────────
echo "── Starting isolated test runtime ─────────────────────────"
mkdir -p "$PROJECT_ROOT/data"

export RUNTIME_PORT="$TEST_PORT"
export RUNTIME_DB="$TEST_DB"

cd "$PROJECT_ROOT/services/runtime"
python main.py &
RUNTIME_PID=$!
echo "  PID: $RUNTIME_PID"

# Wait for health check
echo "  Waiting for health check..."
HEALTH_OK=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 1
done

if [ "$HEALTH_OK" -ne 1 ]; then
  echo "[FAIL] Runtime failed to start on port $TEST_PORT within 30s."
  exit 1
fi
echo "  Runtime healthy on port $TEST_PORT."
echo ""

# ── Step 1: Python acceptance tests ──────────────────────────────
echo "── Step 1/4: Python Acceptance Tests ──────────────────────"
export TEST_API_BASE="http://127.0.0.1:$TEST_PORT/api"

cd "$PROJECT_ROOT"
if bash "$SCRIPT_DIR/run-python-tests.sh"; then
  record_step "Python acceptance tests" "PASS"
else
  record_step "Python acceptance tests" "FAIL"
fi
echo ""

# ── Step 2: Frontend build ───────────────────────────────────────
echo "── Step 2/4: Frontend Build (tsc + vite) ──────────────────"
cd "$PROJECT_ROOT/apps/desktop"
if npm run verify:build > /dev/null 2>&1; then
  echo "  [PASS] Frontend build succeeded"
  record_step "Frontend build" "PASS"
else
  echo "  [FAIL] Frontend build failed"
  record_step "Frontend build" "FAIL"
fi
echo ""

# ── Step 3: Tauri cargo check ────────────────────────────────────
echo "── Step 3/4: Tauri Cargo Check ────────────────────────────"
CARGO="$(find_cargo)"
if [ -z "$CARGO" ]; then
  echo "  [SKIP] cargo not found — skipping Tauri verification"
  record_step "Tauri cargo check" "SKIP"
else
  cd "$PROJECT_ROOT/apps/desktop/src-tauri"
  if "$CARGO" check > /dev/null 2>&1; then
    echo "  [PASS] Tauri cargo check succeeded"
    record_step "Tauri cargo check" "PASS"
  else
    echo "  [FAIL] Tauri cargo check failed"
    record_step "Tauri cargo check" "FAIL"
  fi
fi
echo ""

# ── Step 4: Browser E2E ──────────────────────────────────────────
echo "── Step 4/4: Browser E2E (Playwright) ─────────────────────"
cd "$PROJECT_ROOT/apps/desktop"
export VITE_API_BASE_URL="http://127.0.0.1:$TEST_PORT"
if npx playwright test 2>&1; then
  record_step "Browser E2E (Playwright)" "PASS"
else
  record_step "Browser E2E (Playwright)" "FAIL"
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────
TOTAL=$((STEPS_PASS + STEPS_FAIL))
echo "══════════════════════════════════════════════════════════"
echo " Verification Summary"
echo "══════════════════════════════════════════════════════════"
printf "$STEP_RESULTS"
echo "──────────────────────────────────────────────────────────"
echo "  Total: $TOTAL  Pass: $STEPS_PASS  Fail: $STEPS_FAIL"
echo "══════════════════════════════════════════════════════════"

if [ "$STEPS_FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
