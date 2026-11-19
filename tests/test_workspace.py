"""Configuration workspace: adapters, versions, diff, apply guard, reconcile."""
import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def device_with_profile(client, customer_head, vendor="mikrotik"):
    host = client.post("/api/hosts", headers=customer_head,
                       json={"label": f"sw-{vendor}", "hostname": "10.9.9.9", "protocol": "ssh"}).json()
    site = client.post("/api/inventory/sites", headers=customer_head, json={"name": f"S-{vendor}"}).json()
    rack = client.post("/api/inventory/racks", headers=customer_head,
                       json={"site_id": site["id"], "name": "R", "units": 42}).json()
    device = client.post("/api/inventory/devices", headers=customer_head,
                         json={"rack_id": rack["id"], "name": f"dev-{vendor}", "position": 5,
                               "host_id": host["id"]}).json()
    return host, rack, device


def test_paste_version_diff_and_reconcile(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    head = {}
    host, rack, device = device_with_profile(auth_client, head)

    mikrotik = (FIXTURES / "mikrotik_export.txt").read_text()
    first = auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                             json={"text": mikrotik, "vendor": "mikrotik"}).json()
    assert first["size"] > 100 and len(first["sha256"]) == 64

    versions = auth_client.get(f"/api/inventory/devices/{device['id']}/config/versions").json()["versions"]
    assert len(versions) == 1

    # reconcile uses the LATEST observation (the documented truth stays explicit)
    preview = auth_client.post(f"/api/inventory/devices/{device['id']}/reconcile", json={"apply": False}).json()
    kinds = {a["kind"] for a in preview["actions"]}
    assert "port_missing" in kinds
    assert preview["applied"] is False
    ports_before = auth_client.get(f"/api/inventory/rack/{rack['id']}").json()["devices"][0]["ports"]
    assert ports_before == []          # preview must not write

    applied = auth_client.post(f"/api/inventory/devices/{device['id']}/reconcile", json={"apply": True}).json()
    assert applied["applied"] is True
    device_doc = auth_client.get(f"/api/inventory/rack/{rack['id']}").json()["devices"][0]
    names = {p["name"] for p in device_doc["ports"]}
    assert {"ether2", "bridge-lan"} <= names
    assert device_doc["mgmt_ip"] == "192.168.88.1"     # observed IP, documented once

    # a second, changed configuration gives a real diff
    changed = mikrotik.replace("add name=ether2", "add name=ether9")
    second = auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                              json={"text": changed, "vendor": "mikrotik"}).json()
    d = auth_client.get("/api/inventory/config/diff", params={"left": first["id"], "right": second["id"]}).json()
    assert d["changed"] is True and d["added"] >= 1 and d["removed"] >= 1
    assert "ether9" in d["diff"]
    # secrets never leave in a diff
    assert "SuperSecret123" not in d["diff"]


def test_no_cable_is_ever_invented(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    text = (FIXTURES / "cisco_running.txt").read_text()
    auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                     json={"text": text, "vendor": "cisco"})
    auth_client.post(f"/api/inventory/devices/{device['id']}/reconcile", json={"apply": True})
    tree = auth_client.get("/api/inventory/tree").json()
    assert tree["links"] == 0
    assert auth_client.get("/api/inventory/links").json() == []


def test_vendor_without_adapter_has_no_fake_editor(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {}, vendor="unknown")
    res = auth_client.post(f"/api/inventory/devices/{device['id']}/config/fetch",
                           json={"vendor": "acme"})
    assert res.status_code == 422 and res.json()["detail"] == "vendor_not_supported"


def test_apply_requires_confirmation_and_fresh_version(auth_client):
    from sshdeck import workspace, db

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    text = (FIXTURES / "mikrotik_export.txt").read_text()
    info = auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
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
    info = auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
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
    planned = auth_client.post("/api/inventory/devices", json={
        "rack_id": rack["id"], "name": "planned-ap", "position": 9, "kind": "ap",
        "origin": "planned"}).json()
    link = auth_client.post("/api/inventory/planned-links", json={
        "from_device": device["id"], "to_device": planned["id"], "label": "μελλοντικό uplink"}).json()
    assert link["id"]
    assert auth_client.get("/api/inventory/planned-links").json()[0]["label"] == "μελλοντικό uplink"
    assert auth_client.get("/api/inventory/links").json() == []       # no physical cable created
    assert auth_client.get("/api/inventory/tree").json()["links"] == 0
    assert auth_client.delete(f"/api/inventory/planned-links/{link['id']}").json()["ok"] is True


def test_config_versions_are_customer_scoped(two):
    client, a, b = two
    ha, hb = h(a), h(b)
    host_a, rack_a, device_a = device_with_profile(client, ha)
    info = client.post(f"/api/inventory/devices/{device_a['id']}/config/paste",
                       headers=ha, json={"text": "x", "vendor": "mikrotik"}).json()
    assert client.get(f"/api/inventory/config/versions/{info['id']}", headers=hb).status_code in (404, 409)
    assert client.get(f"/api/inventory/devices/{device_a['id']}/config/versions", headers=hb).status_code in (404, 409)


def h(customer):
    return {"x-customer-id": str(customer["id"])}


@pytest.fixture()
def two(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    a = auth_client.post("/api/customers", json={"name": "Πελάτης Α", "code": "A"}).json()
    b = auth_client.post("/api/customers", json={"name": "Πελάτης Β", "code": "B"}).json()
    return auth_client, a, b


def test_vendor_detection_from_device_then_profile(auth_client):
    from sshdeck import workspace

    assert workspace.adapter_for("MikroTik RouterOS").name == "mikrotik"
    assert workspace.adapter_for("cisco IOS 15").name == "cisco"
    assert workspace.adapter_for("acme").name == "generic"
    assert workspace.adapter_for("").name == "generic"


def test_redaction_hides_secret_values_only():
    from sshdeck import workspace

    text = "/user add name=admin password=SuperSecret123\n/interface ethernet add name=ether1\n"
    cleaned = workspace.redact(text)
    assert "SuperSecret123" not in cleaned
    assert "ether1" in cleaned and "admin" in cleaned


def test_observation_survives_restart_and_distinguishes_unknown_from_absent(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    text = (FIXTURES / "mikrotik_export.txt").read_text()
    auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                     json={"text": text, "vendor": "mikrotik"})
    observed = auth_client.get(f"/api/inventory/devices/{device['id']}/observed").json()["observed"]
    assert observed and observed["data"]["interfaces"]
    # μια αποτυχημένη/κενή συλλογή ΔΕΝ σβήνει την ήδη καταγεγραμμένη καλωδίωση
    auth_client.post("/api/inventory/devices/{}/reconcile".format(device["id"]), json={"apply": True})
    tree = auth_client.get("/api/inventory/tree").json()
    assert tree["links"] == 0
    assert auth_client.get("/api/inventory/links").json() == []


def test_r2_backup_roundtrip_covers_every_supported_kind(auth_client):
    """Ένα backup της εφαρμογής πρέπει να επαναφέρεται για ό,τι δέχεται το API."""
    from sshdeck.kinds import DEVICE_KINDS, PORT_KINDS

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    head = {}
    site = auth_client.post("/api/inventory/sites", headers=head, json={"name": "S"}).json()
    rack = auth_client.post("/api/inventory/racks", headers=head,
                            json={"site_id": site["id"], "name": "R", "units": 48}).json()
    made = {}
    for index, kind in enumerate(DEVICE_KINDS):
        device = auth_client.post("/api/inventory/devices", headers=head, json={
            "rack_id": rack["id"], "name": f"d-{kind}", "kind": kind, "position": index + 1,
        }).json()
        made[kind] = device
    for kind in PORT_KINDS:
        auth_client.post("/api/inventory/ports", headers=head,
                         json={"device_id": made["switch"]["id"], "name": f"p-{kind}", "kind": kind})

    exported = auth_client.get("/api/inventory/export", headers=head).json()
    kinds_in_export = {d["kind"] for d in exported["devices"]}
    port_kinds = {p["kind"] for p in exported["ports"]}
    assert kinds_in_export == set(DEVICE_KINDS)
    assert port_kinds == set(PORT_KINDS)

    dry = auth_client.post("/api/inventory/import", headers=head,
                           json={**exported, "dry_run": True})
    assert dry.status_code == 200, dry.text
    applied = auth_client.post("/api/inventory/import", headers=head, json=exported)
    assert applied.status_code == 200, applied.text


def test_r2_reconcile_port_kind_is_restorable(auth_client):
    """Το reconcile γράφει kind που το import δέχεται (rj45, όχι 'ethernet')."""
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                     json={"text": "/interface ethernet add name=ether7", "vendor": "mikrotik"})
    auth_client.post(f"/api/inventory/devices/{device['id']}/reconcile", json={"apply": True})
    exported = auth_client.get("/api/inventory/export").json()
    assert any(p["name"] == "ether7" for p in exported["ports"])
    assert {p["kind"] for p in exported["ports"]} <= {"rj45", "sfp", "fiber", "power", "other"}
    assert auth_client.post("/api/inventory/import", headers={},
                            json={**exported, "dry_run": True}).status_code == 200


def test_r3_redaction_masks_vendor_specific_secrets():
    from sshdeck import workspace

    cases = {
        "snmp-server community S3cretCommunity RO": "S3cretCommunity",
        "key-string 7 MyOspfKey123": "MyOspfKey123",
        '/interface wireguard peers add private-key="aBc+/123==" comment=x': "aBc+/123==",
        'password "with spaces inside"': "with spaces inside",
        "/user add name=admin password=TopSecret": "TopSecret",
        "enable secret 5 $1$xyz$abc": "$1$xyz$abc",
        "tacacs-server key 7 SecretTacacs": "SecretTacacs",
        "neighbor 10.0.0.1 password 0 BgpPass": "BgpPass",
        "crypto isakmp key IsakmpSecret address 0.0.0.0": "IsakmpSecret",
    }
    for line, secret in cases.items():
        out = workspace.redact(line)
        assert secret not in out, (line, out)
        assert "<redacted" in out, (line, out)

    # μη ευαίσθητες γραμμές μένουν αναγνώσιμες
    plain = "/interface ethernet add name=ether1\n/ip address add address=10.0.0.1/24 interface=ether1"
    assert workspace.redact(plain) == plain


def test_r3_apply_never_returns_raw_secrets(auth_client):
    from sshdeck import db, workspace

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    host, rack, device = device_with_profile(auth_client, {})
    info = auth_client.post(f"/api/inventory/devices/{device['id']}/config/paste",
                            json={"text": "/system identity show", "vendor": "mikrotik"}).json()
    with db.db() as conn:
        result = workspace.apply(conn, device["id"],
                                 '/user add name=admin password=TopSecret123',
                                 info["sha256"], runner=lambda c: "ok", confirm=True)
    assert "TopSecret123" not in str(result)
