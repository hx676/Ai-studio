@echo off
cd /d "%~dp0"

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
set "WPF_LAUNCHER=%~dp0launcher\SynCanvasLauncher.exe"

echo Starting SynCanvas launcher...
echo Main app:       http://127.0.0.1:3000/ (default; auto-selects 3001-3099 if busy)
echo TTS Gradio:     http://127.0.0.1:7861/
echo HeyGem Gradio:  http://127.0.0.1:7860/
echo HeyGem REST:    http://127.0.0.1:8383/
echo.

if exist "%WPF_LAUNCHER%" (
  echo Using native launcher: %WPF_LAUNCHER%
  start "" "%WPF_LAUNCHER%"
  exit /b 0
)

echo Native launcher was not found. Falling back to web launcher.
echo Launcher:       http://127.0.0.1:2999/
echo.
"%PYEXE%" tools\launcher_server.py

echo.
echo Launcher stopped.
pause
