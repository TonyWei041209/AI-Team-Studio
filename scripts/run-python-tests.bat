@echo off
setlocal enabledelayedexpansion
REM ── Run all Python acceptance tests ──────────────────────────────
REM Respects TEST_API_BASE env var (default: http://127.0.0.1:9800/api).

set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

set PASS=0
set FAIL=0
set ERRORS=

echo ========================================
echo  Python Acceptance Tests
echo ========================================
if defined TEST_API_BASE (
    echo API base: %TEST_API_BASE%
) else (
    echo API base: http://127.0.0.1:9800/api
)
echo.

for %%F in (
    tests\phase2_acceptance.py
    tests\phase3_acceptance.py
    tests\phase4a_acceptance.py
    tests\phase4b_acceptance.py
    tests\phase5_api_workflow_acceptance.py
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
echo   Total: %TOTAL%  Pass: %PASS%  Fail: %FAIL%
echo ----------------------------------------

if %FAIL% gtr 0 (
    echo.
    echo Failed suites:
    echo !ERRORS!
    exit /b 1
)

exit /b 0
