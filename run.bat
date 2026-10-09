@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Web Agent is not installed yet. Running installer...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-and-run.ps1"
  exit /b %ERRORLEVEL%
)
".venv\Scripts\python.exe" -m webagent.app
