"""Build a portable, no-install Windows bundle of MUXIVRA.

Result: dist/MUXIVRA-portable-win64/ + dist/MUXIVRA-portable-win64.zip
Layout:
    MUXIVRA.cmd                     <- double-click launcher
    app/sshdeck/ app/web/           <- application code
    runtime/python/                 <- CPython embeddable distribution
    runtime/libs/                   <- win_amd64 wheels unpacked
    data/                           <- created on first run

Run from the project root:  .venv/bin/python scripts/build_windows_portable.py
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
BUNDLE_NAME = "MUXIVRA-portable-win64"
CANDIDATE_VERSIONS = ["3.12.8", "3.12.7", "3.11.9"]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}

LAUNCHER = """@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\\python\\pythonw.exe" goto missing
if not exist "data" mkdir "data"
start "" "runtime\\python\\pythonw.exe" -m sshdeck desktop %*
exit /b 0
:missing
echo.
echo   MUXIVRA cannot start: the application files are not in this folder.
echo   This happens when the .cmd is run from INSIDE the zip.
echo   1^) Right-click the downloaded zip ^> Extract All
echo   2^) Open the extracted MUXIVRA-portable-win64 folder
echo   3^) Run MUXIVRA.cmd from there
echo.
pause
exit /b 1
"""

LAUNCHER_DEBUG = """@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\\python\\python.exe" goto missing
title MUXIVRA debug - native window
"runtime\\python\\python.exe" -m sshdeck desktop --debug %*
echo.
echo   Exit code: %errorlevel%
echo   Log: %~dp0data\\desktop.log
pause
exit /b 0
:missing
echo   Extract the zip first, then run this file from the extracted folder.
pause
exit /b 1
"""

LAUNCHER_WEB = """@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\\python\\python.exe" goto missing
title MUXIVRA - web mode http://127.0.0.1:8722
"runtime\\python\\python.exe" -m sshdeck serve --host 127.0.0.1 --port 8722 --open-browser %*
pause
exit /b 0
:missing
echo   Extract the zip first, then run this file from the extracted folder.
pause
exit /b 1
"""

README_WIN = """MUXIVRA portable (Windows x64)
================================

Native desktop app: δικό του παράθυρο, χωρίς browser και χωρίς URL.
Δεν χρειάζεται εγκατάσταση, admin rights ή Python.

1. Διπλό κλικ  MUXIVRA.exe   (native παράθυρο, χωρίς console/browser)
2. Αν κάτι δεν ανοίξει: MUXIVRA-debug.cmd  (ίδιο mode, με console + devtools)
3. MUXIVRA-web.cmd = παλιά λειτουργία browser στο http://127.0.0.1:8722
4. Ενημερώσεις: Ρυθμίσεις → GitHub repo (owner/name) → Έλεγχος → Κατέβασε &amp; ετοίμασε,
   μετά κλείσε την εφαρμογή και τρέξε το apply-update.cmd που δημιουργείται δίπλα στο MUXIVRA.exe.

Το παράθυρο είναι Qt6 WebEngine μέσα στο bundle (δικό του Chromium) — δεν χρειάζεται
Edge, WebView2 runtime ή .NET. Αν το παράθυρο δεν ανοίξει, τρέξε MUXIVRA-debug.cmd
και στείλε το data\\sshdeck.log.

Data (hosts, κρυπτογραφημένα credentials, host keys) μένουν στο .\\data —
αντίγραφο του φακέλου = μεταφορά/backup (μαζί με το data\\vault.key).

Σειριακές θύρες: η εφαρμογή βλέπει τις COM του μηχανήματος όπου τρέχει.
"""


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def fetch_python(version: str) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    cache = BUILD / f"python-{version}-embed-amd64.zip"
    if cache.exists() and cache.stat().st_size > 1_000_000:
        return cache
    url = f"https://www.python.org/ftp/python/{version}/python-{version}-embed-amd64.zip"
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    cache.write_bytes(data)
    log(f"saved {cache.name} ({len(data)//1024} KiB)")
    return cache


def pick_version() -> str:
    for version in CANDIDATE_VERSIONS:
        try:
            fetch_python(version)
            return version
        except Exception as exc:  # try the next pinned version
            log(f"{version} unavailable: {exc}")
    raise SystemExit("no embeddable Python distribution could be downloaded")


def install_wheels(target: Path, py_version: str, requirements: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    cmd = [
        str(ROOT / ".venv/bin/pip"),
        "install",
        "--target", str(target),
        "--platform", "win_amd64",
        "--python-version", ".".join(py_version.split(".")[:2]),
        "--implementation", "cp",
        "--only-binary=:all:",
        "--no-compile",
        "--upgrade",
        "-r", str(requirements),
    ]
    log("pip install (win_amd64 wheels)")
    subprocess.run(cmd, check=True, capture_output=True)


def install_pywebview(target: Path, py_version: str, version: str = "6.2.1") -> None:
    """pywebview needs --no-deps: its dependency proxy-tools is sdist-only and would
    abort a --only-binary resolve. Its real dependencies are pinned in requirements-win.txt."""
    cmd = [
        str(ROOT / ".venv/bin/pip"),
        "install",
        "--target", str(target),
        "--platform", "win_amd64",
        "--python-version", ".".join(py_version.split(".")[:2]),
        "--implementation", "cp",
        "--only-binary=:all:",
        "--no-compile",
        "--no-deps",
        "--upgrade",
        f"pywebview=={version}",
    ]
    log("pip install pywebview (--no-deps)")
    subprocess.run(cmd, check=True, capture_output=True)


def vendor_sdist_dependency(target: Path, project: str, version: str, module: str) -> Path:
    """Copy a pure-python dependency that PyPI publishes only as an sdist."""
    api = f"https://pypi.org/pypi/{project}/{version}/json"
    with urllib.request.urlopen(api, timeout=60) as response:
        meta = json.loads(response.read())
    sdist = next((u for u in meta["urls"] if u["packagetype"] == "sdist"), None)
    if not sdist:
        raise SystemExit(f"{project} has no sdist to vendor")
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / sdist["filename"]
    if not archive.exists():
        urllib.request.urlretrieve(sdist["url"], archive)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp)
        root = next(p for p in Path(tmp).iterdir() if p.is_dir())
        package = root / module
        single = root / f"{module}.py"
        if package.is_dir():
            shutil.copytree(package, target / module, dirs_exist_ok=True)
            return target / module
        if single.exists():
            shutil.copy2(single, target / f"{module}.py")
            return target / f"{module}.py"
    raise SystemExit(f"{module} not found inside {archive.name}")


def copy_msvc_runtime(bundle: Path) -> None:
    """Qt6 links the MSVC runtime; Windows resolves it from the python.exe directory first."""
    libs = bundle / "runtime" / "libs"
    python_dir = bundle / "runtime" / "python"
    wanted = {"msvcp140", "msvcp140_1", "msvcp140_2", "concrt140", "vcruntime140", "vcruntime140_1"}
    copied = set()
    for dll in libs.rglob("*.dll"):
        if dll.stem.lower() in wanted:
            shutil.copy2(dll, python_dir / dll.name)
            copied.add(dll.name)
    log(f"msvc runtime dlls placed next to python.exe: {sorted(copied)}")


def build_launcher(bundle: Path) -> None:
    """Compile the native MUXIVRA.exe stub (or reuse a prebuilt one)."""
    source = ROOT / "tools" / "launcher.c"
    prebuilt = ROOT / "tools" / "MUXIVRA.exe"
    target = bundle / "MUXIVRA.exe"
    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if gcc and source.exists():
        subprocess.run(
            [gcc, "-O2", "-mwindows", "-o", str(target), str(source)],
            check=True,
            capture_output=True,
        )
        log(f"launcher compiled: {target.name} ({target.stat().st_size} bytes)")
    elif prebuilt.exists():
        shutil.copy2(prebuilt, target)
        log(f"launcher copied from tools/ ({target.stat().st_size} bytes)")
    else:
        log("WARNING: no mingw-w64 and no prebuilt launcher — bundle ships .cmd only")


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))


def patch_pth(python_dir: Path, py_version: str, bundle: Path) -> Path:
    tag = "python" + "".join(py_version.split(".")[:2])
    pth = python_dir / f"{tag}._pth"
    entries = [f"{tag}.zip", ".", r"..\libs", r"..\..\app", "import site"]
    pth.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return pth


def build() -> Path:
    version = pick_version()
    log(f"target bundle: {BUNDLE_NAME} (CPython {version} embeddable)")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    bundle = DIST / BUNDLE_NAME
    bundle.mkdir()

    py_dir = bundle / "runtime" / "python"
    py_dir.mkdir(parents=True)
    with zipfile.ZipFile(BUILD / f"python-{version}-embed-amd64.zip") as zf:
        zf.extractall(py_dir)
    log(f"python embeddable extracted ({len(list(py_dir.iterdir()))} files)")

    patch_pth(py_dir, version, bundle)

    install_wheels(bundle / "runtime" / "libs", version, ROOT / "requirements-win.txt")
    install_pywebview(bundle / "runtime" / "libs", version)
    vendored = vendor_sdist_dependency(bundle / "runtime" / "libs", "proxy-tools", "0.1.0", "proxy_tools")
    log(f"libs installed ({len(list((bundle / 'runtime' / 'libs').iterdir()))} entries, vendored {vendored.name})")
    copy_msvc_runtime(bundle)

    app_dir = bundle / "app"
    app_dir.mkdir()
    copy_tree(ROOT / "sshdeck", app_dir / "sshdeck")
    copy_tree(ROOT / "web", app_dir / "web")
    build_launcher(bundle)

    (bundle / "MUXIVRA.cmd").write_bytes(LAUNCHER.replace("\n", "\r\n").encode("ascii"))
    (bundle / "MUXIVRA-debug.cmd").write_bytes(LAUNCHER_DEBUG.replace("\n", "\r\n").encode("ascii"))
    (bundle / "MUXIVRA-web.cmd").write_bytes(LAUNCHER_WEB.replace("\n", "\r\n").encode("ascii"))
    (bundle / "README-WINDOWS.txt").write_text(README_WIN, encoding="utf-8")
    (bundle / "data").mkdir(exist_ok=True)

    archive = DIST / f"{BUNDLE_NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))
    log(f"archive: {archive} ({archive.stat().st_size/1024/1024:.1f} MiB)")
    return bundle


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-tree", action="store_true", help="leave the unpacked bundle in dist/")
    args = ap.parse_args()
    bundle = build()
    manifest = sorted(p.name for p in (bundle / "runtime" / "libs").iterdir())
    print("[build] top-level packages:", ", ".join(manifest[:18]))
    if not args.keep_tree:
        log("bundle folder kept (dist/) — delete manually if you only need the zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
