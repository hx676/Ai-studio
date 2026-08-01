# SynCanvas modular digital-human release

SynCanvas ships as a small core package. The IndexTTS and HeyGem runtimes are
optional artifacts installed from the Digital Human page.

Managed installations use these local directories:

- `components/digital-human/tts`
- `components/digital-human/heygem`

## Release artifacts

- `SynCanvas-Core-<version>.zip`: launcher, web app, canvases, bundled core
  Python, and the component installer.
- `SynCanvas-DigitalHuman-TTS-<version>.zip`: IndexTTS runtime and models.
- `SynCanvas-DigitalHuman-HeyGem-<version>.zip`: HeyGem runtime and models.
- `components-manifest.json`: immutable artifact names, sizes, download URLs,
  SHA256 values, sentinels, and install targets.

For the offline release, keep the TTS and HeyGem archives beside the extracted
SynCanvas core directory, copy them into the core directory itself, or place
them in `packages/components`. The installer discovers all three layouts.

Run:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_modular_release.ps1 `
  -ComponentBaseUrl "https://downloads.example.com/syncanvas/<version>"
```

For a fast core-only validation build:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_modular_release.ps1 `
  -SkipComponents
```

Component archives are downloaded sequentially with HTTP Range resume support.
Each archive is verified before extraction. Runtime files are staged and then
swapped into place. User voice and output directories are preserved during a
repair or component update.

The production manifest must contain a valid SHA256 and at least one URL for
every artifact. A mirror base URL can also be supplied at runtime through
`SYNCANVAS_COMPONENT_BASE_URL`.

Legacy installations containing root-level `index-tts-2` and
`heygem-win-fix/heygem-win` directories are still detected automatically.
Release builds prefer the managed component directories and fall back to these
legacy paths when preparing an upgrade from an older installation.
