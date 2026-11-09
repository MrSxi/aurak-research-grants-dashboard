@echo off
rem Resets the demo student to "not approved" (for showing the access-request flow), then starts everything.
cd /d "%~dp0"
echo Resetting the demo student...
".venv-local\Scripts\python.exe" scripts\reset_demo.py
call "%~dp0Start Dashboard.bat"
