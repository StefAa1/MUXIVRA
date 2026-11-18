"""GitHub release updates for the portable build.

Read-only check + verified download. The swap itself is done by a generated
apply-update.cmd, because a running pythonw.exe cannot overwrite its own files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/{repo}/releases/latest"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ASSET_BYTES = 400 * 1024 * 1024


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
    """'v0.2.1' / '0.2.1-beta' -> (0, 2, 1); non numeric parts stop the parse."""
    numbers = re.findall(r"\d+", (text or "").lstrip("vV"))
    return tuple(int(n) for n in numbers[:4]) if numbers else (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


def _open(url: str, timeout: float):
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/vnd.github+json",
            "user-agent": "sshdeck-updater",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


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
    assets = []
    for asset in data.get("assets") or []:
        assets.append(
            {
                "name": asset.get("name") or "",
                "url": asset.get("browser_download_url") or "",
                "size": int(asset.get("size") or 0),
                "digest": asset.get("digest") or "",
            }
        )
    archive = next((a for a in assets if a["name"].lower().endswith(".zip")), None)
    return {
        "repo": repo,
        "current": current,
        "latest": tag.lstrip("vV"),
        "available": is_newer(tag, current),
        "asset": archive,
        "assets": assets,
        "notes": str(data.get("body") or "")[:2000],
        "published_at": data.get("published_at"),
    }


def download(asset: dict, dest_dir: Path, opener=None, timeout: float = 60.0) -> Path:
    if not asset or not asset.get("url"):
        raise UpdateError("no_asset", "Το release δεν έχει .zip asset.")
    size = int(asset.get("size") or 0)
    if size > MAX_ASSET_BYTES:
        raise UpdateError("too_large", f"Το asset είναι {size} bytes — πάνω από το όριο.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (asset.get("name") or "update.zip")
    opener = opener or _open
    digest = (asset.get("digest") or "").strip()
    hasher = hashlib.sha256()
    with opener(asset["url"], timeout) as response, open(target, "wb") as fh:
        while chunk := response.read(1024 * 256):
            fh.write(chunk)
            hasher.update(chunk)
    if size and target.stat().st_size != size:
        target.unlink(missing_ok=True)
        raise UpdateError("bad_size", "Το μέγεθος του αρχείου δεν ταιριάζει με το release.")
    if digest.startswith("sha256:"):
        if hasher.hexdigest().lower() != digest.split(":", 1)[1].strip().lower():
            target.unlink(missing_ok=True)
            raise UpdateError("bad_digest", "Το sha256 του αρχείου δεν ταιριάζει.")
    return target


def stage(archive: Path, work_dir: Path) -> Path:
    """Extract a downloaded release next to the bundle, sanity-checked."""
    staging = work_dir / f"staged-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(staging)
    roots = [p for p in staging.iterdir() if p.is_dir()]
    root = roots[0] if len(roots) == 1 and (roots[0] / "MUXIVRA.exe").exists() else staging
    if not (root / "MUXIVRA.exe").exists() and not (root / "MUXIVRA.cmd").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("bad_archive", "Το zip δεν περιέχει MUXIVRA.exe / MUXIVRA.cmd.")
    return root


def write_apply_script(bundle: Path, new_root: Path, script_dir: Path | None = None) -> Path:
    """A .cmd the user runs after closing the app: backup, swap, restart."""
    bundle = bundle.resolve()
    new_root = new_root.resolve()
    script = Path(script_dir).resolve() / "apply-update.cmd" if script_dir else bundle / "apply-update.cmd"
    script.parent.mkdir(parents=True, exist_ok=True)
    backup = bundle.parent / "backup-before-update"
    lines = [
        "@echo off",
        "setlocal",
        f'set "OLD={bundle}"',
        f'set "NEW={new_root}"',
        f'set "BACKUP={backup}"',
        'echo Closing MUXIVRA...',
        "taskkill /IM pythonw.exe /F >nul 2>&1",
        "timeout /t 2 /nobreak >nul",
        f'if exist "%BACKUP%" rmdir /S /Q "%BACKUP%"',
        f'move "%OLD%" "%BACKUP%" >nul',
        'xcopy "%NEW%" "%OLD%" /E /I /H /Y >nul',
        'if errorlevel 1 (',
        '  echo Update failed - restoring the previous version.',
        '  rmdir /S /Q "%OLD%"',
        '  move "%BACKUP%" "%OLD%" >nul',
        '  pause',
        '  exit /b 1',
        ')',
        'start "" "%OLD%\\MUXIVRA.exe"',
        'echo Update applied. You can delete the backup folder later.',
        "timeout /t 3 >nul",
        'del "%~f0"',
        "",
    ]
    script.write_bytes("\r\n".join(lines).encode("ascii"))
    return script


def cleanup_downloads(work_dir: Path, keep_days: int = 7) -> None:
    if not work_dir.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for path in work_dir.glob("*"):
        try:
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
        except OSError:
            continue
