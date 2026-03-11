@echo off
setlocal enabledelayedexpansion
REM ══════════════════════════════════════════════════════════════════
REM Phase 5.5 — Validation Pipeline: Full verification suite
REM ══════════════════════════════════════════════════════════════════
REM Runs all verification steps with an isolated runtime instance:
REM   1. Python acceptance tests (against isolated runtime)
REM   2. Frontend build (tsc + vite)
REM   3. Tauri cargo check
REM   4. Browser E2E (Playwright, against isolated runtime)
REM
REM Environment isolation:
REM   - Dedicated test port (default 9899), fail-fast if occupied
REM   - Unique temporary database per run, deleted on exit
REM   - Only kills the runtime PID this script started
REM ══════════════════════════════════════════════════════════════════

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

if not defined VERIFY_PORT set VERIFY_PORT=9899
set TEST_PORT=%VERIFY_PORT%

REM Generate unique DB name using date/time and random
set "TIMESTAMP=%DATE:~-4%%DATE:~-7,2%%DATE:~-10,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "TIMESTAMP=%TIMESTAMP: =0%"
set /a RAND=%RANDOM%
set "TEST_DB=%PROJECT_ROOT%\data\test_verify_%TIMESTAMP%_%RAND%.db"

set RUNTIME_PID=
set STEPS_PASS=0
set STEPS_FAIL=0
set STEP_RESULTS=

echo ==============================================================
echo  AI Team Studio - Full Verification Pipeline
echo  Phase 5.5 - Validation Infrastructure
echo ==============================================================
echo   Port:     %TEST_PORT%
echo   Database: test_verify_%TIMESTAMP%_%RAND%.db
echo.

REM ── Pre-flight: check port availability ──────────────────────────
echo -- Pre-flight: checking port %TEST_PORT% ---
curl -s --connect-timeout 2 "http://127.0.0.1:%TEST_PORT%/" >nul 2>&1
if !errorlevel! equ 0 (
    echo [FAIL] Port %TEST_PORT% is already in use. Cannot proceed.
    echo   Set VERIFY_PORT=^<port^> to use a different port.
    exit /b 1
)
echo   Port %TEST_PORT% is available.
echo.

REM ── Start isolated runtime ───────────────────────────────────────
echo -- Starting isolated test runtime ---
if not exist "%PROJECT_ROOT%\data" mkdir "%PROJECT_ROOT%\data"

set RUNTIME_PORT=%TEST_PORT%
set RUNTIME_DB=%TEST_DB%

cd /d "%PROJECT_ROOT%\services\runtime"
start "verify-runtime" /B cmd /c "python main.py > nul 2>&1"

REM Get the PID of the python process on our test port
REM Give it a moment to start
timeout /t 3 /nobreak >nul

REM Find PID by port
set RUNTIME_PID=
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":%TEST_PORT% " ^| findstr "LISTENING"') do (
    set RUNTIME_PID=%%a
)

if not defined RUNTIME_PID (
    echo   Waiting for runtime startup...
    timeout /t 5 /nobreak >nul
    for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":%TEST_PORT% " ^| findstr "LISTENING"') do (
        set RUNTIME_PID=%%a
    )
)

REM Wait for health check
echo   Waiting for health check...
set HEALTH_OK=0
for /L %%i in (1,1,30) do (
    if !HEALTH_OK! equ 0 (
        curl -sf "http://127.0.0.1:%TEST_PORT%/api/health" >nul 2>&1
        if !errorlevel! equ 0 (
            set HEALTH_OK=1
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)

if !HEALTH_OK! neq 1 (
    echo [FAIL] Runtime failed to start on port %TEST_PORT% within 30s.
    goto :cleanup
)
echo   Runtime healthy on port %TEST_PORT%.
if defined RUNTIME_PID echo   PID: %RUNTIME_PID%
echo.

REM ── Step 1: Python acceptance tests ──────────────────────────────
echo -- Step 1/4: Python Acceptance Tests ---
set TEST_API_BASE=http://127.0.0.1:%TEST_PORT%/api

cd /d "%PROJECT_ROOT%"
call "%SCRIPT_DIR%run-python-tests.bat"
if !errorlevel! equ 0 (
    set /a STEPS_PASS+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Python acceptance tests          PASS!LF!"
) else (
    set /a STEPS_FAIL+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Python acceptance tests          FAIL!LF!"
)
echo.

REM ── Step 2: Frontend build ───────────────────────────────────────
echo -- Step 2/4: Frontend Build (tsc + vite) ---
cd /d "%PROJECT_ROOT%\apps\desktop"
call npm run verify:build >nul 2>&1
if !errorlevel! equ 0 (
    echo   [PASS] Frontend build succeeded
    set /a STEPS_PASS+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Frontend build                   PASS!LF!"
) else (
    echo   [FAIL] Frontend build failed
    set /a STEPS_FAIL+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Frontend build                   FAIL!LF!"
)
echo.

REM ── Step 3: Tauri cargo check ────────────────────────────────────
echo -- Step 3/4: Tauri Cargo Check ---
call "%SCRIPT_DIR%verify-tauri.bat"
if !errorlevel! equ 0 (
    set /a STEPS_PASS+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Tauri cargo check                PASS!LF!"
) else (
    set /a STEPS_FAIL+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Tauri cargo check                FAIL!LF!"
)
echo.

REM ── Step 4: Browser E2E ──────────────────────────────────────────
echo -- Step 4/4: Browser E2E (Playwright) ---
cd /d "%PROJECT_ROOT%\apps\desktop"
set VITE_API_BASE_URL=http://127.0.0.1:%TEST_PORT%
call npx playwright test
if !errorlevel! equ 0 (
    set /a STEPS_PASS+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Browser E2E (Playwright)         PASS!LF!"
) else (
    set /a STEPS_FAIL+=1
    set "STEP_RESULTS=!STEP_RESULTS!  Browser E2E (Playwright)         FAIL!LF!"
)
echo.

REM ── Summary ──────────────────────────────────────────────────────
set /a TOTAL=%STEPS_PASS%+%STEPS_FAIL%

echo ==============================================================
echo  Verification Summary
echo ==============================================================
echo !STEP_RESULTS!
echo --------------------------------------------------------------
echo   Total: %TOTAL%  Pass: %STEPS_PASS%  Fail: %STEPS_FAIL%
echo ==============================================================

:cleanup
echo.
echo -- Cleanup ---
if defined RUNTIME_PID (
    taskkill /F /PID %RUNTIME_PID% /T >nul 2>&1
    echo   Stopped test runtime (PID %RUNTIME_PID%)
) else (
    echo   No runtime PID to clean up
)
if exist "%TEST_DB%" (
    del /f /q "%TEST_DB%" 2>nul
    del /f /q "%TEST_DB%-wal" 2>nul
    del /f /q "%TEST_DB%-shm" 2>nul
    echo   Removed test database
)

if %STEPS_FAIL% gtr 0 exit /b 1
exit /b 0
