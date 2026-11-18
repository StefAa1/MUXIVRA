"""Importing the package must never touch the filesystem or move user data."""
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
