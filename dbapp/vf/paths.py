"""Project paths.

Two different roots matter:

* ``code_root()``  : the checkout this file lives in. In a git worktree this is
  the worktree. Use it for code and for files that are committed (app data).
* ``data_dir()``   : ONE shared data folder for every agent and every worktree.
  It is not versioned (see .gitignore), so worktrees would otherwise not see it.

Resolution order for the shared root:
1. env ``VF_DATA_DIR`` (points straight at the data folder)
2. env ``VF_ROOT`` (project root, data is ``$VF_ROOT/data``)
3. the main checkout found through ``git rev-parse --git-common-dir``
4. the checkout this file lives in
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


def code_root() -> Path:
    """Root of the checkout (or worktree) that contains this file."""
    return Path(__file__).resolve().parents[2]


def _looks_like_project(p: Path) -> bool:
    return (p / "CLAUDE.md").exists() and (p / "src" / "vf").exists()


@lru_cache(maxsize=1)
def shared_root() -> Path:
    """Root of the MAIN checkout, shared by all worktrees."""
    env = os.environ.get("VF_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = code_root()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=here,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            common = Path(out)
            if common.name == ".git" and _looks_like_project(common.parent):
                return common.parent
    except Exception:
        pass
    return here


def data_dir() -> Path:
    env = os.environ.get("VF_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return shared_root() / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def external_dir() -> Path:
    return data_dir() / "external"


def silver_dir() -> Path:
    return data_dir() / "silver"


def gold_dir() -> Path:
    return data_dir() / "gold"


def labels_dir() -> Path:
    return data_dir() / "labels"


def app_data_dir() -> Path:
    """Committed, static data served by the demo app (per checkout, not shared)."""
    return code_root() / "app" / "public" / "data"


def ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
