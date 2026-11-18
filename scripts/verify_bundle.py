"""Verify that a built Windows bundle matches the current source tree.

Compares every shipped .py/.js/.css/.html file against the repo, and checks the
bundle hygiene rules (no Wine shim, no __pycache__, no account database).
"""
from __future__ import annotations

import hashlib
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "dist" / "MUXIVRA-portable-win64"
ARCHIVE = ROOT / "dist" / "MUXIVRA-portable-win64.zip"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if not BUNDLE.exists():
        print("no bundle found — run scripts/build_windows_portable.py")
        return 2

    problems: list[str] = []
    compared = 0

    for source_dir, shipped_dir in (("sshdeck", "app/sshdeck"), ("web", "app/web")):
        for src in sorted((ROOT / source_dir).rglob("*")):
            if not src.is_file() or "__pycache__" in src.parts or src.suffix == ".pyc":
                continue
            rel = src.relative_to(ROOT / source_dir)
            shipped = BUNDLE / shipped_dir / rel
            if not shipped.exists():
                problems.append(f"missing in bundle: {shipped_dir}/{rel}")
                continue
            if sha(src.read_bytes()) != sha(shipped.read_bytes()):
                problems.append(f"stale content: {shipped_dir}/{rel}")
                continue
            compared += 1

    for banned in ("runtime/python/bcryptprimitives.dll", "app/data/sshdeck.db", "data/sshdeck.db"):
        if (BUNDLE / banned).exists():
            problems.append(f"must not ship: {banned}")

    caches = [p for p in BUNDLE.rglob("__pycache__") if p.is_dir()]
    if caches:
        problems.append(f"{len(caches)} __pycache__ dirs left in bundle")

    if ARCHIVE.exists():
        names = zipfile.ZipFile(ARCHIVE).namelist()
        if any("bcryptprimitives" in n for n in names):
            problems.append("archive contains the Wine shim")
        for required in ("MUXIVRA.exe", "MUXIVRA.cmd", "BUILD.txt", "update-pubkey.txt",
                         "app/sshdeck/keeper.py", "app/sshdeck/transports.py", "app/sshdeck/update.py",
                         "app/sshdeck/buildinfo.py", "app/sshdeck/racks.py",
                         "app/web/netdoc.js", "app/web/netdoc.css", "app/web/diagram.js", "app/web/diagram.css",
                         "runtime/python/python.exe", "runtime/libs/fastapi/__init__.py"):
            if not any(n.endswith(required) for n in names):
                problems.append(f"archive missing {required}")
        libs = sum(1 for n in names if n.startswith("MUXIVRA-portable-win64/runtime/libs/"))
    else:
        problems.append("archive missing — rebuild")

    print(f"compared {compared} source files")
    if problems:
        for problem in problems:
            print("FAIL", problem)
        return 1
    print("bundle matches source; hygiene checks pass")
    if ARCHIVE.exists():
        print(f"archive entries: {len(names)}, libs files: {libs}, "
              f"size: {ARCHIVE.stat().st_size/1024/1024:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
