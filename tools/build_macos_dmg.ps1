[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$Version = "",
    [string]$ReleaseTimestamp = "",
    [string]$DockerImage = "syncanvas-macos-dmg-builder:2026.08.01",
    [ValidateSet("universal-bootstrap", "arm64-offline")]
    [string]$BuildFlavor = "universal-bootstrap",
    [string]$CacheDir = "",
    [switch]$PullBuilderImage,
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
if (-not $ReleaseTimestamp) {
    $ReleaseTimestamp = (Get-Date).ToString("o")
}
if ($Version -notmatch '^\d{4}\.\d{2}\.\d{2}\.\d+$') {
    throw "Version must use YYYY.MM.DD.N format"
}
[DateTimeOffset]::Parse($ReleaseTimestamp) | Out-Null
$isArm64Offline = $BuildFlavor -eq "arm64-offline"
if (-not $OutputDir) {
    $suffix = if ($isArm64Offline) { "-arm64" } else { "" }
    $OutputDir = Join-Path (Split-Path -Parent $SourceRoot) "SynCanvas-DMG-$Version$suffix"
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

function Assert-ChildPath([string]$PathValue, [string]$ParentValue) {
    $path = [System.IO.Path]::GetFullPath($PathValue)
    $parent = [System.IO.Path]::GetFullPath($ParentValue).TrimEnd('\', '/')
    if ($path -eq $parent -or -not $path.StartsWith($parent + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build path: $path"
    }
    return $path
}

function Remove-BuildPath([string]$PathValue, [string]$ParentValue) {
    if (-not (Test-Path -LiteralPath $PathValue)) { return }
    $safe = Assert-ChildPath $PathValue $ParentValue
    Remove-Item -LiteralPath $safe -Recurse -Force
}

function Invoke-Robocopy([string]$From, [string]$To, [string[]]$ExtraArgs = @()) {
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    & robocopy $From $To /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP @ExtraArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Robocopy failed for $From with exit code $LASTEXITCODE"
    }
}

function Write-Utf8NoBom([string]$PathValue, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($PathValue, $Content, $encoding)
}

function Get-VerifiedArtifact([string]$Url, [string]$Destination, [string]$ExpectedSha256) {
    $expected = $ExpectedSha256.ToLowerInvariant()
    if (Test-Path -LiteralPath $Destination) {
        $existingHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $expected) { return }
        Remove-Item -LiteralPath $Destination -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    $temporary = "$Destination.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --fail --location --retry 5 --retry-delay 2 --output $temporary $Url
        if ($LASTEXITCODE -ne 0) { throw "Artifact download failed: $Url" }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $temporary
    }
    $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        throw "Artifact checksum mismatch for $Url`: $actual != $expected"
    }
    Move-Item -LiteralPath $temporary -Destination $Destination -Force
}

$preflightPython = Join-Path $SourceRoot "python\python.exe"
if (-not (Test-Path -LiteralPath $preflightPython)) {
    $preflightPython = (Get-Command python -ErrorAction Stop).Source
}
& $preflightPython (Join-Path $SourceRoot "tools\release_preflight.py") --root $SourceRoot
if ($LASTEXITCODE -ne 0) { throw "Source release preflight failed" }

$runtimeManifest = $null
$runtimeArchivePath = $null
$wheelCache = $null
if ($isArm64Offline) {
    $manifestPath = Join-Path $SourceRoot "macos\runtime-arm64.json"
    $runtimeManifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeManifest.architecture -ne "arm64" -or -not $runtimeManifest.filename -or -not $runtimeManifest.sha256 -or -not $runtimeManifest.url) {
        throw "Invalid macOS arm64 runtime manifest"
    }
    if (-not $CacheDir) {
        $CacheDir = Join-Path (Split-Path -Parent $SourceRoot) ".syncanvas-build-cache\macos-arm64"
    }
    $CacheDir = [System.IO.Path]::GetFullPath($CacheDir)
    New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null
    $runtimeArchivePath = Join-Path $CacheDir ([string]$runtimeManifest.filename)
    Get-VerifiedArtifact ([string]$runtimeManifest.url) $runtimeArchivePath ([string]$runtimeManifest.sha256)

    $requirementsPath = Join-Path $SourceRoot "requirements.lock"
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $wheelCacheName = "wheelhouse-cp312-macosx_11_0_arm64-{0}" -f $requirementsHash.Substring(0, 16)
    $wheelCache = Assert-ChildPath (Join-Path $CacheDir $wheelCacheName) $CacheDir
    $wheelMarker = Join-Path $wheelCache ".complete"
    if (-not (Test-Path -LiteralPath $wheelMarker) -or (Get-Content -LiteralPath $wheelMarker -Raw).Trim() -ne $requirementsHash) {
        Remove-BuildPath $wheelCache $CacheDir
        New-Item -ItemType Directory -Path $wheelCache -Force | Out-Null
        & $preflightPython -m pip download `
            --disable-pip-version-check `
            --only-binary=:all: `
            --implementation cp `
            --python-version 312 `
            --abi cp312 `
            --platform macosx_11_0_arm64 `
            --dest $wheelCache `
            --requirement $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw "macOS arm64 wheel download failed" }
        $lockedCount = @(Get-Content -LiteralPath $requirementsPath | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith("#") }).Count
        $wheels = @(Get-ChildItem -LiteralPath $wheelCache -File -Filter "*.whl")
        if ($wheels.Count -lt $lockedCount) {
            throw "Incomplete macOS arm64 wheelhouse: $($wheels.Count) wheels for $lockedCount locked requirements"
        }
        $requirementsHash | Set-Content -LiteralPath $wheelMarker -Encoding ASCII
    }
}

$buildRoot = Assert-ChildPath (Join-Path $OutputDir ".build") $OutputDir
$dmgRoot = Assert-ChildPath (Join-Path $buildRoot "dmg-root") $buildRoot
$appRoot = Assert-ChildPath (Join-Path $dmgRoot "SynCanvas.app") $dmgRoot
$contentsRoot = Join-Path $appRoot "Contents"
$resourcesRoot = Join-Path $contentsRoot "Resources"
$coreRoot = Join-Path $resourcesRoot "core"
Remove-BuildPath $buildRoot $OutputDir
New-Item -ItemType Directory -Path (Join-Path $contentsRoot "MacOS") -Force | Out-Null
New-Item -ItemType Directory -Path $coreRoot -Force | Out-Null

foreach ($dir in @("app", "static", "tools", "docs", "custom_nodes")) {
    $source = Join-Path $SourceRoot $dir
    if (Test-Path -LiteralPath $source) {
        Invoke-Robocopy $source (Join-Path $coreRoot $dir) @(
            "/XD", "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
            "/XF", "*.pyc", "*.pyo", "*.log"
        )
    }
}
if (Test-Path -LiteralPath (Join-Path $SourceRoot "CLI\macos")) {
    Invoke-Robocopy (Join-Path $SourceRoot "CLI\macos") (Join-Path $coreRoot "CLI\macos")
}
if (Test-Path -LiteralPath (Join-Path $SourceRoot "CLI\README.md")) {
    New-Item -ItemType Directory -Path (Join-Path $coreRoot "CLI") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceRoot "CLI\README.md") -Destination (Join-Path $coreRoot "CLI\README.md") -Force
}

foreach ($name in @(
    "main.py", "VERSION", "requirements.txt", "requirements.lock", "package.json", "package-lock.json",
    "tailwind.config.js", "README.md", "LICENSE", "components-manifest.json", "node-engine-manifest.json", "homepage.yml"
)) {
    $source = Join-Path $SourceRoot $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $coreRoot $name) -Force
    }
}
foreach ($relative in @("API", "data", "components", "output", "packages\components", "workflows", "assets\input", "assets\output")) {
    New-Item -ItemType Directory -Path (Join-Path $coreRoot $relative) -Force | Out-Null
}

$parts = $Version.Split('.')
$shortVersion = "{0}.{1}.{2}" -f [int]$parts[0], [int]$parts[1], [int]$parts[2]
$buildVersion = "{0}{1:D2}{2:D2}{3:D2}" -f [int]$parts[0], [int]$parts[1], [int]$parts[2], [int]$parts[3]
$plist = Get-Content -LiteralPath (Join-Path $SourceRoot "macos\Info.plist.in") -Raw -Encoding UTF8
$plist = $plist.Replace("__SHORT_VERSION__", $shortVersion).Replace("__BUILD_VERSION__", $buildVersion)
Write-Utf8NoBom (Join-Path $contentsRoot "Info.plist") $plist
Copy-Item -LiteralPath (Join-Path $SourceRoot "macos\Stop-SynCanvas.command") -Destination (Join-Path $dmgRoot "Stop-SynCanvas.command") -Force
if ($isArm64Offline) {
    $bootstrapRoot = Join-Path $resourcesRoot "bootstrap"
    $bootstrapWheelhouse = Join-Path $bootstrapRoot "wheelhouse"
    New-Item -ItemType Directory -Path $bootstrapWheelhouse -Force | Out-Null
    Copy-Item -LiteralPath $runtimeArchivePath -Destination (Join-Path $bootstrapRoot ([string]$runtimeManifest.filename)) -Force
    Copy-Item -LiteralPath (Join-Path $SourceRoot "macos\runtime-arm64.json") -Destination (Join-Path $bootstrapRoot "runtime-manifest.json") -Force
    Get-ChildItem -LiteralPath $wheelCache -File -Filter "*.whl" | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $bootstrapWheelhouse $_.Name) -Force
    }
    $wheelChecksumLines = @(Get-ChildItem -LiteralPath $bootstrapWheelhouse -File -Filter "*.whl" | Sort-Object Name | ForEach-Object {
        $wheelHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$wheelHash  $($_.Name)"
    })
    if ($wheelChecksumLines.Count -eq 0) { throw "macOS arm64 wheelhouse is empty" }
    Write-Utf8NoBom (Join-Path $bootstrapRoot "wheelhouse.sha256") (($wheelChecksumLines -join "`n") + "`n")

    $launcher = Get-Content -LiteralPath (Join-Path $SourceRoot "macos\SynCanvas-arm64") -Raw -Encoding UTF8
    $launcher = $launcher.Replace("__PYTHON_RUNTIME_ID__", [string]$runtimeManifest.runtime_id)
    $launcher = $launcher.Replace("__PYTHON_ARCHIVE__", [string]$runtimeManifest.filename)
    $launcher = $launcher.Replace("__PYTHON_SHA256__", [string]$runtimeManifest.sha256)
    Write-Utf8NoBom (Join-Path $contentsRoot "MacOS\SynCanvas") $launcher
    Copy-Item -LiteralPath (Join-Path $SourceRoot "macos\README-macOS-arm64.txt") -Destination (Join-Path $dmgRoot "README-macOS.txt") -Force
} else {
    Copy-Item -LiteralPath (Join-Path $SourceRoot "macos\SynCanvas") -Destination (Join-Path $contentsRoot "MacOS\SynCanvas") -Force
    Copy-Item -LiteralPath (Join-Path $SourceRoot "macos\README-macOS.txt") -Destination (Join-Path $dmgRoot "README-macOS.txt") -Force
}

if (-not $SkipVerify) {
    & $preflightPython (Join-Path $coreRoot "tools\release_smoke_test.py") --root $coreRoot
    if ($LASTEXITCODE -ne 0) { throw "macOS core smoke test failed" }
    foreach ($relative in @("API", "data", "components", "output", "packages\components", "workflows", "assets\input", "assets\output")) {
        $path = Join-Path $coreRoot $relative
        if (Test-Path -LiteralPath $path) { Remove-BuildPath $path $coreRoot }
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    Get-ChildItem -LiteralPath $coreRoot -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-BuildPath $_.FullName $coreRoot }
}

$dockerBuildArgs = @("build")
if ($PullBuilderImage) {
    $dockerBuildArgs += "--pull"
}
$dockerBuildArgs += @("-t", $DockerImage, (Join-Path $SourceRoot "tools\macos-dmg-builder"))
& docker @dockerBuildArgs
if ($LASTEXITCODE -ne 0) { throw "macOS DMG builder image failed" }

$dmgName = if ($isArm64Offline) {
    "SynCanvas-$Version-macos-arm64-offline-unsigned.dmg"
} else {
    "SynCanvas-$Version-universal-unsigned.dmg"
}
$dmgPath = Join-Path $OutputDir $dmgName
if (Test-Path -LiteralPath $dmgPath) { Remove-Item -LiteralPath $dmgPath -Force }
$dockerRunArgs = @(
    "run",
    "--rm",
    "-e", "DMG_NAME=$dmgName",
    "-e", "VOLUME_NAME=SynCanvas $Version",
    "-v", "${dmgRoot}:/input:ro",
    "-v", "${OutputDir}:/output",
    $DockerImage
)
& docker @dockerRunArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $dmgPath)) { throw "DMG creation failed" }

$hash = (Get-FileHash -LiteralPath $dmgPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $dmgName" | Set-Content -LiteralPath "$dmgPath.sha256" -Encoding ASCII
$releaseIndex = [ordered]@{
    schema_version = 1
    version = $Version
    created_at = $ReleaseTimestamp
    platform = if ($isArm64Offline) { "macos-arm64" } else { "macos-universal" }
    architecture = if ($isArm64Offline) { "arm64" } else { "universal-bootstrap" }
    packaging = if ($isArm64Offline) { "dmg-offline" } else { "dmg-bootstrap" }
    filename = $dmgName
    size = [int64](Get-Item -LiteralPath $dmgPath).Length
    sha256 = $hash
    signed = $false
    notarized = $false
    minimum_macos = "11.0"
    python_requirement = if ($isArm64Offline) { $null } else { ">=3.10" }
    bundled_python = if ($isArm64Offline) { [string]$runtimeManifest.python_version } else { $null }
    offline_dependencies = [bool]$isArm64Offline
    wheel_count = if ($isArm64Offline) { @(Get-ChildItem -LiteralPath $bootstrapWheelhouse -File -Filter "*.whl").Count } else { 0 }
    limitations = @("digital-human-win-only", "node-engine-win-only")
}
Write-Utf8NoBom (Join-Path $OutputDir "release-index.json") ($releaseIndex | ConvertTo-Json -Depth 6)
$releaseReadme = if ($isArm64Offline) { "macos\README-macOS-arm64.txt" } else { "macos\README-macOS.txt" }
Copy-Item -LiteralPath (Join-Path $SourceRoot $releaseReadme) -Destination (Join-Path $OutputDir "README-macOS.txt") -Force

if (-not $KeepStage) {
    Remove-BuildPath $buildRoot $OutputDir
}

Write-Host "macOS DMG is ready: $dmgPath"
Write-Host "SHA-256: $hash"
