"""Importing the package must never touch the filesystem or move user data."""
import json
import os
import subprocess
import sys
from pathlib import Path

from sshdeck import config

ROOT = Path(__file__).resolve().parent.parent


def test_import_with_explicit_data_dir_moves_nothing(tmp_path):
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    target = tmp_path / "userdata"

    env = {k: v for k, v in os.environ.items() if k != "SSHDECK_DATA"}
    env["SSHDECK_DATA"] = str(target)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import sshdeck.config as c; print(c.DATA_DIR)"],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert str(target) == result.stdout.strip()
    assert (legacy / "sshdeck.db").read_bytes() == b"legacy-db", "import moved user data"
    assert not target.exists(), "import created directories"


def test_legacy_migration_only_runs_when_asked(tmp_path, monkeypatch):
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    target = tmp_path / "userdata"

    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.setenv("SSHDECK_DATA", str(target))
    config.migrate_legacy_data()          # explicit data dir -> no-op
    assert (legacy / "sshdeck.db").exists()
    assert not target.exists()

    monkeypatch.delenv("SSHDECK_DATA")
    config.migrate_legacy_data()          # startup path adopts the legacy folder
    assert (target / "sshdeck.db").read_bytes() == b"legacy-db"
    assert not legacy.exists()


def test_migration_never_overwrites_existing_database(tmp_path, monkeypatch):
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    (legacy / "vault.key").write_bytes(b"legacy-key")

    target = tmp_path / "userdata"
    target.mkdir()
    (target / "sshdeck.db").write_bytes(b"current-db")
    (target / "vault.key").write_bytes(b"current-key")

    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.delenv("SSHDECK_DATA", raising=False)
    config.migrate_legacy_data()

    assert (target / "sshdeck.db").read_bytes() == b"current-db"
    assert (target / "vault.key").read_bytes() == b"current-key"
    assert (legacy / "sshdeck.db").exists(), "legacy folder must stay untouched"
    imported = list(target.glob("legacy-import-*"))
    assert imported and (imported[0] / "sshdeck.db").read_bytes() == b"legacy-db"
    marker = json.loads((target / "migration.json").read_text())
    assert marker["mode"] == "kept-existing" and marker["status"] == "ok"


def test_migration_adopts_into_an_empty_created_target(tmp_path, monkeypatch):
    """Regression: ensure_dirs() used to create the target first, so migration gave up."""
    import json

    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    (legacy / "vault.key.dpapi").write_bytes(b"legacy-key")

    target = tmp_path / "userdata"
    target.mkdir()          # empty dir, exactly what ensure_dirs() creates
    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.delenv("SSHDECK_DATA", raising=False)

    config.migrate_legacy_data()

    assert (target / "sshdeck.db").read_bytes() == b"legacy-db"
    assert (target / "vault.key.dpapi").read_bytes() == b"legacy-key"
    marker = json.loads((target / "migration.json").read_text())
    assert marker["mode"] == "adopted"


def test_migration_does_not_copy_twice(tmp_path, monkeypatch):
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    target = tmp_path / "userdata"
    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.delenv("SSHDECK_DATA", raising=False)

    config.migrate_legacy_data()
    config.migrate_legacy_data()   # second start: data is already there
    assert (target / "sshdeck.db").read_bytes() == b"legacy-db"
    assert not list(target.glob("legacy-import-*"))


def test_marker_stops_repeat_migration(tmp_path, monkeypatch):
    """Regression: the marker was written but never read, so every start copied again."""
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    target = tmp_path / "userdata"
    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.delenv("SSHDECK_DATA", raising=False)

    config.migrate_legacy_data()          # adopt
    config.migrate_legacy_data()          # second start
    config.migrate_legacy_data()          # desktop + serve in the same boot
    assert not list(target.glob("legacy-import-*")), "repeat migration created a backup"
    assert (target / "sshdeck.db").read_bytes() == b"legacy-db"


def test_marker_also_stops_repeat_for_existing_destination(tmp_path, monkeypatch):
    legacy = tmp_path / "program" / "data"
    legacy.mkdir(parents=True)
    (legacy / "sshdeck.db").write_bytes(b"legacy-db")
    target = tmp_path / "userdata"
    target.mkdir()
    (target / "sshdeck.db").write_bytes(b"current-db")
    monkeypatch.setattr(config, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setattr(config, "DATA_DIR", target)
    monkeypatch.delenv("SSHDECK_DATA", raising=False)

    config.migrate_legacy_data()
    config.migrate_legacy_data()
    assert len(list(target.glob("legacy-import-*"))) == 1
    assert (target / "sshdeck.db").read_bytes() == b"current-db"
