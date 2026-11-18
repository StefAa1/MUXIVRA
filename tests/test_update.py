"""GitHub update flow: version compare, API mapping, verified download, staging."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from sshdeck import update as U


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_for(payload=None, body=None):
    def opener(url, timeout):
        if payload is not None:
            return FakeResponse(json.dumps(payload).encode())
        return FakeResponse(body or b"")
    return opener


def test_version_compare():
    assert U.parse_version("v1.2.3") == (1, 2, 3)
    assert U.is_newer("v0.2.0", "0.1.0") is True
    assert U.is_newer("0.1.0", "0.1.0") is False
    assert U.is_newer("0.1.9", "0.1.10") is False
    assert U.is_newer("1.0", "0.9.9") is True


def test_check_maps_release_and_flags_update():
    payload = {
        "tag_name": "v0.2.0",
        "body": "fixes",
        "published_at": "2026-11-18T10:00:00Z",
        "assets": [
            {"name": "notes.txt", "browser_download_url": "https://x/notes.txt", "size": 10},
            {"name": "MUXIVRA-portable-win64.zip", "browser_download_url": "https://x/app.zip", "size": 1234},
        ],
    }
    result = U.check("owner/repo", "0.1.0", opener=opener_for(payload))
    assert result["available"] is True
    assert result["latest"] == "0.2.0"
    assert result["asset"]["name"] == "MUXIVRA-portable-win64.zip"
    assert result["notes"] == "fixes"


def test_check_rejects_bad_repo():
    with pytest.raises(U.UpdateError) as exc:
        U.check("not-a-repo", "0.1.0", opener=opener_for({}))
    assert exc.value.code == "invalid_repo"


def test_download_verifies_digest(tmp_path):
    body = b"zip-bytes"
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    asset = {"name": "app.zip", "url": "https://x/app.zip", "size": len(body), "digest": digest}
    path = U.download(asset, tmp_path, opener=opener_for(body=body))
    assert path.read_bytes() == body

    bad = {"name": "app.zip", "url": "https://x/app.zip", "size": len(body), "digest": "sha256:" + "0" * 64}
    with pytest.raises(U.UpdateError) as exc:
        U.download(bad, tmp_path, opener=opener_for(body=body))
    assert exc.value.code == "bad_digest"


def make_zip(path: Path, with_exe: bool) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        if with_exe:
            zf.writestr("MUXIVRA-portable-win64/MUXIVRA.exe", b"MZ")
            zf.writestr("MUXIVRA-portable-win64/BUILD.txt", b"v0.2.0")
        else:
            zf.writestr("readme.txt", b"nothing useful")


def test_stage_accepts_bundle_and_rejects_other_zip(tmp_path):
    good = tmp_path / "good.zip"
    make_zip(good, True)
    root = U.stage(good, tmp_path / "work")
    assert (root / "MUXIVRA.exe").exists()

    bad = tmp_path / "bad.zip"
    make_zip(bad, False)
    with pytest.raises(U.UpdateError) as exc:
        U.stage(bad, tmp_path / "work")
    assert exc.value.code == "bad_archive"


def test_apply_script_contains_paths_and_backup(tmp_path):
    bundle = tmp_path / "MUXIVRA-portable-win64"
    bundle.mkdir()
    new_root = tmp_path / "staged"
    new_root.mkdir()
    script = U.write_apply_script(bundle, new_root)
    raw = script.read_bytes()
    text = raw.decode("ascii")
    assert str(bundle.resolve()) in text
    assert "backup-before-update" in text
    assert b"\r\n" in raw
    assert 'start ""' in text
