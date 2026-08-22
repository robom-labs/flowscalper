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
if not exist "frontend\dist\index.html" (
  powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  if errorlevel 1 exit /b 1
)

if "%ROBOM_MODE%"=="" set ROBOM_MODE=READY
if "%ROBOM_PORT%"=="" for /f %%P in ('uv run --frozen python scripts\select_local_port.py') do set ROBOM_PORT=%%P
if "%ROBOM_DB_PATH%"=="" set ROBOM_DB_PATH=%CD%\data\run-ledger.sqlite3
if "%ROBOM_OPEN_BROWSER%"=="" set ROBOM_OPEN_BROWSER=true
echo 데이터 위치: %ROBOM_DB_PATH%
uv run --frozen python scripts\run_server.py
endlocal
