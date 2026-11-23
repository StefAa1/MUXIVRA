"""SNMP discovery: mapping OID → θύρα/συσκευή, με ψεύτικο walker (χωρίς δίκτυο)."""
from sshdeck import lldp, snmpwatch


def fake_walker(host, community, oid, **kwargs):
    return DATA[oid]


DATA = {
    snmpwatch.IF_NAME: {
        "1.3.6.1.2.1.31.1.1.1.1.5": "1/1/3",
        "1.3.6.1.2.1.31.1.1.1.1.9": "1/1/4",
        "1.3.6.1.2.1.31.1.1.1.1.48": "1/1/48",
    },
    snmpwatch.IF_DESCR: {},
    snmpwatch.LLDP_CHASSIS: {
        "1.0.8802.1.1.2.1.4.1.1.5.0.5.1": "00:0b:82:11:22:33",
        "1.0.8802.1.1.2.1.4.1.1.5.0.9.1": "44:19:b6:aa:bb:cc",
    },
    snmpwatch.LLDP_PORT_ID: {
        "1.0.8802.1.1.2.1.4.1.1.7.0.5.1": "00:0b:82:11:22:33",
        "1.0.8802.1.1.2.1.4.1.1.7.0.9.1": "80",
    },
    snmpwatch.LLDP_PORT_DESC: {
        "1.0.8802.1.1.2.1.4.1.1.8.0.5.1": "IP Phone 1625",
    },
    snmpwatch.LLDP_SYS_NAME: {
        "1.0.8802.1.1.2.1.4.1.1.9.0.5.1": "GXP1625",
        "1.0.8802.1.1.2.1.4.1.1.9.0.9.1": "IPC-HDW",
    },
    snmpwatch.BRIDGE_FDB_PORT: {
        "1.3.6.1.2.1.17.4.3.1.2.0.11.130.17.34.51": "5",     # 00:0b:82:11:22:33
        "1.3.6.1.2.1.17.4.3.1.2.68.25.182.170.187.204": "9",  # 44:19:b6:aa:bb:cc
    },
    snmpwatch.BRIDGE_PORT_IFINDEX: {
        "1.3.6.1.2.1.17.1.4.1.2.5": "5",
        "1.3.6.1.2.1.17.1.4.1.2.9": "9",
    },
    snmpwatch.QBridge_FDB_PORT: {
        "1.3.6.1.2.1.17.7.1.2.2.1.2.10.0.11.130.17.34.51": "5",
    },
}


def test_fetch_maps_ports_and_macs():
    res = snmpwatch.fetch("10.10.0.2", "public", walker=fake_walker)
    lldp_rows = res["lldp"]
    assert [r["port"] for r in lldp_rows] == ["1/1/3", "1/1/4"]
    assert lldp_rows[0]["sysname"] == "GXP1625"
    assert lldp_rows[0]["mac"] == "00:0b:82:11:22:33"
    macs = {m["mac"]: m["port"] for m in res["macs"]}
    assert macs["00:0b:82:11:22:33"] == "1/1/3"
    assert macs["44:19:b6:aa:bb:cc"] == "1/1/4"


def test_snmp_lldp_feeds_the_same_matcher():
    res = snmpwatch.fetch("10.10.0.2", "public", walker=fake_walker)
    devices = [{"id": 7, "name": "Phone 101", "mgmt_ip": "", "vendor": "Grandstream",
                "model": "", "kind": "phone"}]
    matched = lldp.match(res["lldp"], devices, {"00:0b:82:11:22:33": 7})
    phone = [n for n in matched["neighbors"] if n["port"] == "1/1/3"][0]
    assert phone["state"] == "matched" and phone["matched_by"] == "mac"
    assert matched["proposed"] == 1


def test_community_never_appears_in_errors(monkeypatch):
    import pytest

    from sshdeck import snmpwatch

    def boom(host, community, oid, **kwargs):
        raise snmpwatch.SnmpError("snmp_unreachable: host")

    with pytest.raises(snmpwatch.SnmpError) as err:
        snmpwatch.fetch("10.10.0.2", "SuperSecretCommunity", walker=boom)
    assert "SuperSecretCommunity" not in str(err.value)


def test_discover_snmp_endpoint_stores_community_sealed(auth_client, monkeypatch):
    """Community γράφεται sealed και δεν επιστρέφεται· το discover διαβάζει SNMP."""
    from sshdeck import db, security, snmpwatch

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    site = auth_client.post("/api/inventory/sites", json={"name": "HQ"}).json()
    rack = auth_client.post("/api/inventory/racks",
                            json={"site_id": site["id"], "name": "R1", "units": 12}).json()
    dev = auth_client.post("/api/inventory/devices", json={
        "rack_id": rack["id"], "name": "Aruba", "kind": "switch", "units": 1,
        "mgmt_ip": "10.10.0.2"}).json()

    res = auth_client.post(f"/api/inventory/devices/{dev['id']}/snmp",
                           json={"community": "s3cret-ro"})
    assert res.status_code == 200 and res.json()["has_community"] is True

    with db.db() as conn:
        stored = conn.execute("SELECT value FROM settings WHERE key = ?",
                              (f"snmp_community:{dev['id']}",)).fetchone()["value"]
    assert "s3cret-ro" not in stored
    assert security.open_sealed(stored) == "s3cret-ro"

    original = snmpwatch.fetch

    def fake_fetch(host, community, **kw):
        kw.pop("walker", None)
        return original(host, community, walker=fake_walker, **kw)

    monkeypatch.setattr(snmpwatch, "fetch", fake_fetch)
    out = auth_client.post(f"/api/inventory/devices/{dev['id']}/discover",
                           json={"mode": "snmp"}).json()
    assert out["source"] == "snmp"
    assert out["lldp"]["proposed"] >= 1
    assert "s3cret-ro" not in str(out)
    ports = {p["port"] for p in out["mac"]["ports"]}
    assert "1/1/3" in ports


def test_discover_without_community_is_rejected(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    site = auth_client.post("/api/inventory/sites", json={"name": "HQ2"}).json()
    rack = auth_client.post("/api/inventory/racks",
                            json={"site_id": site["id"], "name": "R2", "units": 12}).json()
    dev = auth_client.post("/api/inventory/devices", json={
        "rack_id": rack["id"], "name": "SW", "kind": "switch", "units": 1}).json()
    res = auth_client.post(f"/api/inventory/devices/{dev['id']}/discover", json={"mode": "snmp"})
    assert res.status_code == 422
    assert res.json()["detail"] == "snmp_community_missing"
