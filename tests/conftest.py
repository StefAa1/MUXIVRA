import os
import sys
import tempfile
from pathlib import Path

# Isolate the app's data dir before any sshdeck module is imported.
_TMP = tempfile.mkdtemp(prefix="sshdeck-tests-")
os.environ["SSHDECK_DATA"] = _TMP

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sshdeck import config, db  # noqa: E402
from sshdeck.main import create_app  # noqa: E402

HEADERS = {"x-requested-with": "sshdeck"}
PASSWORD = "correct-horse-battery"


def _reset() -> None:
    extra = Path(str(config.VAULT_KEY_PATH) + ".dpapi")
    for path in (config.DB_PATH, config.VAULT_KEY_PATH, extra, config.KNOWN_HOSTS_PATH):
        for candidate in (Path(str(path)), Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    db.init()


@pytest.fixture()
def client():
    _reset()
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    res = client.post("/api/setup", json={"username": "admin", "password": PASSWORD})
    assert res.status_code == 200, res.text
    return client
