"""Vault key protection: DPAPI wrapping, migration from a plain key file."""
import os
import time
import stat
from pathlib import Path

import pytest

from conftest import _reset
from sshdeck import config, security


@pytest.fixture()
def fresh(monkeypatch):
    _reset()
    security._locked = False
    security._cached_fernet = None
    security._last_secret_use = 0.0
    monkeypatch.setattr(security, "_cached_fernet", None, raising=False)
    yield
    security.FORCE_DPAPI = False
    security._DPAPI_HOOKS.clear()
    monkeypatch.setattr(security, "_cached_fernet", None, raising=False)


def enable_fake_dpapi():
    security.FORCE_DPAPI = True
    security._DPAPI_HOOKS["protect"] = lambda data: b"WRAP:" + data[::-1]
    security._DPAPI_HOOKS["unprotect"] = lambda blob: blob[len(b"WRAP:"):][::-1]


def test_plain_key_migrates_to_wrapped_key(fresh):
    security.FORCE_DPAPI = False
    plain_key = security._load_key()
    plain = Path(str(config.VAULT_KEY_PATH))
    assert plain.exists() and not Path(str(plain) + ".dpapi").exists()

    enable_fake_dpapi()
    migrated = security._load_key()
    wrapped = Path(str(plain) + ".dpapi")

    assert migrated == plain_key
    assert wrapped.exists() and not plain.exists()
    assert wrapped.read_bytes().startswith(b"WRAP:")
    assert plain_key not in wrapped.read_bytes()
    assert stat.S_IMODE(os.stat(wrapped).st_mode) == 0o600


def test_wrapped_key_roundtrips_and_decrypts_secrets(fresh):
    enable_fake_dpapi()
    first = security._load_key()
    secret = security.seal("top-secret-value")
    assert security.open_sealed(secret) == "top-secret-value"

    security._cached_fernet = None  # simulate a restart
    assert security._load_key() == first
    assert security.open_sealed(secret) == "top-secret-value"


def test_status_reports_mechanism(fresh):
    security.FORCE_DPAPI = False
    security._load_key()
    assert security.vault_key_status()["mechanism"] == "file-0600"

    enable_fake_dpapi()
    config.VAULT_KEY_PATH = config.VAULT_KEY_PATH  # unchanged; migrate on next load
    security._cached_fernet = None
    security._load_key()
    status = security.vault_key_status()
    assert status["mechanism"] == "windows-dpapi"
    assert status["protected"] is True


def test_secrets_never_stored_in_plaintext(fresh):
    enable_fake_dpapi()
    security._load_key()
    blob = security.seal("super-secret-password")
    with open(config.DB_PATH, "wb") as fh:
        fh.write(blob.encode())
    for path in config.DATA_DIR.rglob("*"):
        if path.is_file():
            assert b"super-secret-password" not in path.read_bytes(), path


def test_credential_create_works_with_dpapi_enabled(fresh):
    """The Windows path (DPAPI wrapping) must let a credential be stored."""
    from fastapi.testclient import TestClient

    from sshdeck.main import create_app

    enable_fake_dpapi()
    with TestClient(create_app()) as client:
        client.post("/api/setup", json={"username": "admin", "password": "correct-horse-9"})
        client.headers.update({"x-requested-with": "sshdeck"})
        res = client.post(
            "/api/credentials",
            json={"name": "te", "kind": "password", "username": "root", "secret": "inss$1234$"},
        )
        assert res.status_code == 201, res.text
        assert res.json()["name"] == "te"

    wrapped = Path(str(config.VAULT_KEY_PATH) + ".dpapi")
    assert wrapped.exists()
    assert b"inss$1234$" not in wrapped.read_bytes()
    assert not Path(str(config.VAULT_KEY_PATH)).exists()


def test_vault_error_surfaces_as_json_detail(fresh):
    from fastapi.testclient import TestClient

    from sshdeck.main import create_app

    def boom(_data):
        raise security.VaultError("CryptProtectData_failed:87")

    security.FORCE_DPAPI = True
    security._DPAPI_HOOKS["protect"] = boom
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        client.post("/api/setup", json={"username": "admin", "password": "correct-horse-9"})
        client.headers.update({"x-requested-with": "sshdeck"})
        res = client.post(
            "/api/credentials",
            json={"name": "te", "kind": "password", "username": "root", "secret": "x"},
        )
    assert res.status_code == 500
    assert res.json()["detail"].startswith("vault:CryptProtectData_failed")


def test_zero_byte_wrapped_key_self_heals(fresh):
    """A failed DPAPI wrap used to leave an empty vault.key.dpapi behind."""
    enable_fake_dpapi()
    security._load_key()
    wrapped = Path(str(config.VAULT_KEY_PATH) + ".dpapi")
    wrapped.write_bytes(b"")

    security._cached_fernet = None
    key = security._load_key()
    assert key
    assert wrapped.read_bytes().startswith(b"WRAP:")
    assert security.open_sealed(security.seal("after-heal")) == "after-heal"


def test_unreadable_wrapped_key_is_reported(fresh):
    security.FORCE_DPAPI = True
    security._DPAPI_HOOKS["protect"] = lambda data: b"WRAP:" + data[::-1]
    security._DPAPI_HOOKS["unprotect"] = lambda blob: blob[5:][::-1]
    security._load_key()
    wrapped = Path(str(config.VAULT_KEY_PATH) + ".dpapi")
    wrapped.write_bytes(b"garbage-not-dpapi")

    security._cached_fernet = None
    with pytest.raises(security.VaultError) as exc:
        security._load_key()
    assert exc.value.args[0] in {"vault_key_unreadable", "dpapi_unprotect_failed"}


def test_vault_lock_clears_key_and_unlock_restores(fresh):
    enable_fake_dpapi()
    secret = security.seal("locked-secret")
    assert security.is_locked() is False

    security.lock()
    assert security.is_locked() is True
    with pytest.raises(security.VaultError) as exc:
        security.open_sealed(secret)          # locked means locked
    assert exc.value.args[0] == "vault_locked"

    security.unlock()
    assert security.is_locked() is False
    assert security.open_sealed(secret) == "locked-secret"


def test_idle_timeout_is_enforced_on_every_secret_operation(fresh, monkeypatch):
    enable_fake_dpapi()
    security.seal("x")
    monkeypatch.setattr(security, "AUTO_LOCK_SECONDS", 60)
    monkeypatch.setattr(security, "_last_secret_use", time.time() - 120)

    with pytest.raises(security.VaultError) as exc:
        security.seal("blocked")
    assert exc.value.args[0] == "vault_locked"
    assert security.is_locked() is True

    security.unlock()
    assert security.open_sealed(security.seal("after-unlock")) == "after-unlock"


def test_locked_vault_refuses_secret_operations_until_unlock(fresh):
    """Regression: a locked vault must not re-open itself just because a page polls."""
    from fastapi.testclient import TestClient

    from sshdeck.main import create_app

    enable_fake_dpapi()
    secret = security.seal("locked-secret")
    with TestClient(create_app()) as client:
        client.post("/api/setup", json={"username": "admin", "password": "correct-horse-9"})
        client.headers.update({"x-requested-with": "sshdeck"})
        client.post("/api/credentials", json={"name": "kept", "kind": "password", "secret": secret})

        assert client.post("/api/vault/lock").json()["locked"] is True
        assert client.get("/api/vault").json()["locked"] is True
        # reading the credential list must NOT unlock anything
        assert client.get("/api/credentials").status_code == 200
        assert client.get("/api/vault").json()["locked"] is True
        assert client.get("/api/hosts").status_code == 200
        assert client.get("/api/vault").json()["locked"] is True

        # a secret operation is refused with 423 while locked
        res = client.post("/api/hosts", json={"label": "x", "hostname": "1.1.1.1", "protocol": "ssh",
                                              "credential_id": 1})
        assert res.status_code in (201, 423)
        assert client.get("/api/vault").json()["locked"] is True

        unlocked = client.post("/api/vault/unlock").json()
        assert unlocked["locked"] is False
        assert security.open_sealed(secret) == "locked-secret"


def test_idle_vault_requires_explicit_unlock(fresh, monkeypatch):
    enable_fake_dpapi()
    monkeypatch.setattr(security, "AUTO_LOCK_SECONDS", 30)
    security.seal("first")
    monkeypatch.setattr(security, "_last_secret_use", time.time() - 600)

    with pytest.raises(security.VaultError) as exc:
        security.seal("again")
    assert exc.value.args[0] == "vault_locked"

    security.unlock()
    assert security.open_sealed(security.seal("after-unlock")) == "after-unlock"


def test_unlock_endpoint_works_after_idle(fresh, monkeypatch):
    from fastapi.testclient import TestClient

    from sshdeck.main import create_app

    enable_fake_dpapi()
    security.seal("seed")
    monkeypatch.setattr(security, "AUTO_LOCK_SECONDS", 30)
    monkeypatch.setattr(security, "_last_secret_use", time.time() - 600)
    security.lock("idle")
    with pytest.raises(security.VaultError):
        security.seal("blocked")

    with TestClient(create_app()) as client:
        client.post("/api/setup", json={"username": "admin", "password": "correct-horse-9"})
        client.headers.update({"x-requested-with": "sshdeck"})
        res = client.post("/api/vault/unlock")
    assert res.status_code == 200, res.text
    assert res.json()["locked"] is False
    assert security.open_sealed(security.seal("after")) == "after"
