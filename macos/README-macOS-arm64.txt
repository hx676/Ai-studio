SynCanvas macOS Apple Silicon Edition

1. This build supports Apple Silicon Macs only (M1 and newer).
2. Drag SynCanvas.app into the Applications folder.
3. On first launch, right-click SynCanvas.app and choose Open.
4. SynCanvas includes an arm64 Python runtime and all locked Python dependencies. No system Python or online dependency installation is required.
5. The first launch extracts the bundled runtime into ~/Library/Application Support/SynCanvas and may take a little longer.
6. When startup finishes, SynCanvas opens http://127.0.0.1:3000/ automatically.
7. Run Stop-SynCanvas.command from the disk image to stop the background service.

Current limitations:
- Digital-human TTS and HeyGem runtimes are Windows/CUDA-only and unavailable on macOS.
- The current bundled Comfy-compatible node-engine runtime is Windows-only and unavailable on macOS.
- This image is unsigned and not notarized. A production public release must be signed, notarized, and tested on a real Apple Silicon Mac.

User data:
~/Library/Application Support/SynCanvas

Launcher log:
~/Library/Application Support/SynCanvas/logs/launcher.log

Service log:
~/Library/Application Support/SynCanvas/logs/service.log
