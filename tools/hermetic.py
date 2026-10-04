"""Keep a drill's git calls inside its temp dir: no inherited GIT_* variables (GIT_DIR would point
them at another repo) and no global or system git config (hooks, signing, url rewrites)."""
from __future__ import annotations

import os
from pathlib import Path


def isolate(home: Path) -> None:
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        del os.environ[k]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.environ["HOME"] = str(home)
