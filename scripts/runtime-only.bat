@echo off
echo [AI Team Studio] Starting FastAPI runtime only...
cd /d %~dp0..\services\runtime
python main.py
