"""Python launcher for the Bun-powered frag-demo app."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    """Launch the Bun server entrypoint for the desktop app."""
    root = _project_root()
    if not (root / "package.json").exists():
        raise SystemExit(
            "package.json was not found next to the Python package. "
            "Run this command from the repository checkout."
        )

    bun = shutil.which("bun")
    if bun is None:
        raise SystemExit("bun is required to launch frag-demo. Install Bun and try again.")

    script = os.environ.get("FRAG_DEMO_NODE_SCRIPT", "start").strip() or "start"
    raise SystemExit(subprocess.call([bun, "run", script], cwd=root))


if __name__ == "__main__":
    main()
