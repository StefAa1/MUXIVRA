"""Signed GitHub-release updates for the portable build.

Trust model: a release is only applicable when `release.json` carries a valid
Ed25519 signature from the pinned public key (data/update-pubkey.txt). Without
that key updates fail closed — an unsigned artifact is never applied.

The swap itself is done by a generated apply-update.cmd, because a running
process cannot overwrite its own binaries. Staging lives outside the bundle,
the script waits for THIS process id, and a failed copy is rolled back.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/{repo}/releases/latest"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ASSET_BYTES = 400 * 1024 * 1024
MAX_EXTRACTED_BYTES = 800 * 1024 * 1024
MAX_ENTRIES = 20000
ALLOWED_DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MANIFEST_NAME = "release.json"
SIGNATURE_NAME = "release.json.sig"


class UpdateError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def bundle_root() -> Path:
    """The portable bundle folder (the app lives in <bundle>/app)."""
    from . import config

    base = config.BASE_DIR
    return base.parent if base.name == "app" else base


def parse_version(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", (text or "").lstrip("vV"))
    return tuple(int(n) for n in numbers[:4]) if numbers else (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


def _open(url: str, timeout: float, accept: str = "application/vnd.github+json",
          token: str | None = None):
    headers = {"accept": accept, "user-agent": "muxivra-updater"}
    if token:
        # read-only token for a private repository; never logged, never stored in a URL
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def _allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("SSHDECK_UPDATE_HOSTS", "").split(",") if h.strip()}
    return ALLOWED_DOWNLOAD_HOSTS | extra


def _assert_https(url: str) -> None:
    """https only, and only hosts we trust. http needs the explicit test switch."""
    parts = urllib.parse.urlsplit(url)
    allow_http = os.environ.get("SSHDECK_UPDATE_ALLOW_HTTP") == "1"
    if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
        raise UpdateError("insecure_url", "Updates require https.")
    if (parts.hostname or "").lower() not in _allowed_hosts():
        raise UpdateError("untrusted_host", f"Download host not allowed: {parts.hostname}")


# ------------------------------------------------------------------ releases
def check(repo: str, current: str, timeout: float = 12.0, opener=None,
          token: str | None = None) -> dict:
    repo = (repo or "").strip()
    if not REPO_RE.match(repo):
        raise UpdateError("invalid_repo", "The repository must be in owner/name form.")
    opener = opener or _open
    try:
        try:
            response = opener(API.format(repo=repo), timeout, token=token) if token else opener(API.format(repo=repo), timeout)
        except TypeError:
            response = opener(API.format(repo=repo), timeout)   # test openers may not take a token
        with response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "repo_not_found",
                f"GitHub returned 404 for {repo}. If the repository is private, add a "
                "read-only token in Settings → Updates.",
            ) from exc
        raise UpdateError("http_error", f"GitHub responded {exc.code} for {repo}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("network", f"Could not connect to GitHub: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("bad_response", "Unreadable response from GitHub.") from exc

    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = [
        {
            "name": asset.get("name") or "",
            "url": asset.get("browser_download_url") or "",
            "size": int(asset.get("size") or 0),
            "digest": asset.get("digest") or "",
        }
        for asset in data.get("assets") or []
    ]
    by_name = {a["name"].lower(): a for a in assets}
    return {
        "repo": repo,
        "current": current,
        "latest": tag.lstrip("vV"),
        "available": is_newer(tag, current),
        "asset": next((a for a in assets if a["name"].lower().endswith(".zip")), None),
        "manifest": by_name.get(MANIFEST_NAME),
        "signature": by_name.get(SIGNATURE_NAME),
        "assets": assets,
        "notes": str(data.get("body") or "")[:2000],
        "published_at": data.get("published_at"),
    }


# --------------------------------------------------------------- signatures
def trusted_key() -> bytes:
    """Pinned Ed25519 public key (base64/hex), from env or data/update-pubkey.txt."""
    from . import config

    raw = os.environ.get("SSHDECK_UPDATE_PUBKEY", "").strip()
    if not raw and config.UPDATE_PUBKEY_PATH.exists():
        raw = config.UPDATE_PUBKEY_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        shipped = config.BASE_DIR / "update-pubkey.txt"
        if shipped.exists():
            raw = shipped.read_text(encoding="utf-8").strip()
    if not raw:
        raw = getattr(config, "DEFAULT_UPDATE_PUBKEY", "").strip()
    if not raw:
        raise UpdateError("no_pubkey", "No signing public key is configured.")
    for decoder in (base64.b64decode, bytes.fromhex):
        try:
            key = decoder(raw)
        except Exception:
            continue
        if len(key) == 32:
            return key
    raise UpdateError("bad_pubkey", "The public key is not a 32-byte Ed25519 key (base64 or hex).")


def verify_manifest(manifest_bytes: bytes, signature: bytes, pubkey: bytes | None = None) -> dict:
    """Verify the Ed25519 signature over the exact manifest bytes, then parse it."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pubkey = pubkey or trusted_key()
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise UpdateError("bad_signature", "The release signature does not verify.") from exc
    except ValueError as exc:
        raise UpdateError("bad_pubkey", "Invalid public key.") from exc
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("bad_manifest", "release.json is not valid JSON.") from exc
    for field in ("version", "archive", "sha256"):
        if not str(manifest.get(field) or "").strip():
            raise UpdateError("bad_manifest", f'release.json has no "{field}".')
    sha = str(manifest["sha256"]).strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise UpdateError("bad_manifest", "Invalid sha256 in release.json.")
    return manifest


def fetch_manifest(release: dict, opener=None, timeout: float = 30.0) -> dict:
    """Download release.json + its signature and verify them."""
    manifest_asset = release.get("manifest") or {}
    signature_asset = release.get("signature") or {}
    if not manifest_asset.get("url") or not signature_asset.get("url"):
        raise UpdateError("unsigned_release", "The release has no release.json / release.json.sig.")
    opener = opener or _open
    for asset in (manifest_asset, signature_asset):
        _assert_https(asset["url"])
        if int(asset.get("size") or 0) > 64 * 1024:
            raise UpdateError("too_large", "The manifest asset is too large.")
    with opener(manifest_asset["url"], timeout, "application/octet-stream") as response:
        manifest_bytes = response.read(64 * 1024)
    with opener(signature_asset["url"], timeout, "application/octet-stream") as response:
        signature_b64 = response.read(4096).strip()
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:
        raise UpdateError("bad_signature", "The signature is not base64.") from exc
    manifest = verify_manifest(manifest_bytes, signature)
    expected_tag = str(release.get("latest") or "").strip().lstrip("vV")
    if expected_tag and str(manifest["version"]).strip().lstrip("vV") != expected_tag:
        raise UpdateError("version_mismatch", "The manifest does not match the selected release.")
    return manifest


# ------------------------------------------------------------------ download
def download_verified(release: dict, manifest: dict, dest_dir: Path, opener=None,
                      timeout: float = 300.0, token: str | None = None) -> Path:
    """Fetch the signed archive and enforce the manifest's name and sha256."""
    asset = release.get("asset") or {}
    if not asset.get("url"):
        raise UpdateError("no_asset", "The release has no .zip asset.")
    _assert_https(asset["url"])
    wanted = str(manifest.get("archive") or "")
    if Path(asset.get("name") or "").name != Path(wanted).name:
        raise UpdateError("archive_mismatch", "The asset is not the file that was signed.")
    size = int(asset.get("size") or 0)
    if size > MAX_ASSET_BYTES:
        raise UpdateError("too_large", f"The asset is {size} bytes — over the limit.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(wanted).name
    hasher = hashlib.sha256()
    written = 0
    opener = opener or _open
    stream = None
    if token:
        try:
            stream = opener(asset["url"], timeout, "application/octet-stream", token=token)
        except TypeError:
            stream = None
    if stream is None:
        stream = opener(asset["url"], timeout, "application/octet-stream")
    with stream as response, open(target, "wb") as fh:
        while chunk := response.read(1024 * 256):
            written += len(chunk)
            if written > MAX_ASSET_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise UpdateError("too_large", "The download exceeded the size limit.")
            fh.write(chunk)
            hasher.update(chunk)
    if size and written != size:
        target.unlink(missing_ok=True)
        raise UpdateError("bad_size", "The file size does not match the release.")
    if hasher.hexdigest().lower() != str(manifest["sha256"]).lower():
        target.unlink(missing_ok=True)
        raise UpdateError("bad_digest", "The sha256 does not match the signed manifest.")
    return target


# -------------------------------------------------------------------- stage
def _check_members(zf: zipfile.ZipFile) -> None:
    """Reject absolute paths, traversal, drive letters and symlinks; cap size."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise UpdateError("too_many_entries", f"The zip has {len(infos)} entries.")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
            raise UpdateError("unsafe_path", f"Unsafe path in the zip: {name}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UpdateError("unsafe_path", f"Symlink inside the zip: {name}")
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise UpdateError("too_large", "The zip exceeds the decompression limit.")


def stage(archive: Path, staging_root: Path) -> Path:
    """Extract into the staging area (outside the bundle) with safety checks."""
    staging = staging_root / f"staged-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            _check_members(zf)
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                target = staging / name
                if not str(target.resolve()).startswith(str(staging.resolve())):
                    raise UpdateError("unsafe_path", f"Path outside staging: {name}")
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, open(target, "wb") as destination:
                    shutil.copyfileobj(source, destination)
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    roots = [p for p in staging.iterdir() if p.is_dir()]
    root = roots[0] if len(roots) == 1 and (roots[0] / "MUXIVRA.exe").exists() else staging
    if not (root / "MUXIVRA.exe").exists() and not (root / "MUXIVRA.cmd").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("bad_archive", "The zip does not contain MUXIVRA.exe / MUXIVRA.cmd.")
    return root


# ------------------------------------------------------------------- apply
def a_drive(path: Path) -> str:
    """Drive letter of a Windows path ('' when the path is not drive-qualified)."""
    text = str(path)
    return text[0].lower() if len(text) > 1 and text[1] == ":" else ""


def _cmd_path(path: Path) -> str:
    """Escape a path for a batch file: % is the only character batch expands."""
    return str(path).replace("%", "%%")


def write_apply_script(
    bundle: Path,
    new_root: Path,
    pid: int,
    staging_root: Path,
    backup_name: str | None = None,
    db_snapshot: Path | None = None,
    db_path: Path | None = None,
) -> Path:
    """Generate a .cmd that waits for THIS pid, swaps with backup/rollback/health check.

    Written as UTF-8 with `chcp 65001` so Greek folders work, with delayed
    expansion disabled so `!` in a path is not eaten, and `%` doubled.
    """
    bundle = bundle.resolve()
    new_root = Path(new_root).resolve()
    staging_root = Path(staging_root).resolve()
    backup = staging_root / (backup_name or f"backup-{time.strftime('%Y%m%d-%H%M%S')}")
    script = staging_root / "apply-update.cmd"
    script.parent.mkdir(parents=True, exist_ok=True)
    # the snapshot was taken by the app through the SQLite backup API (WAL-safe)
    snapshot_src = _cmd_path(db_snapshot) if db_snapshot else "%DBFILE%"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal DisableDelayedExpansion",
        f'set "OLD={_cmd_path(bundle)}"',
        f'set "NEW={_cmd_path(new_root)}"',
        f'set "BACKUP={_cmd_path(backup)}"',
        f'set "HEALTHLOG={_cmd_path(staging_root)}\\health.log"',
        'set "DBFILE=' + (_cmd_path(db_path) if db_path else "%LOCALAPPDATA%\\MUXIVRA\\data\\sshdeck.db") + '"',
        f"set PID={int(pid)}",
        "echo Waiting for MUXIVRA (pid %PID%) to close...",
        "echo (the app closes itself on newer builds; on older ones close the window)",
        "set /a TRIES=0",
        ":wait",
        'tasklist /FI "PID eq %PID%" 2>nul | find /I "%PID%" >nul',
        "if errorlevel 1 goto closed",
        "set /a TRIES+=1",
        "if %TRIES% GEQ 60 goto still_open",
        "  timeout /t 2 /nobreak >nul",
        "goto wait",
        ":still_open",
        "echo.",
        "echo MUXIVRA did not close within 2 minutes - nothing was changed.",
        "echo Close the MUXIVRA window and run this file again.",
        "pause",
        "exit /b 1",
        ":closed",
        'if not exist "%NEW%\\MUXIVRA.exe" (',
        "  echo The staged update is missing or incomplete - nothing was changed.",
        "  pause",
        "  exit /b 1",
        ")",
        'if exist "%BACKUP%" rmdir /S /Q "%BACKUP%"',
        "rem 0) the app is closed: take the freshest consistent copy of the database now",
        'if exist "%DBFILE%" copy /Y "%DBFILE%" "%BACKUP%-db.sqlite" >nul',
        f'if not exist "%BACKUP%-db.sqlite" if exist "{snapshot_src}" copy /Y "{snapshot_src}" "%BACKUP%-db.sqlite" >nul',
        "rem 1) COPY (never move) the install aside; robocopy 0-7 means success",
        'robocopy "%OLD%" "%BACKUP%" /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS >nul',
        "if errorlevel 8 (",
        "  echo Backup failed - the current install was not touched.",
        "  pause",
        "  exit /b 1",
        ")",
        'if not exist "%BACKUP%\\MUXIVRA.exe" (',
        "  echo Backup incomplete - the current install was not touched.",
        "  pause",
        "  exit /b 1",
        ")",
        "rem 2) copy the staged build over the installation",
        'robocopy "%NEW%" "%OLD%" /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS >nul',
        "if errorlevel 8 goto rollback",
        "rem 3) the new build must open its data, schema and vault",
        '"%OLD%\\runtime\\python\\python.exe" -m sshdeck healthcheck >"%HEALTHLOG%" 2>&1',
        "if errorlevel 1 goto rollback",
        'start "" "%OLD%\\MUXIVRA.exe"',
        'echo Update applied. Previous version kept in "%BACKUP%".',
        "goto done",
        ":rollback",
        "echo Update failed - restoring the previous version.",
        'robocopy "%BACKUP%" "%OLD%" /E /COPY:DAT /R:1 /W:1 /PURGE >nul',
        "if errorlevel 8 (",
        "  echo Restore of the previous binaries FAILED - backup kept at %BACKUP%.",
        "  pause",
        "  exit /b 1",
        ")",
        'if exist "%BACKUP%-db.sqlite" copy /Y "%BACKUP%-db.sqlite" "%DBFILE%" >nul',
        "if errorlevel 1 (",
        "  echo Restore of the database FAILED - backup kept at %BACKUP%-db.sqlite.",
        "  pause",
        "  exit /b 1",
        ")",
        'start "" "%OLD%\\MUXIVRA.exe"',
        ":done",
        "timeout /t 3 >nul",
        "(goto) 2>nul & del \"%~f0\" & exit /b 0",
        "",
    ]
    script.write_bytes("\r\n".join(lines).encode("utf-8"))
    return script


def cleanup_staging(staging_root: Path, keep_days: int = 14) -> None:
    if not staging_root.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for path in staging_root.glob("*"):
        try:
            if path.name == "apply-update.cmd":
                continue
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
        except OSError:
            continue
