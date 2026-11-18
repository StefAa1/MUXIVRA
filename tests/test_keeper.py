"""Keeper Secrets Manager import: mapping, merge and CLI (no live Keeper needed)."""
import json
import os
import sys
import types

import pytest

from conftest import HEADERS, PASSWORD
from sshdeck import db, keeper, security

RAW_SERVER = {
    "uid": "U1",
    "title": "prod-db",
    "type": "server",
    "fields": {"host": "10.0.0.5", "port": "2222", "login": "root", "password": "pw-prod"},
    "notes": "primary database",
}
RAW_LOGIN = {
    "uid": "U2",
    "title": "edge-router",
    "type": "login",
    "fields": [{"type": "login", "value": ["ubuntu"]}, {"type": "password", "value": ["s3cret"]}],
    "custom_fields": [{"type": "host", "value": ["edge.example.net"]}],
}
RAW_KEY = {
    "uid": "U3",
    "title": "firewall",
    "type": "sshKey",
    "fields": {
        "host": ["10.0.0.9"],
        "login": ["admin"],
        "key": ["-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"],
        "passphrase": ["keypass"],
    },
}
RAW_EMPTY = {"uid": "U4", "title": "nothing", "type": "login", "fields": {"login": "x"}}


@pytest.fixture()
def fresh():
    from conftest import _reset

    _reset()
    yield


def test_normalise_dict_fields():
    rec = keeper.normalise_record(RAW_SERVER)
    assert (rec.host, rec.port, rec.login, rec.password) == ("10.0.0.5", 2222, "root", "pw-prod")
    assert rec.kind == "server" and rec.usable
    assert rec.notes == "primary database"


def test_normalise_list_fields_and_custom_fields():
    rec = keeper.normalise_record(RAW_LOGIN)
    assert rec.host == "edge.example.net"
    assert rec.login == "ubuntu"
    assert rec.password == "s3cret"


def test_normalise_ssh_key_record():
    rec = keeper.normalise_record(RAW_KEY)
    assert rec.kind == "ssh_key"
    assert rec.private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert rec.passphrase == "keypass"
    assert rec.usable


def test_record_without_secret_is_not_usable():
    assert not keeper.normalise_record(RAW_EMPTY).usable


def test_import_records_creates_encrypted_hosts(fresh):
    with db.db() as conn:
        summary = keeper.import_records(conn, [RAW_SERVER, RAW_LOGIN, RAW_KEY, RAW_EMPTY], group="Keeper")
        assert summary["seen"] == 4
        assert summary["hosts_created"] == 3
        assert summary["credentials"] == 3
        assert summary["ignored"] == 1
        rows = conn.execute(
            "SELECT h.label, h.port, h.username, c.kind, c.secret_enc FROM hosts h "
            "JOIN credentials c ON c.id = h.credential_id ORDER BY h.label"
        ).fetchall()
        assert [r["label"] for r in rows] == ["edge-router", "firewall", "prod-db"]
        kinds = {r["label"]: r["kind"] for r in rows}
        assert kinds["firewall"] == "key" and kinds["prod-db"] == "password"
        sealed = {r["label"]: r["secret_enc"] for r in rows}
        assert security.open_sealed(sealed["prod-db"]) == "pw-prod"
        raw_db = open(db.config.DB_PATH, "rb").read()
    assert b"pw-prod" not in raw_db


def test_import_is_idempotent(fresh):
    with db.db() as conn:
        first = keeper.import_records(conn, [RAW_SERVER])
        second = keeper.import_records(conn, [RAW_SERVER])
    assert first["hosts_created"] == 1
    assert second["hosts_created"] == 0 and second["hosts_skipped"] == 1


def test_dry_run_writes_nothing(fresh):
    with db.db() as conn:
        summary = keeper.import_records(conn, [RAW_SERVER, RAW_KEY], dry_run=True)
        hosts = conn.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"]
        creds = conn.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"]
    assert summary["seen"] == 2
    assert summary["hosts_created"] == 0
    assert hosts == 0 and creds == 0
    assert {d["credential"] for d in summary["details"]} == {"password", "key"}


def test_fetch_records_uses_sdk_and_locks_down_config(monkeypatch, tmp_path):
    class FakeRecord:
        def __init__(self, payload):
            self.dict = payload

    class FakeSecretsManager:
        seen_token = None

        def __init__(self, token, config):
            FakeSecretsManager.seen_token = token
            self.config = config

        def get_secrets(self, uids=None):
            return [FakeRecord({"uid": "1", "title": "from-keeper", "fields": {"host": "h"}})]

    class FakeStorage:
        def __init__(self, path):
            self.path = path
            open(path, "w", encoding="utf-8").write("{}")

    pkg = types.ModuleType("keeper_secrets_manager_core")
    pkg.SecretsManager = FakeSecretsManager
    storage = types.ModuleType("keeper_secrets_manager_core.storage")
    storage.FileKeyValueStorage = FakeStorage
    pkg.storage = storage
    monkeypatch.setitem(sys.modules, "keeper_secrets_manager_core", pkg)
    monkeypatch.setitem(sys.modules, "keeper_secrets_manager_core.storage", storage)

    cfg = tmp_path / "ksm-config.json"
    records = keeper.fetch_records("US:one-time-token", str(cfg))
    assert records and records[0]["title"] == "from-keeper"
    assert FakeSecretsManager.seen_token == "US:one-time-token"
    assert oct(os.stat(cfg).st_mode)[-3:] == "600"


def test_fetch_records_without_token_fails_closed(tmp_path):
    with pytest.raises(keeper.KeeperError) as excinfo:
        keeper.fetch_records("", str(tmp_path / "cfg.json"))
    assert excinfo.value.code == "token_missing"


def test_cli_import_from_exported_json(fresh, tmp_path, capsys):
    from sshdeck.cli import main as cli_main

    export = tmp_path / "keeper-export.json"
    export.write_text(json.dumps({"records": [RAW_SERVER, RAW_KEY]}), encoding="utf-8")

    assert cli_main(["keeper-import", "--from-json", str(export), "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["seen"] == 2 and dry["hosts_created"] == 0

    assert cli_main(["keeper-import", "--from-json", str(export), "--group", "Keeper"]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["hosts_created"] == 2 and done["credentials"] == 2

    with db.db() as conn:
        labels = [r["label"] for r in conn.execute("SELECT label FROM hosts ORDER BY label")]
        groups = [r["group_name"] for r in conn.execute("SELECT group_name FROM hosts")]
    assert labels == ["firewall", "prod-db"]
    assert set(groups) == {"Keeper"}
