"""Which build is this, and where does it keep its data — shown in Settings."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config

_cached_build: str | None = None


def build_id() -> str:
    """Short commit (or the build stamp baked in at packaging time)."""
    global _cached_build
    if _cached_build:
        return _cached_build
    stamp = os.environ.get("SSHDECK_BUILD", "").strip()
    if not stamp:
        for candidate in (config.BASE_DIR / "BUILD.txt", config.BASE_DIR.parent / "BUILD.txt"):
            if candidate.exists():
                stamp = candidate.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                break
    if not stamp:
        try:
            stamp = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=config.BASE_DIR,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            stamp = ""
    _cached_build = stamp or "dev"
    return _cached_build


def portable() -> bool:
    bundle = config.BASE_DIR.parent if config.BASE_DIR.name == "app" else None
    return bool(bundle and (bundle / "MUXIVRA.exe").exists())


def info() -> dict:
    from . import __version__

    return {
        "version": __version__,
        "build": build_id(),
        "mode": "portable" if portable() else "source",
        "data_dir": str(config.DATA_DIR),
        "staging_dir": str(config.STAGING_DIR),
        "program_dir": str(config.BASE_DIR),
        "python": os.sys.version.split()[0],
        "platform": os.sys.platform,
    }


def data_dir_owner() -> Path:
    return config.DATA_DIR
