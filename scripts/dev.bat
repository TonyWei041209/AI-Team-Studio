@echo off
echo [AI Team Studio] Starting development environment...
echo.

echo [1/2] Starting FastAPI runtime on port 9800...
start "AI Team Studio - Runtime" cmd /k "cd /d %~dp0..\services\runtime && python main.py"

echo [2/2] Starting Tauri desktop app...
timeout /t 2 /nobreak >nul
cd /d %~dp0..\apps\desktop
npm run tauri:dev
