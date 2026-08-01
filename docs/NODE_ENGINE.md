# SynCanvas Node Engine

## Scope

The node engine is an optional, isolated runtime used to execute compatible Comfy nodes from the Classic and Smart canvases. Users interact with SynCanvas only; the runtime process, catalog, models, extensions, tasks, and output files are managed by SynCanvas.

The V1 compatibility target is standard backend nodes. Extensions that depend on custom Comfy frontend JavaScript, overlays, interactive selectors, or automatic graph mutation are marked `limited`. A limited node may execute, but its original Comfy frontend is not loaded inside SynCanvas.

## Isolation

- Runtime program: `components/node-engine/runtime/`
- Models and read-only model configuration: `data/node-engine/models/` and `data/node-engine/extra_model_paths.yaml`
- Runtime extensions: `data/node-engine/custom_nodes/`
- Disabled extensions: `data/node-engine/disabled_custom_nodes/`
- Input, output, logs, and persistent run records: `data/node-engine/`

The runtime uses its own Python, Torch, CUDA dependencies, model cache, and process. Extension requirements are installed only into the runtime Python environment. Enabling, disabling, installing, upgrading, or deleting a runtime extension restarts only the node engine.

Process isolation is fault isolation, not a security sandbox. Comfy custom nodes are local trusted code and can access the current Windows user account. Only install extensions from sources you trust.

## Canvas Contract

- Both canvases save runtime nodes as `syncanvas.node-engine/runtime-node`.
- Node pickers default to **Canvas Utility** scope. This includes nodes whose ports use only image, mask, audio, video, text, number, boolean, or local enum values, so they can operate without a Comfy model library.
- Nodes with opaque model-graph ports such as `MODEL`, `CLIP`, `VAE`, `LATENT`, or `CONDITIONING` remain available under **All Nodes** for advanced workflows and existing saved canvases.
- A node stores `classType`, widget values, input modes, a lightweight definition snapshot, and a definition fingerprint.
- Scalar inputs are widgets by default. Switching one to port mode removes its widget value from the submitted graph and requires a connected value when the input is required.
- Runtime-only values such as `MODEL`, `LATENT`, and `CONDITIONING` can connect only inside one runtime island.
- Images, masks, audio, video, text, numbers, booleans, and JSON-compatible values cross the SynCanvas boundary through managed bridge nodes.
- A connected runtime island is compiled and submitted as one graph. It is never executed one node at a time.
- Engine events map queueing, model loading, node progress, cache hits, completion, cancellation, and errors back to the corresponding canvas nodes.

## Management

Open **Settings > Node Engine** to:

- browse Canvas Utility nodes by default and opt into the complete catalog when needed;
- install a local portable runtime, start or stop it, and rescan the node catalog;
- copy models into the managed model library with SHA-256 verification;
- add external read-only model roots without copying model files;
- install extensions from a local directory, ZIP archive, or HTTPS Git repository;
- install dependencies, enable, disable, upgrade, or delete runtime extensions.

Models are optional. Image, mask, text, scalar, audio, and video utility nodes do not require a configured Comfy model directory. The model manager is an advanced surface for users who intentionally build model graphs.

Model import tasks and extension tasks are persisted. A non-terminal task found after a SynCanvas restart is marked `interrupted` instead of being reported as still running.

## Security Boundaries

- Model destination paths are constrained to the selected managed category.
- Read-only model paths must resolve below their declared source root.
- Model symlinks and unsupported model suffixes are ignored or rejected.
- Extension ZIP entries may not escape the staging directory or create symbolic links.
- Remote extension sources must use HTTPS.
- Managed bridge, model, extension, task, and output directories are separate from the installed runtime program.
- SynCanvas verifies copied model contents with SHA-256 before registering them.
- SynCanvas never terminates an unverified PID merely because it matches a previously stored process number.

## GPL Distribution Gate

ComfyUI is GPL-3.0. The optional node engine must remain a separately installed component with its original license and source notices. It must not be copied into the restricted-license SynCanvas application tree as if it were SynCanvas-owned code.

Before publishing a downloadable node-engine artifact:

1. Pin the exact upstream commit or release in `node-engine-manifest.json` as `source_version`.
2. Set `source_url` and, when needed, `source_offer_url` to the corresponding complete source.
3. Preserve the runtime `LICENSE` file and all third-party notices in the artifact.
4. Publish the artifact SHA-256 and use the same version in the installed source record.
5. Review the distribution with qualified license counsel.

The installer rejects a downloadable artifact configuration that has URLs and a SHA-256 but lacks an exact source version or source URL. A user-supplied local runtime is recorded as `local-import-unpinned` unless the local manifest identifies its exact source version.

## V1 Limits

- No extension marketplace or arbitrary online ZIP installer.
- No execution of Comfy custom frontend JavaScript.
- No promise that every third-party node can load or execute.
- No serialization of runtime-only Python or GPU objects through the SynCanvas backend.
- Real GPU acceptance still requires a compatible portable runtime, models, CUDA stack, and at least one supported GPU extension on the test machine.
