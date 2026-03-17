@echo off
chcp 65001 >nul 2>&1
title AI Team Studio Desktop

echo ============================================================
echo   AI Team Studio - Desktop App
echo ============================================================
echo.

:: ── Paths ──────────────────────────────────────────────────────
set "ROOT=%~dp0"
set "RUNTIME_DIR=%ROOT%services\runtime"
set "DESKTOP_DIR=%ROOT%apps\desktop"

:: ── Pre-flight checks ──────────────────────────────────────────
where python >nul 2>&1 || goto no_python
where node >nul 2>&1 || goto no_node
where cargo >nul 2>&1 || goto no_cargo
goto checks_ok

:no_python
echo   [ERROR] python not found in PATH.
pause
exit /b 1

:no_node
echo   [ERROR] node not found in PATH.
pause
exit /b 1

:no_cargo
echo   [ERROR] cargo / Rust not found in PATH.
echo           Install from https://rustup.rs
pause
exit /b 1

:checks_ok

:: ── Start Backend ──────────────────────────────────────────────
echo   [1/2] Starting backend...
cd /d "%RUNTIME_DIR%"
start "AI-Team-Studio-Backend" /min cmd /c "python -m uvicorn main:app --port 9800 --log-level info"

echo         Waiting for backend...
set RETRIES=0
:wait_backend
timeout /t 1 /nobreak >nul
curl -sf http://127.0.0.1:9800/api/projects >nul 2>&1 && goto backend_ok
set /a RETRIES+=1
if %RETRIES% GEQ 20 goto backend_timeout
goto wait_backend

:backend_timeout
echo   [ERROR] Backend did not start within 20 seconds.
pause
exit /b 1

:backend_ok
echo         Backend ready.
echo.

:: ── Start Tauri Desktop App ────────────────────────────────────
echo   [2/2] Starting desktop app...
echo         First launch compiles Rust, may take a few minutes.
echo         Subsequent launches will be fast.
echo.
cd /d "%DESKTOP_DIR%"
call npm run tauri:dev

:: ── Cleanup on close ───────────────────────────────────────────
echo.
echo   Shutting down backend...
taskkill /fi "WINDOWTITLE eq AI-Team-Studio-Backend" /f >nul 2>&1
echo   AI Team Studio closed.
pause
