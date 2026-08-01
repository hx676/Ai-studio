@echo off
cd /d "%~dp0"

call "%~dp0run.bat"
exit /b %errorlevel%
