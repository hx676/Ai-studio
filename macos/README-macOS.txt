SynCanvas macOS Edition

1. Drag SynCanvas.app into the Applications folder.
2. On first launch, right-click SynCanvas.app and choose Open.
3. SynCanvas requires Python 3.10 or newer. The first launch creates an isolated environment and installs locked dependencies online.
4. When startup finishes, SynCanvas opens http://127.0.0.1:3000/ automatically.
5. Run Stop-SynCanvas.command from the disk image to stop the background service.

This bootstrap build works on Apple Silicon and Intel Macs because Python dependencies are installed for the current architecture.

Current limitations:
- Digital-human TTS and HeyGem runtimes are Windows/CUDA-only and unavailable on macOS.
- The current bundled Comfy-compatible node-engine runtime is Windows-only and unavailable on macOS.
- This image is unsigned and not notarized. A production public release must be signed, notarized, and tested on a real Mac.

User data:
~/Library/Application Support/SynCanvas

Launcher log:
~/Library/Application Support/SynCanvas/logs/launcher.log

Service log:
~/Library/Application Support/SynCanvas/logs/service.log
