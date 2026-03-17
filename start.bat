@echo off
chcp 65001 >nul 2>&1
title AI Team Studio

echo ============================================================
echo   AI Team Studio - Desktop Launcher
echo ============================================================
echo.

:: ── Paths ──────────────────────────────────────────────────────
set "ROOT=%~dp0"
set "RUNTIME_DIR=%ROOT%services\runtime"
set "DESKTOP_DIR=%ROOT%apps\desktop"

:: ── Pre-flight checks ──────────────────────────────────────────
where python >nul 2>&1 || (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)
where node >nul 2>&1 || (
    echo [ERROR] node not found in PATH.
    pause
    exit /b 1
)

:: ── Start Backend (FastAPI on port 9800) ───────────────────────
echo   [1/2] Starting backend (FastAPI)...
cd /d "%RUNTIME_DIR%"
start "AI-Team-Studio-Backend" /min cmd /c "python -m uvicorn main:app --port 9800 --log-level info"

:: Wait for backend readiness
echo         Waiting for backend...
set RETRIES=0
:wait_backend
timeout /t 1 /nobreak >nul
curl -sf http://127.0.0.1:9800/api/projects >nul 2>&1 && goto backend_ok
set /a RETRIES+=1
if %RETRIES% GEQ 20 (
    echo   [ERROR] Backend did not start within 20 seconds.
    pause
    exit /b 1
)
goto wait_backend
:backend_ok
echo         Backend ready.
echo.

:: ── Start Frontend (Vite dev server) ───────────────────────────
echo   [2/2] Starting frontend (Vite dev)...
cd /d "%DESKTOP_DIR%"
start "AI-Team-Studio-Frontend" /min cmd /c "npx vite --open"

echo.
echo ============================================================
echo   AI Team Studio is running!
echo.
echo   Backend:  http://127.0.0.1:9800
echo   Frontend: http://localhost:5173
echo.
echo   Close this window to keep running in background,
echo   or press Ctrl+C then close to stop all.
echo ============================================================
echo.
pause
