@echo off
rem Starts the AURAK dashboard:
rem   1. Ollama (local chatbot model), if installed
rem   2. Python backend service on http://127.0.0.1:8100 (internal, own minimized window)
rem   3. ASP.NET Core web frontend on http://localhost:8000 (this window), then opens the browser
cd /d "%~dp0"
title AURAK Dashboard (ASP.NET frontend)
set DOTNET_NOLOGO=1
set DOTNET_CLI_TELEMETRY_OPTOUT=1

where ollama >nul 2>nul && (
  tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "Ollama" /min ollama serve
)

start "AURAK Backend (Python)" /min ".venv-local\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8100

start "" cmd /c "timeout /t 20 /nobreak >nul && start http://localhost:8000"
echo Starting the AURAK dashboard at http://localhost:8000 ...
echo Run "Stop Dashboard.bat" to stop everything.
dotnet run --project web-aspnet -c Release --no-launch-profile
pause
