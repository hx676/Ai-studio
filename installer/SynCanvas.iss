#ifndef AppVersion
  #define AppVersion "0.0.0.0"
#endif
#ifndef StageDir
  #error StageDir must point to the audited core release directory
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "SynCanvas"
#define LauncherExe "SynCanvasLauncher.exe"
#define AppId "{{B245CF51-32A2-4CDE-B2B3-2B7A2525719C}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SynCanvas
DefaultDirName={localappdata}\Programs\SynCanvas
DefaultGroupName=SynCanvas
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=SynCanvas-Setup-{#AppVersion}-win-x64
SetupIconFile=..\launcher\app.ico
UninstallDisplayIcon={app}\{#LauncherExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription=SynCanvas Windows Installer
VersionInfoCompany=SynCanvas
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\API"; Flags: uninsneveruninstall
Name: "{app}\assets\input"; Flags: uninsneveruninstall
Name: "{app}\assets\output"; Flags: uninsneveruninstall
Name: "{app}\components"; Flags: uninsneveruninstall
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\output"; Flags: uninsneveruninstall
Name: "{app}\packages\components"; Flags: uninsneveruninstall
Name: "{app}\workflows"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\SynCanvas"; Filename: "{app}\{#LauncherExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\SynCanvas"; Filename: "{app}\{#LauncherExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\{#LauncherExe}"; Description: "启动 SynCanvas"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  PythonExe: String;
  SupervisorScript: String;
begin
  Result := '';
  PythonExe := ExpandConstant('{app}\python\python.exe');
  SupervisorScript := ExpandConstant('{app}\tools\service_supervisor.py');
  if FileExists(PythonExe) and FileExists(SupervisorScript) then
  begin
    Exec(PythonExe, '"' + SupervisorScript + '" --stop', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
