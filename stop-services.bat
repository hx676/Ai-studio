@echo off
cd /d "%~dp0"

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo Stopping SynCanvas services started by run.bat...
echo.

"%PYEXE%" tools\service_supervisor.py --stop

echo.
echo Stop command finished.
pause
