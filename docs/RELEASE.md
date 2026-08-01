# Release process

Windows EXE 的安装结构、升级保留规则、签名和阻断条件见
[`EXE_PACKAGING.md`](EXE_PACKAGING.md)。正式封装前先运行源码预检：

```powershell
python tools/release_preflight.py --root .
```

Run the full local gate before packaging:

```powershell
npm ci
npm run build:css
python -m pytest -q
python -m compileall -q app tests tools
Get-ChildItem static,custom_nodes,tools -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
node static/js/i18n/validate-i18n.js
python tools/check_mojibake.py --json
python tools/runtime_preflight.py --check
$launcherArtifacts = Join-Path $env:TEMP "SynCanvas-Launcher-Build"
dotnet build launcher/SynCanvasLauncher.csproj -c Release --artifacts-path $launcherArtifacts
```

Create a modular build with `tools/build_modular_release.ps1`. The script includes `custom_nodes/`, `CLI/`, both component manifests, dependency locks and licenses. It ignores the local generic `packages/` wheel cache, uses a curated root-file allowlist, builds a self-contained `win-x64` launcher, verifies archives and SHA-256 metadata, validates the node-engine GPL source offer, and starts the staged application from an empty `data/` directory before creating the core ZIP.

The clean-stage smoke test checks the Agent, AI Workflow and node-engine APIs plus the built-in Agent/AI Workflow, image-comparison and runtime-node packages. Launcher publishing uses an isolated artifacts directory so a running desktop launcher cannot lock the release build. Component GPU acceptance remains separate from ordinary CI.

Release archives must never contain user `data/`, `assets/`, outputs, logs, API credentials, imported models or extensions. Optional component repair and upgrade preserve those directories.

The portable build is the audited input for the installer. After installing Inno Setup 6, create the Windows setup EXE with:

```powershell
powershell -ExecutionPolicy Bypass -File tools/build_windows_installer.ps1
```

This emits `SynCanvas-Setup-<version>-win-x64.exe` and its SHA-256 file. Code signing is a separate release-owner step and must happen before publishing the final checksum.
