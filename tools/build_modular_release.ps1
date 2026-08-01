[CmdletBinding()]
param(
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [string]$Version = "",
    [string]$ReleaseTimestamp = "",
    [string]$ComponentBaseUrl = "",
    [string]$SevenZipPath = "C:\Program Files\7-Zip\7z.exe",
    [switch]$SkipComponents,
    [switch]$SkipCore,
    [switch]$SkipVerify,
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"

if (-not $SourceRoot) {
    $SourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
}

function Resolve-AbsolutePath([string]$PathValue) {
    return [System.IO.Path]::GetFullPath($PathValue)
}

function Assert-ChildPath([string]$PathValue, [string]$ParentValue) {
    $path = Resolve-AbsolutePath $PathValue
    $parent = (Resolve-AbsolutePath $ParentValue).TrimEnd('\', '/')
    if ($path -eq $parent -or -not $path.StartsWith($parent + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build path: $path is not a child of $parent"
    }
    return $path
}

function Remove-BuildPath([string]$PathValue, [string]$BuildParent) {
    if (-not (Test-Path -LiteralPath $PathValue)) {
        return
    }
    $safePath = Assert-ChildPath $PathValue $BuildParent
    Remove-Item -LiteralPath $safePath -Recurse -Force
}

function Reset-StagedMutableDirectories([string]$StageRoot) {
    foreach ($relativeDir in @(
        "API",
        "assets\input",
        "assets\output",
        "components",
        "data",
        "output",
        "packages\components",
        "workflows"
    )) {
        $target = Join-Path $StageRoot $relativeDir
        if (Test-Path -LiteralPath $target) {
            Remove-BuildPath $target $StageRoot
        }
        New-Item -ItemType Directory -Path $target -Force | Out-Null
    }
}

function Remove-StagedSourceCaches([string]$StageRoot) {
    foreach ($relativeRoot in @("app", "custom_nodes", "tools")) {
        $scanRoot = Join-Path $StageRoot $relativeRoot
        if (-not (Test-Path -LiteralPath $scanRoot)) {
            continue
        }
        @(Get-ChildItem -LiteralPath $scanRoot -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue) |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object { Remove-BuildPath $_.FullName $StageRoot }
        Get-ChildItem -LiteralPath $scanRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }
}

function Invoke-Robocopy([string]$From, [string]$To, [string[]]$ExtraArgs = @()) {
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    $args = @(
        $From,
        $To,
        "/E",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP"
    ) + $ExtraArgs
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Robocopy failed for $From with exit code $LASTEXITCODE"
    }
}

function Get-DirectoryBytes([string]$RootPath, [string[]]$ExcludedPrefixes = @()) {
    $root = Resolve-AbsolutePath $RootPath
    $sum = 0L
    Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
        $excluded = $false
        foreach ($prefix in $ExcludedPrefixes) {
            if ($relative.StartsWith($prefix.Trim('/'), [StringComparison]::OrdinalIgnoreCase)) {
                $excluded = $true
                break
            }
        }
        if (-not $excluded) {
            $sum += [int64]$_.Length
        }
    }
    return $sum
}

function Get-ZipUncompressedBytes([string]$ArchivePath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        $sum = 0L
        foreach ($entry in $archive.Entries) {
            $sum += [int64]$entry.Length
        }
        return $sum
    }
    finally {
        $archive.Dispose()
    }
}

function Write-Sha256File([string]$ArtifactPath) {
    $hash = (Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $shaPath = "$ArtifactPath.sha256"
    "$hash  $([System.IO.Path]::GetFileName($ArtifactPath))" | Set-Content -LiteralPath $shaPath -Encoding ASCII
    return $hash
}

function Write-Utf8NoBomFile([string]$PathValue, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($PathValue, $Content, $encoding)
}

function Invoke-ArchiveTest([string]$ArchivePath) {
    if ($SkipVerify) {
        return
    }
    & $SevenZipPath t $ArchivePath -bb0
    if ($LASTEXITCODE -ne 0) {
        throw "Archive verification failed: $ArchivePath"
    }
}

function Assert-ReleaseManifest([object]$DigitalManifest, [object]$NodeManifest) {
    if (-not $NodeManifest.component.license -or $NodeManifest.component.license -ne "GPL-3.0") {
        throw "Node engine manifest must declare GPL-3.0"
    }
    foreach ($field in @("source_url", "source_version", "source_offer_url")) {
        if (-not [string]$NodeManifest.component.$field) {
            throw "Node engine manifest is missing $field"
        }
    }
    $nodeArtifact = $NodeManifest.component.artifact
    if (@($nodeArtifact.platforms).Count -eq 0) {
        throw "Node engine artifact must declare platforms"
    }
    if (@($nodeArtifact.urls).Count -gt 0 -and [string]$nodeArtifact.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Downloadable node engine artifact must include SHA-256"
    }
    foreach ($artifact in @($DigitalManifest.component.artifacts)) {
        if (@($artifact.platforms).Count -eq 0) {
            throw "Digital-human artifact $($artifact.id) must declare platforms"
        }
        $hasManualDownload = $artifact.manual_download -and [string]$artifact.manual_download.share_url
        if ((@($artifact.urls).Count -gt 0 -or $hasManualDownload) -and [string]$artifact.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
            throw "Downloadable component artifact $($artifact.id) must include SHA-256"
        }
    }
}

$SourceRoot = Resolve-AbsolutePath $SourceRoot
if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot "main.py"))) {
    throw "SynCanvas source root is invalid: $SourceRoot"
}
if (-not (Test-Path -LiteralPath $SevenZipPath)) {
    throw "7-Zip was not found: $SevenZipPath"
}
if (-not $Version) {
    $Version = (Get-Content -LiteralPath (Join-Path $SourceRoot "VERSION") -Raw).Trim()
}
if (-not $ReleaseTimestamp) {
    $ReleaseTimestamp = (Get-Date).ToString("o")
}
try {
    [DateTimeOffset]::Parse($ReleaseTimestamp) | Out-Null
}
catch {
    throw "ReleaseTimestamp must be a valid ISO-8601 timestamp"
}
if (-not $Version) {
    throw "Release version is empty"
}
if ($Version -notmatch '^\d{4}\.\d{2}\.\d{2}\.\d+$') {
    throw "Release version must use YYYY.MM.DD.N format: $Version"
}
if (-not $OutputDir) {
    $OutputDir = Join-Path (Split-Path -Parent $SourceRoot) "SynCanvas-Modular-$Version"
}
$OutputDir = Resolve-AbsolutePath $OutputDir
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$buildRoot = Assert-ChildPath (Join-Path $OutputDir ".build") $OutputDir
$coreStage = Assert-ChildPath (Join-Path $buildRoot "core") $buildRoot
$launcherStage = Assert-ChildPath (Join-Path $buildRoot "launcher") $buildRoot
Remove-BuildPath $buildRoot $OutputDir
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

$safeVersion = $Version -replace '[^0-9A-Za-z._-]', '-'
$componentVersion = if ($Version -match '^(\d{4})\.(\d{2})\.(\d{2})') {
    "$($Matches[1])$($Matches[2])$($Matches[3])"
}
else {
    $safeVersion
}
$heygemVideoOutputName = [string]([char]0x89C6) + [char]0x9891 + [char]0x8F93 + [char]0x51FA
$ttsFileName = "SynCanvas-DigitalHuman-TTS-$componentVersion.zip"
$heygemFileName = "SynCanvas-DigitalHuman-HeyGem-$componentVersion.zip"
$coreFileName = "SynCanvas-Core-$safeVersion.zip"
$ttsArchive = Join-Path $OutputDir $ttsFileName
$heygemArchive = Join-Path $OutputDir $heygemFileName
$coreArchive = Join-Path $OutputDir $coreFileName

$manifestTemplate = Get-Content -LiteralPath (Join-Path $SourceRoot "components-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $SkipComponents) {
    $manifestTemplate.component.version = $Version
}
$nodeManifestTemplate = Get-Content -LiteralPath (Join-Path $SourceRoot "node-engine-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-ReleaseManifest $manifestTemplate $nodeManifestTemplate

$preflightPython = Join-Path $SourceRoot "python\python.exe"
if (-not (Test-Path -LiteralPath $preflightPython)) {
    $preflightPython = (Get-Command python -ErrorAction Stop).Source
}
& $preflightPython (Join-Path $SourceRoot "tools\release_preflight.py") --root $SourceRoot
if ($LASTEXITCODE -ne 0) {
    throw "Source release preflight failed"
}

if (-not $SkipComponents) {
    $managedDigitalHumanRoot = Join-Path $SourceRoot "components\digital-human"
    $managedTtsRoot = Join-Path $managedDigitalHumanRoot "tts"
    $managedHeyGemRoot = Join-Path $managedDigitalHumanRoot "heygem"
    $legacyTtsRoot = Join-Path $SourceRoot "index-tts-2"
    $legacyHeyGemRoot = Join-Path $SourceRoot "heygem-win-fix\heygem-win"
    $ttsRoot = if (Test-Path -LiteralPath (Join-Path $managedTtsRoot "py312\python.exe")) {
        $managedTtsRoot
    }
    else {
        $legacyTtsRoot
    }
    $heygemRoot = if (Test-Path -LiteralPath (Join-Path $managedHeyGemRoot "py38\python.exe")) {
        $managedHeyGemRoot
    }
    else {
        $legacyHeyGemRoot
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ttsRoot "py312\python.exe"))) {
        throw "TTS runtime is incomplete: $ttsRoot"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $heygemRoot "py38\python.exe"))) {
        throw "HeyGem runtime is incomplete: $heygemRoot"
    }

    foreach ($artifactPath in @($ttsArchive, $heygemArchive)) {
        if (Test-Path -LiteralPath $artifactPath) {
            Remove-Item -LiteralPath $artifactPath -Force
        }
        if (Test-Path -LiteralPath "$artifactPath.sha256") {
            Remove-Item -LiteralPath "$artifactPath.sha256" -Force
        }
    }

    Write-Host "Building TTS component: $ttsArchive"
    Push-Location $ttsRoot
    try {
        & $SevenZipPath a -tzip -mx=5 -mmt=on -mcu=on -bb0 $ttsArchive "*" `
            "-xr!assets\bak\*" `
            "-xr!voices\*" `
            "-xr!outputs\*" `
            "-xr!tmp\*" `
            "-xr!examples\*" `
            "-xr!data\log\*" `
            "-xr!py312\etc\aau_token" `
            "-xr!*.log"
        if ($LASTEXITCODE -ne 0) {
            throw "TTS component archive failed"
        }
    }
    finally {
        Pop-Location
    }
    Invoke-ArchiveTest $ttsArchive
    $ttsHash = Write-Sha256File $ttsArchive

    Write-Host "Building HeyGem component: $heygemArchive"
    Push-Location $heygemRoot
    try {
        & $SevenZipPath a -tzip -mx=5 -mmt=on -mcu=on -bb0 $heygemArchive "*" `
            "-xr!save\*" `
            "-xr!$heygemVideoOutputName\*" `
            "-xr!result\*" `
            "-xr!tmp\*" `
            "-xr!data\log\*" `
            "-xr!py38\etc\aau_token" `
            "-xr!*.log" `
            "-xr!*.wav" `
            "-xr!*.mp3"
        if ($LASTEXITCODE -ne 0) {
            throw "HeyGem component archive failed"
        }
    }
    finally {
        Pop-Location
    }
    Invoke-ArchiveTest $heygemArchive
    $heygemHash = Write-Sha256File $heygemArchive

    $ttsArtifact = $manifestTemplate.component.artifacts | Where-Object { $_.id -eq "tts" }
    $heygemArtifact = $manifestTemplate.component.artifacts | Where-Object { $_.id -eq "heygem" }
    $ttsArtifact.version = $Version
    $ttsArtifact.filename = $ttsFileName
    $ttsArtifact.download_size = [int64](Get-Item -LiteralPath $ttsArchive).Length
    $ttsArtifact.installed_size = Get-ZipUncompressedBytes $ttsArchive
    $ttsArtifact.sha256 = $ttsHash
    $heygemArtifact.version = $Version
    $heygemArtifact.filename = $heygemFileName
    $heygemArtifact.download_size = [int64](Get-Item -LiteralPath $heygemArchive).Length
    $heygemArtifact.installed_size = Get-ZipUncompressedBytes $heygemArchive
    $heygemArtifact.sha256 = $heygemHash
    # Fresh component archives have not been uploaded yet. Never carry a previous
    # release's manual share link into a manifest with a new checksum.
    $ttsArtifact.manual_download = $null
    $heygemArtifact.manual_download = $null
    if ($ComponentBaseUrl) {
        $baseUrl = $ComponentBaseUrl.TrimEnd("/")
        $ttsArtifact.urls = @("$baseUrl/$ttsFileName")
        $heygemArtifact.urls = @("$baseUrl/$heygemFileName")
    }
    else {
        $ttsArtifact.urls = @()
        $heygemArtifact.urls = @()
    }
    $manifestTemplate.component.download_size = [int64]$ttsArtifact.download_size + [int64]$heygemArtifact.download_size
    $manifestTemplate.component.installed_size = [int64]$ttsArtifact.installed_size + [int64]$heygemArtifact.installed_size
}
else {
    # The digital-human artifacts are independently versioned. A core-only
    # release must preserve their filename, version, checksum, and manual source.
    foreach ($artifact in $manifestTemplate.component.artifacts) {
        if ($ComponentBaseUrl) {
            $artifact.urls = @("$($ComponentBaseUrl.TrimEnd('/'))/$($artifact.filename)")
        }
    }
}

foreach ($artifact in $manifestTemplate.component.artifacts) {
    if ($artifact.manual_download -and [string]$artifact.manual_download.filename -ne [string]$artifact.filename) {
        $artifact.manual_download = $null
    }
}

Assert-ReleaseManifest $manifestTemplate $nodeManifestTemplate
$manifestOutput = Join-Path $OutputDir "components-manifest.json"
Write-Utf8NoBomFile $manifestOutput ($manifestTemplate | ConvertTo-Json -Depth 10)
$nodeManifestOutput = Join-Path $OutputDir "node-engine-manifest.json"
Write-Utf8NoBomFile $nodeManifestOutput ($nodeManifestTemplate | ConvertTo-Json -Depth 10)
$launcherMetadata = $null

if (-not $SkipCore) {
    Remove-BuildPath $coreStage $buildRoot
    New-Item -ItemType Directory -Path $coreStage -Force | Out-Null

    foreach ($dir in @("app", "static", "tools", "docs", "custom_nodes", "CLI")) {
        $sourceDir = Join-Path $SourceRoot $dir
        if (Test-Path -LiteralPath $sourceDir) {
            Invoke-Robocopy $sourceDir (Join-Path $coreStage $dir) @(
                "/XD", "__pycache__", ".ruff_cache", ".pytest_cache",
                "/XF", "*.pyc", "*.pyo", "*.log"
            )
        }
    }
    foreach ($dir in @("python")) {
        $sourceDir = Join-Path $SourceRoot $dir
        if (Test-Path -LiteralPath $sourceDir) {
            Invoke-Robocopy $sourceDir (Join-Path $coreStage $dir)
        }
    }

    $rootFiles = @(
        "main.py",
        "VERSION",
        "requirements.txt",
        "requirements.lock",
        "package.json",
        "package-lock.json",
        "tailwind.config.js",
        "README.md",
        "LICENSE",
        "components-manifest.json",
        "node-engine-manifest.json",
        "homepage.yml",
        "run.bat",
        "stop-services.bat"
    )
    foreach ($name in $rootFiles) {
        $sourceFile = Join-Path $SourceRoot $name
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $coreStage $name) -Force
        }
    }
    $shortcutBat = @(Get-ChildItem -LiteralPath $SourceRoot -File -Filter "*SynCanvas.bat" | Where-Object { $_.Name -ne "run.bat" })
    if ($shortcutBat.Count -ne 1) {
        throw "Expected exactly one SynCanvas convenience launcher batch file"
    }
    Copy-Item -LiteralPath $shortcutBat[0].FullName -Destination (Join-Path $coreStage $shortcutBat[0].Name) -Force
    Copy-Item -LiteralPath $manifestOutput -Destination (Join-Path $coreStage "components-manifest.json") -Force
    Copy-Item -LiteralPath $nodeManifestOutput -Destination (Join-Path $coreStage "node-engine-manifest.json") -Force

    $requiredCorePaths = @(
        "LICENSE",
        "components-manifest.json",
        "node-engine-manifest.json",
        "custom_nodes\syncanvas_agent_skill\node.json",
        "custom_nodes\syncanvas_image_compare\node.json",
        "custom_nodes\syncanvas_output_folder\node.json",
        "custom_nodes\syncanvas_runtime_node\node.json",
        "custom_nodes\syncanvas_templates\node.json",
        "CLI\windows\jimeng",
        "static\vendor\css\tailwind.css",
        "static\workflows\reference-style-prompt.classic.json",
        "static\workflows\reference-style-prompt.smart.json"
    )
    foreach ($relativePath in $requiredCorePaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $coreStage $relativePath))) {
            throw "Core release is missing required path: $relativePath"
        }
    }

    Reset-StagedMutableDirectories $coreStage

    Write-Host "Publishing launcher"
    Remove-BuildPath $launcherStage $buildRoot
    $launcherArtifacts = Assert-ChildPath (Join-Path $buildRoot "launcher-artifacts") $buildRoot
    & dotnet publish (Join-Path $SourceRoot "launcher\SynCanvasLauncher.csproj") `
        -c Release `
        -r win-x64 `
        --self-contained true `
        --artifacts-path $launcherArtifacts `
        -p:PublishSingleFile=true `
        -p:PublishReadyToRun=false `
        -p:AssemblyVersion=$Version `
        -p:FileVersion=$Version `
        -p:InformationalVersion=$Version `
        -o $launcherStage
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher publish failed"
    }
    $launcherExe = Join-Path $launcherStage "SynCanvasLauncher.exe"
    if (-not (Test-Path -LiteralPath $launcherExe)) {
        throw "Published launcher executable is missing"
    }
    $launcherFileVersion = [string](Get-Item -LiteralPath $launcherExe).VersionInfo.FileVersion
    if ($launcherFileVersion -ne $Version) {
        throw "Launcher file version $launcherFileVersion does not match release version $Version"
    }
    Copy-Item -LiteralPath $launcherExe -Destination (Join-Path $coreStage "SynCanvasLauncher.exe") -Force
    $launcherMetadata = [ordered]@{
        filename = "SynCanvasLauncher.exe"
        runtime_identifier = "win-x64"
        self_contained = $true
        file_version = $launcherFileVersion
        size = [int64](Get-Item -LiteralPath $launcherExe).Length
        sha256 = (Get-FileHash -LiteralPath $launcherExe -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    if (-not $SkipVerify) {
        & $preflightPython (Join-Path $coreStage "tools\release_preflight.py") --root $coreStage --stage --version $Version
        if ($LASTEXITCODE -ne 0) {
            throw "Staged release preflight failed"
        }
        $smokePython = Join-Path $coreStage "python\python.exe"
        if (-not (Test-Path -LiteralPath $smokePython)) {
            $smokePython = (Get-Command python -ErrorAction Stop).Source
        }
        & $smokePython (Join-Path $coreStage "tools\release_smoke_test.py") --root $coreStage
        if ($LASTEXITCODE -ne 0) {
            throw "Clean release smoke test failed"
        }
        Reset-StagedMutableDirectories $coreStage
        Remove-StagedSourceCaches $coreStage
        & $preflightPython (Join-Path $coreStage "tools\release_preflight.py") --root $coreStage --stage --version $Version
        if ($LASTEXITCODE -ne 0) {
            throw "Post-smoke staged release preflight failed"
        }
    }

    if (Test-Path -LiteralPath $coreArchive) {
        Remove-Item -LiteralPath $coreArchive -Force
    }
    if (Test-Path -LiteralPath "$coreArchive.sha256") {
        Remove-Item -LiteralPath "$coreArchive.sha256" -Force
    }
    Write-Host "Building core package: $coreArchive"
    Push-Location $coreStage
    try {
        & $SevenZipPath a -tzip -mx=5 -mmt=on -mcu=on -bb0 $coreArchive "*"
        if ($LASTEXITCODE -ne 0) {
            throw "Core archive failed"
        }
    }
    finally {
        Pop-Location
    }
    Invoke-ArchiveTest $coreArchive
    Write-Sha256File $coreArchive | Out-Null
}

$publishedComponentArtifacts = @(
    $manifestTemplate.component.artifacts | ForEach-Object {
        $artifactPath = Join-Path $OutputDir ([string]$_.filename)
        $manualDownload = $_.manual_download
        if ((Test-Path -LiteralPath $artifactPath) -or @($_.urls).Count -gt 0 -or ($manualDownload -and [string]$manualDownload.share_url)) {
            [ordered]@{
                id = $_.id
                filename = $_.filename
                size = [int64]$_.download_size
                sha256 = $_.sha256
                included = [bool](Test-Path -LiteralPath $artifactPath)
                manual_download = if ($manualDownload -and [string]$manualDownload.share_url) {
                    [ordered]@{
                        provider = [string]$manualDownload.provider
                        share_url = [string]$manualDownload.share_url
                        extraction_code = [string]$manualDownload.extraction_code
                    }
                } else { $null }
            }
        }
    }
)

$releaseIndex = [ordered]@{
    schema_version = 2
    version = $Version
    created_at = $ReleaseTimestamp
    platform = "win-x64"
    packaging = "portable-modular"
    core = if (Test-Path -LiteralPath $coreArchive) {
        [ordered]@{
            filename = $coreFileName
            size = [int64](Get-Item -LiteralPath $coreArchive).Length
            sha256 = (Get-FileHash -LiteralPath $coreArchive -Algorithm SHA256).Hash.ToLowerInvariant()
            launcher = $launcherMetadata
        }
    } else { $null }
    manifest = "components-manifest.json"
    node_engine_manifest = "node-engine-manifest.json"
    component_artifacts = $publishedComponentArtifacts
}
Write-Utf8NoBomFile (Join-Path $OutputDir "release-index.json") ($releaseIndex | ConvertTo-Json -Depth 8)

if (-not $KeepStage) {
    Remove-BuildPath $buildRoot $OutputDir
}

Write-Host ""
Write-Host "Modular release is ready: $OutputDir"
Get-ChildItem -LiteralPath $OutputDir -File | Sort-Object Name | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
