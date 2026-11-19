"""Runtime paths and settings.

Persistent data (database, vault key, known_hosts) lives OUTSIDE the program
folder so an update can replace the binaries without touching user data.
"""
import os
import json
import shutil
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    """Per-user data dir: %LOCALAPPDATA%\\MUXIVRA on Windows, XDG elsewhere."""
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "MUXIVRA" / "data"
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "muxivra" / "data"


def _legacy_data_dir() -> Path:
    return BASE_DIR / "data"


def _looks_like_data(path: Path) -> bool:
    return any((path / name).exists() for name in ("sshdeck.db", "vault.key", "vault.key.dpapi"))


def _migration_marker(target: Path) -> Path:
    return target / "migration.json"


def _completed_migration(target: Path, source: Path) -> bool:
    """A finished migration (any mode) must not run again for the same source."""
    marker = _migration_marker(target)
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("status") == "ok" and data.get("source") == str(source)


def _migrate_legacy_data(target: Path) -> None:
    """Adopt a pre-0.4 `<bundle>/data` folder without ever overwriting data.

    - target missing/empty  -> move the legacy folder into place
    - target already has a database or vault -> keep it, copy the legacy data
      beside it (legacy-import-<stamp>) and leave the legacy folder untouched
    """
    legacy = _legacy_data_dir()
    if not legacy.exists():
        return
    if _completed_migration(target, legacy):
        return                      # already adopted/copied once: nothing to do
    if _looks_like_data(target):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = target / f"legacy-import-{stamp}"
        destination.mkdir(parents=True, exist_ok=True)
        for item in legacy.iterdir():
            if item.name in {"sshdeck.log", "updates"}:
                continue
            if item.is_dir():
                shutil.copytree(item, destination / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination / item.name)
        _migration_marker(target).write_text(
            json.dumps(
                {"source": str(legacy), "mode": "kept-existing", "status": "ok",
                 "copied_to": destination.name, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            ),
            encoding="utf-8",
        )
        return
    if target.exists() and not _looks_like_data(target):
        # target directory exists but holds no data (ensure_dirs() made it):
        # adopt the legacy files into it, never overwriting something already there
        for item in legacy.iterdir():
            destination = target / item.name
            if destination.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)
        _migration_marker(target).write_text(
            json.dumps({"source": str(legacy), "mode": "adopted", "status": "ok",
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}),
            encoding="utf-8",
        )
        return
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(legacy), str(target))
        return
    except (OSError, shutil.Error):
        pass
    # a cross-device move fails; copy file by file instead of losing data
    target.mkdir(parents=True, exist_ok=True)
    for item in legacy.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


DATA_DIR = Path(os.environ.get("SSHDECK_DATA") or _default_data_dir())

WEB_DIR = Path(os.environ.get("SSHDECK_WEB", BASE_DIR / "web"))

DB_PATH = DATA_DIR / "sshdeck.db"
VAULT_KEY_PATH = DATA_DIR / "vault.key"
KNOWN_HOSTS_PATH = DATA_DIR / "known_hosts"          # legacy, customer 1
KNOWN_HOSTS_DIR = None                               # resolved lazily (DATA_DIR may change)


def known_hosts_file(customer_id: int | None = None):
    """Each customer gets its own trust store: same IP, different legitimate keys."""
    from pathlib import Path

    base = Path(DATA_DIR) / "known_hosts.d"
    if customer_id is None:
        return Path(KNOWN_HOSTS_PATH)
    return base / f"customer-{int(customer_id)}"


def ensure_known_hosts(customer_id: int | None = None) -> str:
    import os

    ensure_dirs()
    path = known_hosts_file(customer_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    else:
        os.chmod(path, 0o600)
    return str(path)


def migrate_known_hosts(default_customer_id: int) -> bool:
    """Adopt the pre-0.4.6 shared trust file into the unclassified customer."""
    from pathlib import Path
    import shutil

    legacy = Path(KNOWN_HOSTS_PATH)
    if not legacy.exists() or not legacy.stat().st_size:
        return False
    target = known_hosts_file(default_customer_id)
    if target.exists() and target.stat().st_size:
        return False                     # already migrated: never overwrite trust
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    target.chmod(0o600)
    return True
LOG_PATH = DATA_DIR / "sshdeck.log"

def _default_staging_dir(data_dir: Path) -> Path:
    """Staging for updates: inside the data dir, unless the data dir itself sits
    inside the program folder (a pre-0.4 layout) — then step outside, because the
    program folder is exactly what an update replaces."""
    bundle = BASE_DIR.parent if BASE_DIR.name == "app" else None
    portable = bool(bundle and (bundle / "MUXIVRA.exe").exists())
    if not portable:
        return data_dir / "updates"          # source install: updates never replace this tree
    resolved = data_dir.resolve()
    if bundle.resolve() in resolved.parents:
        return bundle.parent / f"{bundle.name}-updates"   # legacy data inside the bundle
    return data_dir.parent / "updates"


# update staging must never live inside the folder that gets replaced
STAGING_DIR = Path(os.environ.get("SSHDECK_STAGING") or _default_staging_dir(DATA_DIR))
UPDATE_PUBKEY_PATH = DATA_DIR / "update-pubkey.txt"
# Pinned Ed25519 public key for release signatures (base64, raw 32 bytes).
# A release that does not verify against this key is refused; replacing it is a
# code change, which is the point.
DEFAULT_UPDATE_PUBKEY = "JD1KLsqJo/lc3WKSxl9Se9y0l3eLblFa9/5fhfUbLMg="

BIND_HOST = os.environ.get("SSHDECK_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("SSHDECK_PORT", "8722"))

COOKIE_NAME = "sshdeck_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "sshdeck"

CONNECT_TIMEOUT = 12
KEEPALIVE_SECONDS = 30
DEFAULT_TERM = "xterm-256color"


def migrate_legacy_data() -> None:
    """Run ONCE at app start (never on import): adopt a pre-0.4 <bundle>/data."""
    if os.environ.get("SSHDECK_DATA"):
        return  # explicit data dir: the operator chose it, do not move anything
    _migrate_legacy_data(DATA_DIR)


def ensure_dirs() -> None:
    for directory in (DATA_DIR, STAGING_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
