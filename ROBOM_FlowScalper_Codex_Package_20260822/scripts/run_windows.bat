@echo off
REM Windows에서 localhost PAPER 서버를 시작하고 브라우저를 연다.
setlocal
cd /d "%~dp0\.."

where uv >nul 2>nul
if errorlevel 1 (
  powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  if errorlevel 1 exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  if errorlevel 1 exit /b 1
)

if "%ROBOM_MODE%"=="" set ROBOM_MODE=FIXTURE_OFFLINE
set ROBOM_OPEN_BROWSER=true
uv run python scripts\run_server.py
endlocal
