@echo off
rem Start backend and frontend in two windows, then open the browser.
cd /d "%~dp0"
if not exist "backend\.env" (
  echo [info] backend\.env not found - starting in offline demo mode ^(MOCK_LLM=1^).
  set MOCK_LLM=1
)
start "mini-claude-code backend" /d "%~dp0backend" cmd /k python main.py
start "mini-claude-code frontend" /d "%~dp0frontend" cmd /k python serve.py
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:5173
