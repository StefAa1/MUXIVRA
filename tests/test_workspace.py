"""Configuration workspace: adapters, versions, diff, apply guard, reconcile."""
import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def device_with_profile(client, customer_head, vendor="mikrotik"):
    host = client.post("/api/hosts", headers=customer_head,
                       json={"label": f"sw-{vendor}", "hostname": "10.9.9.9", "protocol": "ssh"}).json()
    site = client.post("/api/netdoc/sites", headers=customer_head, json={"name": f"S-{vendor}"}).json()
    rack = client.post("/api/netdoc/racks", headers=customer_head,
                       json={"site_id": site["id"], "name": "R", "units": 42}).json()
    device = client.post("/api/netdoc/devices", headers=customer_head,
                         json={"rack_id": rack["id"], "name": f"dev-{vendor}", "position": 5,
                               "host_id": host["id"]}).json()
    return host, rack, device


def test_paste_version_diff_and_reconcile(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    head = {}
    host, rack, device = device_with_profile(auth_client, head)

    mikrotik = (FIXTURES / "mikrotik_export.txt").read_text()
    first = auth_client.post(f"/api/netdoc/devices/{device['id']}/config/paste",
                             json={"text": mikrotik, "vendor": "mikrotik"}).json()
    assert first["size"] > 100 and len(first["sha256"]) == 64

    versions = auth_client.get(f"/api/netdoc/devices/{device['id']}/config/versions").json()["versions"]
    assert len(versions) == 1

    # reconcile uses the LATEST observation (the documented truth stays explicit)
    preview = auth_client.post(f"/api/netdoc/devices/{device['id']}/reconcile", json={"apply": False}).json()
    kinds = {a["kind"] for a in preview["actions"]}
    assert "port_missing" in kinds
    assert preview["applied"] is False
    ports_before = auth_client.get(f"/api/netdoc/rack/{rack['id']}").json()["devices"][0]["ports"]
    assert ports_before == []          # preview must not write

    applied = auth_client.post(f"/api/netdoc/devices/{device['id']}/reconcile", json={"apply": True}).json()
    assert applied["applied"] is True
    device_doc = auth_client.get(f"/api/netdoc/rack/{rack['id']}").json()["devices"][0]
    names = {p["name"] for p in device_doc["ports"]}
    assert {"ether2", "bridge-lan"} <= names
    assert device_doc["mgmt_ip"] == "192.168.88.1"     # observed IP, documented once

    # a second, changed configuration gives a real diff
    changed = mikrotik.replace("add name=ether2", "add name=ether9")
    second = auth_client.post(f"/api/netdoc/devices/{device['id']}/config/paste",
                              json={"text": changed, "vendor": "mikrotik"}).json()
    d = auth_client.get("/api/netdoc/config/diff", params={"left": first["id"], "right": second["id"]}).json()
    assert d["changed"] is True and d["added"] >= 1 and d["removed"] >= 1
    assert "ether9" in d["diff"]
    # secrets never leave in a diff
    assert "SuperSecret123" not in d["diff"]


def test_no_cable_is_ever_invented(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    text = (FIXTURES / "cisco_running.txt").read_text()
    auth_client.post(f"/api/netdoc/devices/{device['id']}/config/paste",
                     json={"text": text, "vendor": "cisco"})
    auth_client.post(f"/api/netdoc/devices/{device['id']}/reconcile", json={"apply": True})
    tree = auth_client.get("/api/netdoc/tree").json()
    assert tree["links"] == 0
    assert auth_client.get("/api/netdoc/links").json() == []


def test_vendor_without_adapter_has_no_fake_editor(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {}, vendor="unknown")
    res = auth_client.post(f"/api/netdoc/devices/{device['id']}/config/fetch",
                           json={"vendor": "acme"})
    assert res.status_code == 422 and res.json()["detail"] == "vendor_not_supported"


def test_apply_requires_confirmation_and_fresh_version(auth_client):
    from sshdeck import workspace, db

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    text = (FIXTURES / "mikrotik_export.txt").read_text()
    info = auth_client.post(f"/api/netdoc/devices/{device['id']}/config/paste",
                            json={"text": text, "vendor": "mikrotik"}).json()
    sent = []
    with db.db() as conn:
        with pytest.raises(workspace.ConfigError) as exc:
            workspace.apply(conn, device["id"], "/system identity set name=x",
                            info["sha256"], runner=sent.append, confirm=False)
        assert exc.value.code == "confirmation_required"
        assert sent == []
        with pytest.raises(workspace.ConfigError) as stale:
            workspace.apply(conn, device["id"], "/system identity set name=x",
                            "0" * 64, runner=sent.append, confirm=True)
        assert stale.value.code == "stale_config"
        result = workspace.apply(conn, device["id"], "/system identity set name=x\n/system reboot",
                                 info["sha256"], runner=lambda c: "ok", confirm=True)
        assert result["applied"] and not result["partial"]


def test_partial_apply_is_reported(auth_client):
    from sshdeck import workspace, db

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    info = auth_client.post(f"/api/netdoc/devices/{device['id']}/config/paste",
                            json={"text": "/system identity show", "vendor": "mikrotik"}).json()

    def runner(command):
        if "reboot" in command:
            raise RuntimeError("timeout")
        return "ok"

    with db.db() as conn:
        result = workspace.apply(conn, device["id"], "a\nreboot", info["sha256"],
                                 runner=runner, confirm=True)
    assert result["partial"] is True and result["failed"]["command"] == "reboot"


def test_planned_links_are_separate_from_physical_cables(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    planned = auth_client.post("/api/netdoc/devices", json={
        "rack_id": rack["id"], "name": "planned-ap", "position": 9, "kind": "ap",
        "origin": "planned"}).json()
    link = auth_client.post("/api/netdoc/planned-links", json={
        "from_device": device["id"], "to_device": planned["id"], "label": "μελλοντικό uplink"}).json()
    assert link["id"]
    assert auth_client.get("/api/netdoc/planned-links").json()[0]["label"] == "μελλοντικό uplink"
    assert auth_client.get("/api/netdoc/links").json() == []       # no physical cable created
    assert auth_client.get("/api/netdoc/tree").json()["links"] == 0
    assert auth_client.delete(f"/api/netdoc/planned-links/{link['id']}").json()["ok"] is True


def test_config_versions_are_customer_scoped(two):
    client, a, b = two
    ha, hb = h(a), h(b)
    host_a, rack_a, device_a = device_with_profile(client, ha)
    info = client.post(f"/api/netdoc/devices/{device_a['id']}/config/paste",
                       headers=ha, json={"text": "x", "vendor": "mikrotik"}).json()
    assert client.get(f"/api/netdoc/config/versions/{info['id']}", headers=hb).status_code in (404, 409)
    assert client.get(f"/api/netdoc/devices/{device_a['id']}/config/versions", headers=hb).status_code in (404, 409)


def h(customer):
    return {"x-customer-id": str(customer["id"])}


@pytest.fixture()
def two(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    a = auth_client.post("/api/customers", json={"name": "Πελάτης Α", "code": "A"}).json()
    b = auth_client.post("/api/customers", json={"name": "Πελάτης Β", "code": "B"}).json()
    return auth_client, a, b
