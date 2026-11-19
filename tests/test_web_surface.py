"""Isolated device web management: validation, per-customer isolation, honest fallback."""
import pathlib

import pytest

from sshdeck import webview_surface as ws


def test_only_http_and_https_and_no_credentials_in_url():
    assert ws.validate_url("https://192.168.88.1/") == "https://192.168.88.1/"
    for bad, code in [
        ("file:///etc/passwd", "bad_scheme"),
        ("javascript:alert(1)", "bad_scheme"),
        ("https://", "bad_url"),
        ("", "url_required"),
        ("http://admin:secret@10.0.0.1/", "credentials_in_url"),
        ("http://admin@10.0.0.1/", "credentials_in_url"),
    ]:
        with pytest.raises(ws.SurfaceError) as exc:
            ws.validate_url(bad)
        assert exc.value.code == code, bad


def test_each_customer_gets_its_own_profile(tmp_path, monkeypatch):
    from sshdeck import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    a = ws.profile_dir(1)
    b = ws.profile_dir(2)
    assert a != b
    assert a.is_dir() and b.is_dir()
    assert "customer-1" in str(a) and "customer-2" in str(b)


def test_surface_has_no_bridge_and_no_app_cookies(tmp_path, monkeypatch):
    from sshdeck import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    cfg = ws.surface_config("https://10.0.0.5/", 7, "Router")
    assert cfg["bridge"] is False
    assert cfg["app_cookies"] is False
    assert cfg["schemes"] == ["http", "https"]
    assert cfg["trust_sha256"] == ""


def test_fallback_is_declared_when_qt_is_missing(monkeypatch):
    monkeypatch.setattr(ws, "qt_available", lambda: False)
    result = ws.launch("https://10.0.0.5/", 1, "Router")
    assert result["mode"] == "external"
    assert "warning" in result and "isolation" in result["warning"]


def test_default_url_comes_from_documented_management_ip(tmp_path, monkeypatch):
    from sshdeck import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    row = {"mgmt_ip": "192.168.88.1", "hostname": ""}
    assert ws.default_url(row) == "http://192.168.88.1/"
    with pytest.raises(ws.SurfaceError) as exc:
        ws.default_url({"mgmt_ip": "", "hostname": ""})
    assert exc.value.code == "no_address"


def test_api_opens_surface_only_for_owned_device(auth_client, monkeypatch):
    from sshdeck import webview_surface as surface

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    launched = []
    monkeypatch.setattr(surface, "qt_available", lambda: True)
    monkeypatch.setattr(surface, "launch", lambda *a, **k: launched.append(a) or {"mode": "window", "pid": 1, "url": a[0]})

    a = auth_client.post("/api/customers", json={"name": "A"}).json()
    b = auth_client.post("/api/customers", json={"name": "B"}).json()
    ha = {"x-customer-id": str(a["id"])}
    hb = {"x-customer-id": str(b["id"])}
    site = auth_client.post("/api/inventory/sites", headers=ha, json={"name": "S"}).json()
    rack = auth_client.post("/api/inventory/racks", headers=ha, json={"site_id": site["id"], "name": "R"}).json()
    dev = auth_client.post("/api/inventory/devices", headers=ha, json={
        "rack_id": rack["id"], "name": "gw", "mgmt_ip": "10.5.5.5"}).json()

    ok = auth_client.post(f"/api/inventory/devices/{dev['id']}/web", headers=ha, json={})
    assert ok.status_code == 200 and ok.json()["mode"] == "window"
    assert launched and "10.5.5.5" in launched[0][0]

    cfg = auth_client.get(f"/api/inventory/devices/{dev['id']}/web/config", headers=ha).json()
    assert cfg["bridge"] is False and cfg["url"].startswith("http://10.5.5.5")

    assert auth_client.post(f"/api/inventory/devices/{dev['id']}/web", headers=hb, json={}).status_code in (404, 409)
    assert auth_client.post(f"/api/inventory/devices/{dev['id']}/web", headers=ha,
                            json={"url": "file:///etc/passwd"}).status_code == 422


def test_host_level_web_endpoint_uses_the_host_address(auth_client, monkeypatch):
    from sshdeck import webview_surface as surface

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    monkeypatch.setattr(surface, "qt_available", lambda: True)
    seen = []
    monkeypatch.setattr(surface, "launch",
                        lambda url, cid, label, trust=None: seen.append((url, cid, label)) or {"mode": "window", "pid": 2, "url": url})
    host = auth_client.post("/api/hosts", json={"label": "core", "hostname": "10.44.0.1", "protocol": "ssh"}).json()
    res = auth_client.post(f"/api/hosts/{host['id']}/web", json={})
    assert res.status_code == 200 and res.json()["mode"] == "window"
    assert seen and "10.44.0.1" in seen[0][0]
    assert auth_client.post(f"/api/hosts/{host['id']}/web", json={"url": "file:///etc/passwd"}).status_code == 422
