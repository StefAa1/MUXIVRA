"""Signed update flow: Ed25519 manifest, verified download, safe staging, targeted swap."""
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sshdeck import update as U

PRIVATE = Ed25519PrivateKey.generate()
PUBLIC = PRIVATE.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(payload=None, body=None):
    def opener(url, timeout, accept=None):
        if payload is not None:
            return FakeResponse(json.dumps(payload).encode())
        return FakeResponse(body or b"")
    return opener


def make_archive(path: Path, members=None, with_exe=True) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        if with_exe:
            zf.writestr("MUXIVRA-portable-win64/MUXIVRA.exe", b"MZ")
            zf.writestr("MUXIVRA-portable-win64/BUILD.txt", b"v0.4.0")
        for name, data in (members or {}).items():
            zf.writestr(name, data)
    return path.read_bytes()


def signed_manifest(archive_bytes: bytes, version="0.4.0", archive_name="MUXIVRA-portable-win64.zip"):
    manifest = {
        "version": version,
        "archive": archive_name,
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "built_at": "2026-11-18T20:00:00Z",
    }
    raw = json.dumps(manifest, sort_keys=True).encode()
    return manifest, raw, PRIVATE.sign(raw)


def test_version_compare():
    assert U.parse_version("v1.2.3") == (1, 2, 3)
    assert U.is_newer("v0.4.0", "0.3.1") is True
    assert U.is_newer("0.3.1", "0.3.1") is False


def test_check_maps_release_assets():
    payload = {
        "tag_name": "v0.4.0",
        "body": "fixes",
        "assets": [
            {"name": "release.json", "browser_download_url": "https://github.com/x/release.json", "size": 200},
            {"name": "release.json.sig", "browser_download_url": "https://github.com/x/release.json.sig", "size": 88},
            {"name": "MUXIVRA-portable-win64.zip", "browser_download_url": "https://github.com/x/a.zip", "size": 10},
        ],
    }
    result = U.check("owner/repo", "0.3.1", opener=opener_for(payload))
    assert result["available"] is True
    assert result["manifest"]["name"] == "release.json"
    assert result["signature"]["name"] == "release.json.sig"
    assert result["asset"]["name"].endswith(".zip")


def test_manifest_signature_roundtrip_and_tamper(tmp_path):
    archive = tmp_path / "a.zip"
    data = make_archive(archive)
    manifest, raw, signature = signed_manifest(data)

    assert U.verify_manifest(raw, signature, PUBLIC)["version"] == "0.4.0"

    with pytest.raises(U.UpdateError) as exc:
        U.verify_manifest(raw.replace(b"0.4.0", b"0.4.1"), signature, PUBLIC)
    assert exc.value.code == "bad_signature"

    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(U.UpdateError) as exc:
        U.verify_manifest(raw, signature, other)
    assert exc.value.code == "bad_signature"


def test_missing_pubkey_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("SSHDECK_UPDATE_PUBKEY", raising=False)
    from sshdeck import config

    monkeypatch.setattr(config, "UPDATE_PUBKEY_PATH", tmp_path / "absent.txt")
    monkeypatch.setattr(config, "DEFAULT_UPDATE_PUBKEY", "", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path, raising=False)
    archive = tmp_path / "a.zip"
    data = make_archive(archive)
    _manifest, raw, signature = signed_manifest(data)
    with pytest.raises(U.UpdateError) as exc:
        U.verify_manifest(raw, signature)
    assert exc.value.code == "no_pubkey"


def test_fetch_manifest_rejects_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("SSHDECK_UPDATE_PUBKEY", base64.b64encode(PUBLIC).decode())
    archive = tmp_path / "a.zip"
    data = make_archive(archive)
    _manifest, raw, signature = signed_manifest(data, version="0.4.0")

    def opener(url, timeout, accept=None):
        if url.endswith("release.json"):
            return FakeResponse(raw)
        return FakeResponse(base64.b64encode(signature))

    release = {
        "latest": "0.5.0",
        "manifest": {"name": "release.json", "url": "https://github.com/x/release.json", "size": len(raw)},
        "signature": {"name": "release.json.sig", "url": "https://github.com/x/release.json.sig", "size": 88},
    }
    with pytest.raises(U.UpdateError) as exc:
        U.fetch_manifest(release, opener=opener)
    assert exc.value.code == "version_mismatch"


def test_download_enforces_signed_name_and_digest(tmp_path):
    archive = tmp_path / "a.zip"
    data = make_archive(archive)
    manifest, _raw, _sig = signed_manifest(data)
    asset = {"name": "MUXIVRA-portable-win64.zip", "url": "https://github.com/x/a.zip", "size": len(data)}

    path = U.download_verified({"asset": asset}, manifest, tmp_path, opener=opener_for(body=data))
    assert path.read_bytes() == data

    bad = dict(manifest, sha256="0" * 64)
    with pytest.raises(U.UpdateError) as exc:
        U.download_verified({"asset": asset}, bad, tmp_path, opener=opener_for(body=data))
    assert exc.value.code == "bad_digest"

    renamed = {"asset": dict(asset, name="other.zip")}
    with pytest.raises(U.UpdateError) as exc:
        U.download_verified(renamed, manifest, tmp_path, opener=opener_for(body=data))
    assert exc.value.code == "archive_mismatch"


@pytest.mark.parametrize("url", ["http://github.com/x.zip", "https://evil.example.com/x.zip"])
def test_download_refuses_unsafe_urls(tmp_path, url):
    manifest = {"archive": "x.zip", "sha256": "0" * 64, "version": "0.4.0"}
    with pytest.raises(U.UpdateError) as exc:
        U.download_verified(
            {"asset": {"name": "x.zip", "url": url}}, manifest, tmp_path, opener=opener_for(body=b"")
        )
    assert exc.value.code in {"insecure_url", "untrusted_host"}


def test_stage_rejects_traversal_and_symlinks(tmp_path):
    evil = tmp_path / "evil.zip"
    make_archive(evil, {"../escape.txt": b"x"})
    with pytest.raises(U.UpdateError) as exc:
        U.stage(evil, tmp_path / "staging")
    assert exc.value.code == "unsafe_path"

    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as zf:
        info = zipfile.ZipInfo("MUXIVRA-portable-win64/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(U.UpdateError) as exc:
        U.stage(link, tmp_path / "staging")
    assert exc.value.code == "unsafe_path"

    good = tmp_path / "good.zip"
    make_archive(good)
    root = U.stage(good, tmp_path / "staging")
    assert (root / "MUXIVRA.exe").exists()


def test_apply_script_targets_this_pid_and_can_roll_back(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    new_root = tmp_path / "updates" / "staged-1"
    new_root.mkdir(parents=True)
    (new_root / "MUXIVRA.exe").write_bytes(b"MZ")

    script = U.write_apply_script(bundle, new_root, pid=4242, staging_root=tmp_path / "updates")
    raw = script.read_bytes()
    text = raw.decode("ascii")

    assert b"\r\n" in raw
    assert "set PID=4242" in text
    assert 'tasklist /FI "PID eq %PID%"' in text
    assert "taskkill /IM" not in text
    assert "robocopy" in text and "restoring the previous version" in text
    assert "chcp 65001" in text and "DisableDelayedExpansion" in text
    assert "healthcheck" in text and "goto rollback" in text
    assert str(bundle.resolve()) in text and str(new_root.resolve()) in text
    assert script.parent == (tmp_path / "updates").resolve()


def test_apply_script_handles_unicode_and_special_paths(tmp_path):
    """Regression: Greek folder names raised UnicodeEncodeError (.encode('ascii'))."""
    bundle = tmp_path / "Επιφάνεια (x) ! 100%" / "MUXIVRA-portable-win64"
    bundle.mkdir(parents=True)
    (bundle / "MUXIVRA.exe").write_bytes(b"MZ")
    staged_root = tmp_path / "staging ü"
    new_root = staged_root / "staged-1"
    new_root.mkdir(parents=True)
    (new_root / "MUXIVRA.exe").write_bytes(b"MZ")

    script = U.write_apply_script(bundle, new_root, pid=99, staging_root=staged_root)
    text = script.read_bytes().decode("utf-8")

    assert "Επιφάνεια" in text and "ü" in text       # written, not crashed
    assert "100%%" in text                           # % doubled for batch
    assert "! 100" in text or "!" in text            # kept, delayed expansion is off
    assert "DisableDelayedExpansion" in text
    assert b"\r\n" in script.read_bytes()


def test_a_drive_detection_for_cross_drive_swaps():
    assert U.a_drive(Path("C:/Users/x/MUXIVRA")) == "c"
    assert U.a_drive(Path("d:/staging")) == "d"
    assert U.a_drive(Path("/root/sshdeck")) == ""


def test_apply_script_backs_up_database_and_exits_zero(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "MUXIVRA.exe").write_bytes(b"MZ")
    staged = tmp_path / "staging" / "staged"
    staged.mkdir(parents=True)
    (staged / "MUXIVRA.exe").write_bytes(b"MZ")

    text = U.write_apply_script(bundle, staged, 7, tmp_path / "staging").read_text()
    assert "-db.sqlite" in text                       # DB copied aside before the swap
    assert text.rstrip().endswith("exit /b 0")        # headless fixture got 1 after del
    assert "healthcheck" in text


def test_private_repo_token_is_stored_sealed_and_never_returned(auth_client):
    from sshdeck import db, security

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    token = "ghp_" + "x" * 32
    assert auth_client.post("/api/update/token", json={"token": token}).json()["configured"] is True
    state = auth_client.get("/api/update/state").json()
    assert state["token"] is True
    assert token not in str(state)
    with db.db() as conn:
        blob = db.get_setting(conn, "update_token_enc")
    assert token not in blob                      # sealed, not plaintext
    assert security.open_sealed(blob) == token
    auth_client.post("/api/update/token", json={"token": ""})
    assert auth_client.get("/api/update/state").json()["token"] is False


def test_404_explains_private_repo():
    import urllib.error

    from sshdeck import update

    def opener(url, timeout, accept="", token=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    try:
        update.check("owner/name", "0.4.9", opener=opener)
    except update.UpdateError as exc:
        assert exc.code == "repo_not_found"
        assert "private" in exc.message.lower()
    else:
        raise AssertionError("404 must be reported")


def test_official_source_needs_no_configuration(auth_client, monkeypatch):
    """Καθαρή εγκατάσταση: check χωρίς repo, χωρίς token."""
    from sshdeck import api, config, update

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    state = auth_client.get("/api/update/state").json()
    assert state["repo"] == config.DEFAULT_UPDATE_REPO
    assert state["token"] is False

    seen = {}

    def fake_check(repo, current, timeout=12.0, opener=None, token=None):
        seen["repo"] = repo
        seen["token"] = token
        return {"repo": repo, "current": current, "latest": "9.9.9", "available": True,
                "asset": {"name": "MUXIVRA-portable-win64.zip", "url": "https://x/y.zip", "size": 1},
                "manifest": {"name": "release.json", "url": "https://x/release.json", "size": 1},
                "signature": {"name": "release.json.sig", "url": "https://x/release.json.sig", "size": 1}}

    monkeypatch.setattr(update, "check", fake_check)
    res = auth_client.post("/api/update/check", json={})
    assert res.status_code == 200, res.text
    assert seen["repo"] == config.DEFAULT_UPDATE_REPO
    assert seen["token"] == ""


def test_apply_is_honest_off_windows(auth_client):
    from sshdeck import config

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    res = auth_client.post("/api/update/apply", json={})
    assert res.status_code == 409
    assert res.json()["detail"] in {"nothing_staged"} or "apply_requires_windows" in res.json()["detail"]


def test_apply_windows_branch_actually_launches(auth_client, tmp_path, monkeypatch):
    """Ο κλάδος Windows δεν επιστρέφει NameError: φτιάχνει process με το script."""
    import types

    from sshdeck import api, config

    script = tmp_path / "apply-update.cmd"
    script.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path, raising=False)
    called = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            called["args"] = args

    monkeypatch.setattr(api, "subprocess", types.SimpleNamespace(
        Popen=FakePopen, CREATE_NEW_PROCESS_GROUP=0), raising=False)
    monkeypatch.setattr(api.os, "name", "nt")

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    res = auth_client.post("/api/update/apply", json={})
    assert res.status_code == 200, res.text
    assert res.json()["started"] is True
    assert str(script) in " ".join(called["args"])
