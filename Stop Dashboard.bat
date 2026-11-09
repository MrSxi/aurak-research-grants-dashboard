@echo off
rem Stops the ASP.NET frontend (port 8000), the Python backend (port 8100) and the Ollama chatbot model.
echo Stopping the AURAK dashboard...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,8100 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
echo Stopping Ollama...
taskkill /IM "ollama app.exe" /F >nul 2>nul
taskkill /IM ollama.exe /F >nul 2>nul
echo Done.
timeout /t 3 >nul
