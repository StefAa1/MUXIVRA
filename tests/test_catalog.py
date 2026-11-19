"""Vendor catalog: real models and their port layouts."""
import pytest

from sshdeck import catalog


def test_four_vendors_with_models_and_ports():
    vendors = catalog.vendors()
    keys = {vendor["key"] for vendor in vendors}
    assert keys == {"cisco", "mikrotik", "aruba", "ubiquiti"}
    for vendor in vendors:
        assert vendor["models"], vendor["key"]
        for model in vendor["models"]:
            assert model["ports"] > 0, model["key"]
            assert model["kind"] in {"switch", "router", "firewall", "ap"}


def test_port_names_follow_vendor_convention():
    cisco = catalog.get("cisco-c9200-24p")
    names = [p["name"] for p in cisco.ports()]
    assert names[:2] == ["Gi0/1", "Gi0/2"]
    assert "Gi0/25" in names and len(names) == 28
    assert cisco.units == 1

    mikrotik = catalog.get("mikrotik-crs328-24p")
    mik = [p["name"] for p in mikrotik.ports()]
    assert mik[0] == "ether1" and "sfp-sfpplus1" in mik

    aruba = catalog.get("aruba-2930f-24g")
    aru = [p["name"] for p in aruba.ports()]
    assert aru[0] == "1" and "25" in aru

    ubiquiti = catalog.get("ubiquiti-usw-24-poe")
    ub = [p["name"] for p in ubiquiti.ports()]
    assert ub[0] == "Port 1" and "SFP+ 1" in ub


def test_unknown_model_is_rejected():
    with pytest.raises(catalog.CatalogError) as exc:
        catalog.get("nope-123")
    assert exc.value.code == "unknown_model"


def test_device_from_model_gets_vendor_and_ports(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    site = auth_client.post("/api/netdoc/sites", json={"name": "S"}).json()
    rack = auth_client.post("/api/netdoc/racks", json={"site_id": site["id"], "name": "R", "units": 42}).json()
    device = auth_client.post("/api/netdoc/devices", json={
        "rack_id": rack["id"], "name": "core-sw", "position": 10,
        "model_key": "mikrotik-crs328-24p",
    }).json()
    assert device["vendor"] == "mikrotik"
    assert device["model"] == "CRS328-24P-4S+RM"
    layout = auth_client.get(f"/api/netdoc/rack/{rack['id']}").json()
    ports = layout["devices"][0]["ports"]
    assert len(ports) == 28
    assert {p["name"] for p in ports} >= {"ether1", "ether24", "sfp-sfpplus1"}

    # idempotent: a second application never duplicates ports
    from sshdeck import catalog as cat, db
    with db.db() as conn:
        created = cat.create_ports(conn, device["id"], "mikrotik-crs328-24p")
    assert created == 0


def test_catalog_endpoint_lists_models(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    payload = auth_client.get("/api/netdoc/catalog").json()
    assert len(payload["vendors"]) == 4
    aruba = auth_client.get("/api/netdoc/catalog/aruba").json()
    assert all(m["vendor"] == "aruba" for m in aruba["models"])
