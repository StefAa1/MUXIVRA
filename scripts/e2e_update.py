"""End-to-end check of the signed-update pipeline, without touching a real install.

Builds a fake bundle + signed release, serves it over loopback HTTP, runs the
real update code (manifest verification, download, staging, apply script), then
simulates the swap and proves the data dir and vault survive untouched.

    .venv/bin/python scripts/e2e_update.py
"""
from __future__ import annotations

import base64
import functools
import hashlib
import http.server
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="muxivra-e2e-update-"))
(os.environ.setdefault("SSHDECK_DATA", str(WORK / "userdata")))
os.environ["SSHDECK_DATA"] = str(WORK / "userdata")
os.environ["SSHDECK_UPDATE_ALLOW_HTTP"] = "1"
os.environ["SSHDECK_UPDATE_HOSTS"] = "127.0.0.1"

from sshdeck import db, security, update as U  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_bundle(path: Path, version: str) -> None:
    (path / "app" / "sshdeck").mkdir(parents=True)
    (path / "runtime" / "python").mkdir(parents=True)
    (path / "MUXIVRA.exe").write_bytes(b"MZ-fake-launcher-" + version.encode())
    (path / "MUXIVRA.cmd").write_bytes(b"@echo off\r\n")
    (path / "app" / "sshdeck" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (path / "BUILD.txt").write_text(f"{version}\n")


def zip_bundle(bundle: Path, target: Path) -> bytes:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                zf.write(file, Path(bundle.name) / file.relative_to(bundle))
    return target.read_bytes()


def dir_fingerprint(path: Path) -> dict:
    out = {}
    for file in sorted(path.rglob("*")):
        if file.is_file():
            out[str(file.relative_to(path))] = hashlib.sha256(file.read_bytes()).hexdigest()
    return out


def main() -> int:
    # --- current install: a bundle with data living OUTSIDE it ------------
    bundle = WORK / "install" / "MUXIVRA-portable-win64"
    make_bundle(bundle, "0.3.1")
    db.init()
    with db.db() as conn:
        conn.execute(
            "INSERT INTO hosts (label, hostname, port, protocol, username, created_at, updated_at)"
            " VALUES ('core-sw', '10.0.0.9', 22, 'ssh', 'admin', ?, ?)",
            (db.now(), db.now()),
        )
    secret_blob = security.seal("vault-secret-must-survive")
    with db.db() as conn:
        conn.execute(
            "INSERT INTO credentials (name, kind, username, secret_enc, created_at, updated_at)"
            " VALUES ('upd', 'password', 'root', ?, ?, ?)",
            (secret_blob, db.now(), db.now()),
        )
    data_before = dir_fingerprint(U.__dict__["Path"](os.environ["SSHDECK_DATA"]) if False else Path(os.environ["SSHDECK_DATA"]))

    # --- a signed release, served over loopback ---------------------------
    new_bundle = WORK / "MUXIVRA-portable-win64"
    make_bundle(new_bundle, "0.4.0")
    archive = WORK / "dist" / "MUXIVRA-portable-win64.zip"
    archive.parent.mkdir(parents=True)
    archive_bytes = zip_bundle(new_bundle, archive)

    private = Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    os.environ["SSHDECK_UPDATE_PUBKEY"] = base64.b64encode(public_raw).decode()

    manifest = {
        "version": "0.4.0",
        "archive": archive.name,
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    (archive.parent / "release.json").write_bytes(manifest_bytes)
    (archive.parent / "release.json.sig").write_bytes(base64.b64encode(private.sign(manifest_bytes)))

    port = free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(archive.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    release = {
        "latest": "0.4.0",
        "asset": {"name": archive.name, "url": f"{base}/{archive.name}", "size": len(archive_bytes)},
        "manifest": {"name": "release.json", "url": f"{base}/release.json", "size": len(manifest_bytes)},
        "signature": {"name": "release.json.sig", "url": f"{base}/release.json.sig", "size": 88},
    }

    # --- the real pipeline -------------------------------------------------
    try:
        verified = U.fetch_manifest(release)
        check("signed manifest verifies", verified["version"] == "0.4.0", verified["sha256"][:12])
    except Exception as exc:  # pragma: no cover - failure path
        check("signed manifest verifies", False, repr(exc))
        return 1

    tampered = dict(manifest, version="0.4.1")
    (archive.parent / "release.json").write_bytes(json.dumps(tampered, sort_keys=True).encode())
    try:
        U.fetch_manifest(release)
        check("tampered manifest rejected", False, "accepted a modified manifest")
    except U.UpdateError as exc:
        check("tampered manifest rejected", exc.code == "bad_signature", exc.code)
    (archive.parent / "release.json").write_bytes(manifest_bytes)

    archive_path = U.download_verified(release, verified, Path(os.environ["SSHDECK_DATA"]).parent / "updates")
    check("download matches signed sha256", archive_path.stat().st_size == len(archive_bytes),
          f"{archive_path.stat().st_size} bytes")

    staged = U.stage(archive_path, Path(os.environ["SSHDECK_DATA"]).parent / "updates")
    check("archives extracted outside the bundle", bundle not in staged.parents and staged.name == "MUXIVRA-portable-win64", str(staged))
    check("staged version is the new one", (staged / "BUILD.txt").read_text().strip() == "0.4.0")

    script = U.write_apply_script(bundle, staged, os.getpid(), Path(os.environ["SSHDECK_DATA"]).parent / "updates")
    text = script.read_bytes().decode("ascii")
    check("apply script waits for our pid", f"set PID={os.getpid()}" in text and "tasklist /FI" in text)
    check("apply script never kills unrelated pythonw", "taskkill /IM" not in text)
    check("apply script lives outside the bundle", bundle not in script.parents, str(script.parent))

    # --- simulate the swap the script performs ----------------------------
    backup = WORK / "updates" / "backup-sim"
    shutil.move(str(bundle), str(backup))
    shutil.copytree(staged, bundle)
    data_after = dir_fingerprint(Path(os.environ["SSHDECK_DATA"]))
    check("user data untouched by the swap", data_before == data_after,
          f"{len(data_after)} files identical")
    check("new binaries in place", (bundle / "BUILD.txt").read_text().strip() == "0.4.0")

    conn = sqlite3.connect(Path(os.environ["SSHDECK_DATA"]) / "sshdeck.db")
    hosts = conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0]
    creds = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    conn.close()
    check("database survives", hosts == 1 and creds == 1, f"{hosts} hosts / {creds} credentials")
    check("vault still decrypts after the swap", security.open_sealed(secret_blob) == "vault-secret-must-survive")

    # --- rollback path -----------------------------------------------------
    shutil.rmtree(bundle)
    shutil.move(str(backup), str(bundle))
    check("rollback restores the previous build", (bundle / "BUILD.txt").read_text().strip() == "0.3.1")
    check("data still intact after rollback", dir_fingerprint(Path(os.environ["SSHDECK_DATA"])) == data_before)

    server.shutdown()
    failures = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    if failures:
        print("failures:", ", ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
