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


def _open(url: str, timeout: float, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(
        url, headers={"accept": accept, "user-agent": "muxivra-updater"}
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _allowed_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("SSHDECK_UPDATE_HOSTS", "").split(",") if h.strip()}
    return ALLOWED_DOWNLOAD_HOSTS | extra


def _assert_https(url: str) -> None:
    """https only, and only hosts we trust. http needs the explicit test switch."""
    parts = urllib.parse.urlsplit(url)
    allow_http = os.environ.get("SSHDECK_UPDATE_ALLOW_HTTP") == "1"
    if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
        raise UpdateError("insecure_url", "Οι ενημερώσεις απαιτούν https.")
    if (parts.hostname or "").lower() not in _allowed_hosts():
        raise UpdateError("untrusted_host", f"Μη επιτρεπτός host λήψης: {parts.hostname}")


# ------------------------------------------------------------------ releases
def check(repo: str, current: str, timeout: float = 12.0, opener=None) -> dict:
    repo = (repo or "").strip()
    if not REPO_RE.match(repo):
        raise UpdateError("invalid_repo", "Το repository πρέπει να είναι της μορφής owner/name.")
    opener = opener or _open
    try:
        with opener(API.format(repo=repo), timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpdateError("http_error", f"GitHub απάντησε {exc.code} για {repo}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("network", f"Δεν έγινε σύνδεση με το GitHub: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("bad_response", "Μη αναγνώσιμη απάντηση από το GitHub.") from exc

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
        raise UpdateError("no_pubkey", "Δεν έχει οριστεί δημόσιο κλειδί υπογραφής.")
    for decoder in (base64.b64decode, bytes.fromhex):
        try:
            key = decoder(raw)
        except Exception:
            continue
        if len(key) == 32:
            return key
    raise UpdateError("bad_pubkey", "Το δημόσιο κλειδί δεν είναι 32-byte Ed25519 (base64 ή hex).")


def verify_manifest(manifest_bytes: bytes, signature: bytes, pubkey: bytes | None = None) -> dict:
    """Verify the Ed25519 signature over the exact manifest bytes, then parse it."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pubkey = pubkey or trusted_key()
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, manifest_bytes)
    except InvalidSignature as exc:
        raise UpdateError("bad_signature", "Η υπογραφή του release δεν επαληθεύεται.") from exc
    except ValueError as exc:
        raise UpdateError("bad_pubkey", "Άκυρο δημόσιο κλειδί.") from exc
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("bad_manifest", "Το release.json δεν είναι έγκυρο JSON.") from exc
    for field in ("version", "archive", "sha256"):
        if not str(manifest.get(field) or "").strip():
            raise UpdateError("bad_manifest", f"Το release.json δεν έχει «{field}».")
    sha = str(manifest["sha256"]).strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise UpdateError("bad_manifest", "Μη έγκυρο sha256 στο release.json.")
    return manifest


def fetch_manifest(release: dict, opener=None, timeout: float = 30.0) -> dict:
    """Download release.json + its signature and verify them."""
    manifest_asset = release.get("manifest") or {}
    signature_asset = release.get("signature") or {}
    if not manifest_asset.get("url") or not signature_asset.get("url"):
        raise UpdateError("unsigned_release", "Το release δεν έχει release.json / release.json.sig.")
    opener = opener or _open
    for asset in (manifest_asset, signature_asset):
        _assert_https(asset["url"])
        if int(asset.get("size") or 0) > 64 * 1024:
            raise UpdateError("too_large", "Το manifest asset είναι υπερβολικά μεγάλο.")
    with opener(manifest_asset["url"], timeout, "application/octet-stream") as response:
        manifest_bytes = response.read(64 * 1024)
    with opener(signature_asset["url"], timeout, "application/octet-stream") as response:
        signature_b64 = response.read(4096).strip()
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:
        raise UpdateError("bad_signature", "Η υπογραφή δεν είναι base64.") from exc
    manifest = verify_manifest(manifest_bytes, signature)
    expected_tag = str(release.get("latest") or "").strip().lstrip("vV")
    if expected_tag and str(manifest["version"]).strip().lstrip("vV") != expected_tag:
        raise UpdateError("version_mismatch", "Το manifest δεν ταιριάζει με το επιλεγμένο release.")
    return manifest


# ------------------------------------------------------------------ download
def download_verified(release: dict, manifest: dict, dest_dir: Path, opener=None, timeout: float = 300.0) -> Path:
    """Fetch the signed archive and enforce the manifest's name and sha256."""
    asset = release.get("asset") or {}
    if not asset.get("url"):
        raise UpdateError("no_asset", "Το release δεν έχει .zip asset.")
    _assert_https(asset["url"])
    wanted = str(manifest.get("archive") or "")
    if Path(asset.get("name") or "").name != Path(wanted).name:
        raise UpdateError("archive_mismatch", "Το asset δεν είναι το αρχείο που υπογράφηκε.")
    size = int(asset.get("size") or 0)
    if size > MAX_ASSET_BYTES:
        raise UpdateError("too_large", f"Το asset είναι {size} bytes — πάνω από το όριο.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(wanted).name
    hasher = hashlib.sha256()
    written = 0
    opener = opener or _open
    with opener(asset["url"], timeout, "application/octet-stream") as response, open(target, "wb") as fh:
        while chunk := response.read(1024 * 256):
            written += len(chunk)
            if written > MAX_ASSET_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise UpdateError("too_large", "Η λήψη ξεπέρασε το όριο μεγέθους.")
            fh.write(chunk)
            hasher.update(chunk)
    if size and written != size:
        target.unlink(missing_ok=True)
        raise UpdateError("bad_size", "Το μέγεθος του αρχείου δεν ταιριάζει με το release.")
    if hasher.hexdigest().lower() != str(manifest["sha256"]).lower():
        target.unlink(missing_ok=True)
        raise UpdateError("bad_digest", "Το sha256 δεν ταιριάζει με το υπογεγραμμένο manifest.")
    return target


# -------------------------------------------------------------------- stage
def _check_members(zf: zipfile.ZipFile) -> None:
    """Reject absolute paths, traversal, drive letters and symlinks; cap size."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise UpdateError("too_many_entries", f"Το zip έχει {len(infos)} entries.")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
            raise UpdateError("unsafe_path", f"Μη ασφαλής διαδρομή στο zip: {name}")
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise UpdateError("unsafe_path", f"Symlink μέσα στο zip: {name}")
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise UpdateError("too_large", "Το zip ξεπερνά το όριο αποσυμπίεσης.")


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
                    raise UpdateError("unsafe_path", f"Διαδρομή εκτός staging: {name}")
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
        raise UpdateError("bad_archive", "Το zip δεν περιέχει MUXIVRA.exe / MUXIVRA.cmd.")
    return root


# ------------------------------------------------------------------- apply
def _cmd_path(path: Path) -> str:
    """Escape a path for a batch file: % is the only character batch expands."""
    return str(path).replace("%", "%%")


def write_apply_script(
    bundle: Path,
    new_root: Path,
    pid: int,
    staging_root: Path,
    backup_name: str | None = None,
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
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "setlocal DisableDelayedExpansion",
        f'set "OLD={_cmd_path(bundle)}"',
        f'set "NEW={_cmd_path(new_root)}"',
        f'set "BACKUP={_cmd_path(backup)}"',
        f'set "HEALTHLOG={_cmd_path(staging_root)}\\health.log"',
        f"set PID={int(pid)}",
        "echo Waiting for MUXIVRA (pid %PID%) to close...",
        ":wait",
        'tasklist /FI "PID eq %PID%" 2>nul | find /I "%PID%" >nul',
        "if not errorlevel 1 (",
        "  timeout /t 2 /nobreak >nul",
        "  goto wait",
        ")",
        'if not exist "%NEW%\\MUXIVRA.exe" (',
        "  echo The staged update is missing or incomplete - nothing was changed.",
        "  pause",
        "  exit /b 1",
        ")",
        'if exist "%BACKUP%" rmdir /S /Q "%BACKUP%"',
        'move "%OLD%" "%BACKUP%" >nul || (echo Cannot move the current install - close explorer windows on it. & pause & exit /b 1)',
        'robocopy "%NEW%" "%OLD%" /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS >nul',
        "if errorlevel 8 goto rollback",
        '"%OLD%\\runtime\\python\\python.exe" -m sshdeck healthcheck >"%HEALTHLOG%" 2>&1',
        "if errorlevel 1 goto rollback",
        'start "" "%OLD%\\MUXIVRA.exe"',
        'echo Update applied. Previous version kept in "%BACKUP%".',
        "goto done",
        ":rollback",
        "echo Update failed - restoring the previous version.",
        'rmdir /S /Q "%OLD%"',
        'move "%BACKUP%" "%OLD%" >nul',
        'start "" "%OLD%\\MUXIVRA.exe"',
        ":done",
        "timeout /t 3 >nul",
        'del "%~f0"',
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
