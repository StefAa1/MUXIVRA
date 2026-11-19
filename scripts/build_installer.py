#!/usr/bin/env python3
"""Build the Windows installer (setup.exe) from the portable bundle.

The installer is a plain NSIS package: per-user install into %LOCALAPPDATA%,
no admin rights, Start-Menu/Desktop shortcuts, uninstaller that keeps the
user's data folder. It is built from the same bundle that ships as the ZIP, so
both artifacts contain identical application files.

    .venv/bin/python scripts/build_installer.py
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist" / "MUXIVRA-portable-win64"


def version() -> str:
    sys.path.insert(0, str(ROOT))
    import sshdeck

    return sshdeck.__version__


def main() -> int:
    if not BUNDLE.is_dir():
        print(f"bundle missing: {BUNDLE} — run scripts/build_windows_portable.py first")
        return 2
    if shutil.which("makensis") is None:
        print("makensis not found: apt-get install nsis")
        return 2

    # the installer must never carry user data or debug leftovers
    for unwanted in ("data", "app/data"):
        path = BUNDLE / unwanted
        if path.exists():
            print(f"refusing to package {unwanted}: remove it from the bundle first")
            return 2

    ver = version()
    out = ROOT / "dist" / f"MUXIVRA-Setup-{ver}.exe"
    if out.exists():
        out.unlink()

    command = [
        "makensis",
        "-V2",
        f"-DVERSION={ver}",
        f"-DBUNDLE_DIR={BUNDLE}",
        f"-DOUT_FILE={out}",
        str(ROOT / "packaging" / "installer.nsi"),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or not out.exists():
        print(output[-2000:])
        print("RESULT: FAIL")
        return 1

    body = out.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    pe = body[:2] == b"MZ"
    size = round(out.stat().st_size / 1024 / 1024, 1)
    print(f"installer : {out}")
    print(f"version   : {ver}")
    print(f"size      : {size} MiB")
    print(f"sha256    : {digest}")
    print(f"PE header : {'ok' if pe else 'MISSING'}")
    (out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print("checksum  :", out.parent / f"{out.name}.sha256")
    print("RESULT:", "PASS" if pe and size > 20 else "FAIL")
    return 0 if pe else 1


if __name__ == "__main__":
    raise SystemExit(main())
