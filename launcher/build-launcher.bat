@echo off
cd /d "%~dp0"

where dotnet >nul 2>nul
if errorlevel 1 (
  echo .NET SDK not found. Please install .NET 8 SDK first.
  pause
  exit /b 1
)

dotnet --list-sdks | findstr /r "^8\." >nul
if errorlevel 1 (
  echo .NET 8 SDK not found. Please install .NET 8 SDK first.
  echo Current dotnet info:
  dotnet --info
  pause
  exit /b 1
)

echo Publishing SynCanvasLauncher...
dotnet publish SynCanvasLauncher.csproj -c Release -r win-x64 --self-contained false /p:PublishSingleFile=true /p:PublishReadyToRun=false -o publish
if errorlevel 1 (
  echo Publish failed.
  pause
  exit /b 1
)

copy /y publish\SynCanvasLauncher.exe SynCanvasLauncher.exe >nul
echo Done: %cd%\SynCanvasLauncher.exe
pause
