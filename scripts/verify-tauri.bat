@echo off
setlocal
REM ── Verify Tauri shell compiles (cargo check only) ───────────────
REM Locates cargo via: CARGO_HOME → USERPROFILE\.cargo\bin → where cargo

set SCRIPT_DIR=%~dp0
set TAURI_DIR=%SCRIPT_DIR%..\apps\desktop\src-tauri

REM ── Locate cargo ─────────────────────────────────────────────────
set "CARGO="

if defined CARGO_HOME (
    if exist "%CARGO_HOME%\bin\cargo.exe" (
        set "CARGO=%CARGO_HOME%\bin\cargo.exe"
        goto :found
    )
)

if exist "%USERPROFILE%\.cargo\bin\cargo.exe" (
    set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe"
    goto :found
)

where cargo >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%i in ('where cargo') do set "CARGO=%%i"
    goto :found
)

echo [FAIL] cargo not found. Install Rust: https://rustup.rs
exit /b 1

:found
echo ========================================
echo  Tauri Shell Verification (cargo check)
echo ========================================
echo cargo: %CARGO%
echo dir:   %TAURI_DIR%
echo.

cd /d "%TAURI_DIR%"
"%CARGO%" check 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [FAIL] Tauri cargo check failed
    exit /b 1
)

echo.
echo [PASS] Tauri cargo check succeeded
exit /b 0
