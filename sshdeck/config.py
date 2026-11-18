"""Runtime paths and settings.

Persistent data (database, vault key, known_hosts) lives OUTSIDE the program
folder so an update can replace the binaries without touching user data.
"""
import os
import shutil
import sys
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


def _migrate_legacy_data(target: Path) -> None:
    """Move a pre-0.4 `<bundle>/data` folder to the per-user location, once."""
    legacy = _legacy_data_dir()
    if target.exists() or not legacy.exists():
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
KNOWN_HOSTS_PATH = DATA_DIR / "known_hosts"
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
