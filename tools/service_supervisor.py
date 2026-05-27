"""Compatibility entrypoint for the SynCanvas service supervisor.

The implementation lives under tools.service_supervisor_parts so callers can
keep invoking this file directly while the large supervisor is split further.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.service_supervisor_parts.cli import *  # noqa: F401,F403,E402
from tools.service_supervisor_parts.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
