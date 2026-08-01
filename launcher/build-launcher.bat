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
set "APP_VERSION=1.0.0.0"
if exist "%~dp0..\VERSION" set /p APP_VERSION=<"%~dp0..\VERSION"
dotnet publish SynCanvasLauncher.csproj -c Release -r win-x64 --self-contained true --artifacts-path publish-artifacts /p:PublishSingleFile=true /p:PublishReadyToRun=false /p:AssemblyVersion=%APP_VERSION% /p:FileVersion=%APP_VERSION% /p:InformationalVersion=%APP_VERSION% -o publish
if errorlevel 1 (
  echo Publish failed.
  pause
  exit /b 1
)

copy /y publish\SynCanvasLauncher.exe SynCanvasLauncher.exe >nul
echo Done: %cd%\SynCanvasLauncher.exe
pause
