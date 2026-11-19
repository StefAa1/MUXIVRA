"""Healthcheck must never mint a key over existing ciphertext (fail closed)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_healthcheck(data_dir: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "SSHDECK_DATA"}
    env["SSHDECK_DATA"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "sshdeck", "healthcheck"],
                          capture_output=True, text=True, env=env, timeout=120)


def seed(data_dir: Path, *, create_key: bool = True) -> None:
    """Create the data dir in a subprocess so config.DATA_DIR matches it."""
    env = {k: v for k, v in os.environ.items() if k != "SSHDECK_DATA"}
    env["SSHDECK_DATA"] = str(data_dir)
    env["PYTHONPATH"] = str(ROOT)
    code = (
        "from sshdeck import db, security;"
        "db.init();"
        + ("blob = security.seal('seeded-secret');" if create_key else "blob = 'no-key';")
        + "conn = db.connect();"
        "conn.execute(\"INSERT INTO credentials (name, kind, username, secret_enc, created_at, updated_at)"
        " VALUES ('c','password','root',?,0,0)\", (blob,));"
        "conn.commit(); conn.close()"
    )
    subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, check=True, timeout=120)


def test_fresh_install_passes():
    data = Path(tempfile.mkdtemp(prefix="hc-fresh-"))
    result = run_healthcheck(data)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthcheck ok" in result.stdout


def test_missing_key_with_credentials_fails_without_creating_one():
    data = Path(tempfile.mkdtemp(prefix="hc-miss-"))
    seed(data, create_key=False)
    before = sorted(p.name for p in data.iterdir())
    result = run_healthcheck(data)
    after = sorted(p.name for p in data.iterdir())
    assert result.returncode == 5, result.stdout + result.stderr
    assert "vault key missing" in result.stdout
    assert before == after, "healthcheck must not create or replace a key"
    assert not (data / "vault.key").exists() and not (data / "vault.key.dpapi").exists()


def test_empty_key_file_fails():
    data = Path(tempfile.mkdtemp(prefix="hc-empty-"))
    seed(data, create_key=False)
    (data / "vault.key").write_bytes(b"")
    result = run_healthcheck(data)
    assert result.returncode != 0


def test_corrupt_key_fails():
    data = Path(tempfile.mkdtemp(prefix="hc-bad-"))
    seed(data, create_key=True)
    (data / "vault.key").write_bytes(b"garbage")
    result = run_healthcheck(data)
    assert result.returncode != 0


def test_valid_key_passes():
    data = Path(tempfile.mkdtemp(prefix="hc-ok-"))
    seed(data, create_key=True)
    result = run_healthcheck(data)
    assert result.returncode == 0, result.stdout + result.stderr
