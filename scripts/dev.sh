#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "[AI Team Studio] Starting development environment..."
echo

echo "[1/2] Starting FastAPI runtime on port 9800..."
cd "$PROJECT_ROOT/services/runtime"
python main.py &
RUNTIME_PID=$!

echo "[2/2] Starting Tauri desktop app..."
sleep 2
cd "$PROJECT_ROOT/apps/desktop"
npm run tauri:dev

# Clean up runtime on exit
kill $RUNTIME_PID 2>/dev/null
