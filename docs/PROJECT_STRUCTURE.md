# SynCanvas project structure

SynCanvas separates source code, optional components, and user-owned runtime data.
Only source code and component manifests belong in Git.

```text
app/                         FastAPI application and backend services
static/                      Browser UI, styles, scripts, and bundled web assets
custom_nodes/                SynCanvas native extension packages
launcher/                    Native Windows launcher source
tests/                       Python and frontend contract tests
tools/                       Development, release, and service-management tools
docs/                        Architecture and developer documentation
CLI/                         Optional CLI installation and login helpers

components/                  Installed optional runtimes; local and ignored
  digital-human/tts/         Managed IndexTTS component
  digital-human/heygem/      Managed HeyGem component
  node-engine/runtime/       Managed isolated node-engine runtime

data/                        User settings, canvases, conversations, and runtime state
assets/                      User-managed asset library
output/                      Generated exports
logs/                        Local development and acceptance logs
packages/                    Local/offline component archives
python/                      Bundled core Python runtime
```

## Ownership rules

- Never commit `components/`, `data/`, `assets/`, `output/`, `logs/`, `packages/`, or bundled Python files.
- Keep component metadata in the root manifests and installation state under `data/components/`.
- Keep SynCanvas extension source in `custom_nodes/`; Comfy-compatible runtime extensions live under `data/node-engine/custom_nodes/`.
- Root-level `index-tts-2/` and `heygem-win-fix/` are legacy locations only. New installs use `components/digital-human/`.
- User data is preserved during component repair, upgrade, and project cleanup.

## Runtime consistency

- `app.main` is the only production FastAPI application and lifecycle owner.
- Compatibility routes imported from `app.upstream_runtime` share the modular app's WebSocket manager, event loop, canvas/history locks, ComfyUI instance list, and GPU load state.
- New JSON persistence must use `app.core.json_store.atomic_write_json`; a route must not overwrite a user data JSON file directly.
- `app.upstream_runtime` is a read-only compatibility source. New product routes belong in an explicit `app/api/` router and service module.
- Classic and Smart Canvas keep their persisted node types for backward compatibility. Shared import, extension and runtime-node protocols live outside the surface-specific layout code.
