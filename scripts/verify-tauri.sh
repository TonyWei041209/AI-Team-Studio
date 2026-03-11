#!/usr/bin/env bash
# ── Verify Tauri shell compiles (cargo check only) ───────────────
# Compatible with Git Bash on Windows + Linux/macOS.
# Locates cargo via: CARGO_HOME → HOME/.cargo/bin → which cargo
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAURI_DIR="$(cd "$SCRIPT_DIR/../apps/desktop/src-tauri" && pwd)"

# ── Locate cargo ─────────────────────────────────────────────────
find_cargo() {
  # 1. CARGO_HOME env
  if [ -n "${CARGO_HOME:-}" ] && [ -x "${CARGO_HOME}/bin/cargo" ]; then
    echo "${CARGO_HOME}/bin/cargo"
    return
  fi
  # 2. USERPROFILE (Windows) or HOME
  local home_dir="${USERPROFILE:-$HOME}"
  if [ -x "${home_dir}/.cargo/bin/cargo" ]; then
    echo "${home_dir}/.cargo/bin/cargo"
    return
  fi
  # 3. which / command -v
  if command -v cargo >/dev/null 2>&1; then
    command -v cargo
    return
  fi
  echo ""
}

CARGO="$(find_cargo)"
if [ -z "$CARGO" ]; then
  echo "[FAIL] cargo not found. Install Rust: https://rustup.rs"
  exit 1
fi

echo "========================================"
echo " Tauri Shell Verification (cargo check)"
echo "========================================"
echo "cargo: $CARGO"
echo "dir:   $TAURI_DIR"
echo ""

cd "$TAURI_DIR"
if "$CARGO" check 2>&1; then
  echo ""
  echo "[PASS] Tauri cargo check succeeded"
  exit 0
else
  echo ""
  echo "[FAIL] Tauri cargo check failed"
  exit 1
fi
