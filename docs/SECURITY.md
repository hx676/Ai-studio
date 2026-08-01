# SynCanvas security boundary

SynCanvas is a local desktop application. The main HTTP service binds to `127.0.0.1` by default and refuses non-loopback values in `SYNCANVAS_MAIN_HOST`. This release does not provide authenticated LAN access.

Browser writes and WebSocket connections validate `Host` and `Origin`. Same-origin browser calls are accepted; requests without `Origin` remain available to the local launcher and native plugins. Optional `SYNCANVAS_ALLOWED_ORIGINS` entries must also be loopback origins.

API credentials stay in `API/.env`. `/api/config/token` exposes only whether ModelScope is configured, never the token itself. Legacy `api_key` request fields remain parseable for old clients but ModelScope execution ignores them. Logs, task records and HTTP error details redact common credential forms.

Workflow imports are untrusted input. Current limits are:

- JSON: 32 MiB;
- compressed ZIP: 512 MiB;
- 2,000 ZIP entries;
- 500 MiB per resource and 2 GiB expanded total;
- abnormal compression ratios are rejected;
- symbolic links, path traversal and undeclared archive resources are rejected;
- 5,000 nodes and 20,000 connections per imported graph.

General uploads are limited to 500 MiB per file and 1 GiB per request. Large uploads and exports use spooled files or streaming paths instead of building a complete archive in memory.

Custom nodes and node-engine extensions are trusted local code. Process isolation limits crashes and dependency conflicts; it is not a security sandbox. The node engine is a separately installed GPL-3.0 component with its own source/version/license metadata.

Do not publish a build with a non-loopback bind override, empty download SHA-256, missing node-engine source version, or a missing license file.
