"""Runtime paths and settings. Everything lives under one data dir."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SSHDECK_DATA", BASE_DIR / "data"))
WEB_DIR = Path(os.environ.get("SSHDECK_WEB", BASE_DIR / "web"))

DB_PATH = DATA_DIR / "sshdeck.db"
VAULT_KEY_PATH = DATA_DIR / "vault.key"
KNOWN_HOSTS_PATH = DATA_DIR / "known_hosts"
LOG_PATH = DATA_DIR / "sshdeck.log"

BIND_HOST = os.environ.get("SSHDECK_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("SSHDECK_PORT", "8722"))

COOKIE_NAME = "sshdeck_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "sshdeck"

CONNECT_TIMEOUT = 12
KEEPALIVE_SECONDS = 30
DEFAULT_TERM = "xterm-256color"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
