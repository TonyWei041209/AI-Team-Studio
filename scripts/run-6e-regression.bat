@echo off
setlocal enabledelayedexpansion
REM ── Phase 6E Regression Test Suite ──────────────────────────────
REM Runs all 6E acceptance tests (A through E) against a running server.
REM
REM Usage:
REM   1. Start the runtime server (or let this script start one):
REM        cd services\runtime
REM        set RUNTIME_DB=<db_path>
REM        python -m uvicorn main:app --port 9800
REM   2. Run this script:
REM        scripts\run-6e-regression.bat
REM
REM Environment:
REM   TEST_API_BASE  - API base URL (default: http://127.0.0.1:9800/api)

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

set PASS=0
set FAIL=0
set TOTAL=0
set ERRORS=

echo ========================================
echo  Phase 6E Regression Suite
echo ========================================
if defined TEST_API_BASE (
    echo API base: %TEST_API_BASE%
) else (
    echo API base: http://127.0.0.1:9800/api
)
echo.

for %%F in (
    tests\phase6ea_acceptance.py
    tests\phase6eb_acceptance.py
    tests\phase6ec_acceptance.py
    tests\phase6ee_acceptance.py
) do (
    set "NAME=%%~nF"
    <nul set /p "=  !NAME!... "
    python "%PROJECT_ROOT%\%%F" >nul 2>&1
    if !errorlevel! equ 0 (
        echo PASS
        set /a PASS+=1
    ) else (
        echo FAIL
        set /a FAIL+=1
        set "ERRORS=!ERRORS!  - !NAME!!LF!"
    )
)

set /a TOTAL=%PASS%+%FAIL%
echo.
echo ----------------------------------------
echo   6E Total: %TOTAL%  Pass: %PASS%  Fail: %FAIL%
echo ----------------------------------------

if %FAIL% gtr 0 (
    echo.
    echo Failed suites:
    echo !ERRORS!
    exit /b 1
)

echo.
echo 6E baseline: ALL PASS
exit /b 0
