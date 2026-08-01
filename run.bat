@echo off
cd /d "%~dp0"

set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"
set "WPF_LAUNCHER="
set "DEV_LAUNCHER_PROJECT=%~dp0launcher\SynCanvasLauncher.csproj"
set "DEV_LAUNCHER=%~dp0launcher\bin\Release\net8.0-windows\win-x64\SynCanvasLauncher.exe"
set "PACKAGED_LAUNCHER=%~dp0SynCanvasLauncher.exe"
set "LAUNCHER_BUILD_LOG=%~dp0data\service-logs\launcher-build.log"
set "RUNTIME_PREFLIGHT=%~dp0tools\runtime_preflight.py"
set "RUNTIME_PREFLIGHT_LOG=%~dp0data\service-logs\runtime-preflight.log"

if not exist "%~dp0data\service-logs" mkdir "%~dp0data\service-logs"

if exist "%RUNTIME_PREFLIGHT%" (
  "%PYEXE%" -B "%RUNTIME_PREFLIGHT%" --check >"%RUNTIME_PREFLIGHT_LOG%" 2>&1
  if errorlevel 1 (
    echo Repairing missing main-app Python dependencies...
    "%PYEXE%" -B "%RUNTIME_PREFLIGHT%" --repair >>"%RUNTIME_PREFLIGHT_LOG%" 2>&1
    if errorlevel 1 (
      echo Main-app dependency repair failed. See:
      echo   %RUNTIME_PREFLIGHT_LOG%
      pause
      exit /b 1
    )
  )
)

echo Starting SynCanvas launcher...
echo Main app:       http://127.0.0.1:3000/ (default; auto-selects 3001-3099 if busy)
echo Digital human:  optional component; download and start it from the Digital Human page
echo.

if exist "%DEV_LAUNCHER_PROJECT%" (
  where dotnet >nul 2>nul
  if not errorlevel 1 (
    echo Building the current development launcher...
    dotnet build "%DEV_LAUNCHER_PROJECT%" -c Release --nologo --verbosity minimal >"%LAUNCHER_BUILD_LOG%" 2>&1
    if not errorlevel 1 if exist "%DEV_LAUNCHER%" set "WPF_LAUNCHER=%DEV_LAUNCHER%"
    if errorlevel 1 (
      echo Launcher build failed. See:
      echo   %LAUNCHER_BUILD_LOG%
    )
  )
)

if not defined WPF_LAUNCHER if exist "%PACKAGED_LAUNCHER%" set "WPF_LAUNCHER=%PACKAGED_LAUNCHER%"

if defined WPF_LAUNCHER (
  echo Using native launcher: %WPF_LAUNCHER%
  start "" "%WPF_LAUNCHER%"
  exit /b 0
)

echo Current native launcher is unavailable. Falling back to web launcher.
echo Launcher:       http://127.0.0.1:2999/
echo.
"%PYEXE%" -B tools\launcher_server.py

echo.
echo Launcher stopped.
pause
