"""Rack documentation: layout bounds, duplicate ports, one-link-per-port, export."""
import pytest


@pytest.fixture()
def rack(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    site = auth_client.post("/api/netdoc/sites", json={"name": "Υπόγειο Α"}).json()
    cabinet = auth_client.post("/api/netdoc/racks", json={"site_id": site["id"], "name": "R1", "units": 42}).json()
    return {"client": auth_client, "site": site, "rack": cabinet}


def add_device(rack, name, position=10, units=1, kind="switch"):
    return rack["client"].post(
        "/api/netdoc/devices",
        json={"rack_id": rack["rack"]["id"], "name": name, "position": position, "units": units, "kind": kind},
    )


def test_device_layout_bounds_and_overlap(rack):
    assert add_device(rack, "sw-core-1", position=40, units=3).json()["name"] == "sw-core-1"

    outside = add_device(rack, "sw-too-high", position=41, units=3)
    assert outside.status_code == 422 and outside.json()["detail"] == "device_outside_rack"

    overlap = add_device(rack, "sw-overlap", position=41, units=1)
    assert overlap.status_code == 409 and overlap.json()["detail"].startswith("position_taken:")


def test_bulk_ports_and_duplicate_link_rejected(rack):
    switch = add_device(rack, "sw-1", position=20).json()
    panel = add_device(rack, "pp-1", position=21, kind="patch-panel").json()

    bulk = rack["client"].post("/api/netdoc/ports/bulk", json={"device_id": switch["id"], "prefix": "eth", "count": 4})
    assert [p["name"] for p in bulk.json()["created"]] == ["eth1", "eth2", "eth3", "eth4"]
    ports = rack["client"].get(f"/api/netdoc/devices/{switch['id']}/ports").json()
    pp_ports = rack["client"].post(
        "/api/netdoc/ports/bulk", json={"device_id": panel["id"], "prefix": "p", "count": 2}
    ).json()["created"]

    first = rack["client"].post(
        "/api/netdoc/links", json={"port_a": ports[0]["id"], "port_b": pp_ports[0]["id"], "cable": "CAT6-01"}
    )
    assert first.status_code == 200

    duplicate = rack["client"].post(
        "/api/netdoc/links", json={"port_a": ports[0]["id"], "port_b": pp_ports[1]["id"]}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"].startswith("port_already_linked:")

    same = rack["client"].post("/api/netdoc/links", json={"port_a": ports[1]["id"], "port_b": ports[1]["id"]})
    assert same.status_code == 422 and same.json()["detail"] == "same_port"


def test_tree_and_csv_export(rack):
    switch = add_device(rack, "sw-2", position=30).json()
    panel = add_device(rack, "pp-2", position=31, kind="patch-panel").json()
    rack["client"].post("/api/netdoc/ports/bulk", json={"device_id": switch["id"], "prefix": "gi", "count": 2})
    rack["client"].post("/api/netdoc/ports/bulk", json={"device_id": panel["id"], "prefix": "pp", "count": 2})
    a = rack["client"].get(f"/api/netdoc/devices/{switch['id']}/ports").json()[0]
    b = rack["client"].get(f"/api/netdoc/devices/{panel['id']}/ports").json()[0]
    rack["client"].post("/api/netdoc/links", json={"port_a": a["id"], "port_b": b["id"], "cable": "CAT6-07", "length_m": 3.5})

    tree = rack["client"].get("/api/netdoc/tree").json()
    assert tree["sites"][0]["racks"][0]["name"] == "R1"
    assert len(tree["sites"][0]["racks"][0]["devices"]) == 2
    assert tree["links"] == 1

    csv = rack["client"].get("/api/netdoc/links.csv")
    assert "device_a,port_a" in csv.text
    assert "CAT6-07" in csv.text


def test_delete_rack_cascades(rack):
    switch = add_device(rack, "sw-3", position=5).json()
    panel = add_device(rack, "pp-3", position=6, kind="patch-panel").json()
    ports_a = rack["client"].post("/api/netdoc/ports/bulk", json={"device_id": switch["id"], "prefix": "e", "count": 1}).json()["created"]
    ports_b = rack["client"].post("/api/netdoc/ports/bulk", json={"device_id": panel["id"], "prefix": "p", "count": 1}).json()["created"]
    rack["client"].post("/api/netdoc/links", json={"port_a": ports_a[0]["id"], "port_b": ports_b[0]["id"]})

    assert rack["client"].delete(f"/api/netdoc/racks/{rack['rack']['id']}").status_code == 200
    assert rack["client"].get("/api/netdoc/links").json() == []
    assert rack["client"].get("/api/netdoc/tree").json()["sites"][0]["racks"] == []
