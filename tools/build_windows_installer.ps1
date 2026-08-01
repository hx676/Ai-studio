[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$Version = "",
    [string]$ReleaseTimestamp = "",
    [string]$InnoCompiler = "",
    [switch]$SkipVerify,
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"

if (-not $SourceRoot) {
    $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)
if (-not $Version) {
    $Version = (Get-Content -LiteralPath (Join-Path $SourceRoot "VERSION") -Raw).Trim()
}
if (-not $OutputDir) {
    $OutputDir = Join-Path (Split-Path -Parent $SourceRoot) "SynCanvas-Installer-$Version"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

if (-not $InnoCompiler) {
    $InnoCompiler = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw "Inno Setup 6 compiler was not found. Install Inno Setup 6 or pass -InnoCompiler."
}

$portableOutput = Join-Path $OutputDir "portable"
$releaseParams = @{
    SourceRoot = $SourceRoot
    OutputDir = $portableOutput
    Version = $Version
    ReleaseTimestamp = $ReleaseTimestamp
    SkipComponents = $true
    KeepStage = $true
    SkipVerify = [bool]$SkipVerify
}
& (Join-Path $SourceRoot "tools\build_modular_release.ps1") @releaseParams
if ($LASTEXITCODE -ne 0) {
    throw "Portable core build failed"
}

$stageDir = Join-Path $portableOutput ".build\core"
$preflightPython = Join-Path $SourceRoot "python\python.exe"
if (-not (Test-Path -LiteralPath $preflightPython)) {
    $preflightPython = (Get-Command python -ErrorAction Stop).Source
}
& $preflightPython (Join-Path $SourceRoot "tools\release_preflight.py") --root $stageDir --stage --version $Version
if ($LASTEXITCODE -ne 0) {
    throw "Installer input did not pass release preflight"
}

$issFile = Join-Path $SourceRoot "installer\SynCanvas.iss"
& $InnoCompiler "/Q" "/DAppVersion=$Version" "/DStageDir=$stageDir" "/DOutputDir=$OutputDir" $issFile
if ($LASTEXITCODE -ne 0) {
    throw "Windows installer build failed"
}

$setupFile = Join-Path $OutputDir "SynCanvas-Setup-$Version-win-x64.exe"
if (-not (Test-Path -LiteralPath $setupFile)) {
    throw "Installer output is missing: $setupFile"
}
$hash = (Get-FileHash -LiteralPath $setupFile -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $([System.IO.Path]::GetFileName($setupFile))" | Set-Content -LiteralPath "$setupFile.sha256" -Encoding ASCII
if (-not $KeepStage) {
    $portableRoot = [System.IO.Path]::GetFullPath($portableOutput).TrimEnd('\', '/')
    $stageBuildRoot = [System.IO.Path]::GetFullPath((Join-Path $portableRoot ".build"))
    if (-not $stageBuildRoot.StartsWith($portableRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe installer staging path: $stageBuildRoot"
    }
    if (Test-Path -LiteralPath $stageBuildRoot) {
        Remove-Item -LiteralPath $stageBuildRoot -Recurse -Force
    }
}
Write-Host "Windows installer is ready: $setupFile"
Write-Host "SHA-256: $hash"
