"""Two customers with identical names and private IPs must never mix."""
import pytest


@pytest.fixture()
def two(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    a = auth_client.post("/api/customers", json={"name": "Πελάτης Α", "code": "A"}).json()
    b = auth_client.post("/api/customers", json={"name": "Πελάτης Β", "code": "B"}).json()
    return auth_client, a, b


def h(customer):
    return {"x-customer-id": str(customer["id"])}


def seed(client, customer):
    """Same site name, same host label, same private IP, in both customers."""
    head = h(customer)
    host = client.post("/api/hosts", headers=head,
                       json={"label": "Router", "hostname": "192.168.1.1", "protocol": "ssh"}).json()
    site = client.post("/api/netdoc/sites", headers=head, json={"name": "Κεντρικό"}).json()
    rack = client.post("/api/netdoc/racks", headers=head,
                       json={"site_id": site["id"], "name": "R1", "units": 42}).json()
    sw = client.post("/api/netdoc/devices", headers=head,
                     json={"rack_id": rack["id"], "name": "sw", "position": 10, "host_id": host["id"]}).json()
    pp = client.post("/api/netdoc/devices", headers=head,
                     json={"rack_id": rack["id"], "name": "pp", "kind": "patch-panel", "position": 11}).json()
    a = client.post("/api/netdoc/ports/bulk", headers=head,
                    json={"device_id": sw["id"], "prefix": "e", "count": 2}).json()["created"]
    b = client.post("/api/netdoc/ports/bulk", headers=head,
                    json={"device_id": pp["id"], "prefix": "p", "count": 2}).json()["created"]
    link = client.post("/api/netdoc/links", headers=head,
                       json={"port_a": a[0]["id"], "port_b": b[0]["id"], "cable": "C-1"}).json()
    return {"host": host, "site": site, "rack": rack, "sw": sw, "pp": pp,
            "ports": a + b, "link": link}


def test_same_names_and_ips_are_isolated(two):
    client, a, b = two
    sa = seed(client, a)
    sb = seed(client, b)

    assert sa["host"]["uuid"] != sb["host"]["uuid"]
    assert len(client.get("/api/hosts", headers=h(a)).json()) == 1
    assert len(client.get("/api/hosts", headers=h(b)).json()) == 1
    tree_a = client.get("/api/netdoc/tree", headers=h(a)).json()
    tree_b = client.get("/api/netdoc/tree", headers=h(b)).json()
    assert tree_a["sites"][0]["id"] == sa["site"]["id"]
    assert tree_b["sites"][0]["id"] == sb["site"]["id"]
    assert tree_a["links"] == 1 and tree_b["links"] == 1

    exp_a = client.get("/api/netdoc/export", headers=h(a)).json()
    exp_b = client.get("/api/netdoc/export", headers=h(b)).json()
    assert {s["name"] for s in exp_a["sites"]} == {"Κεντρικό"}
    assert exp_a["devices"][0]["id"] != exp_b["devices"][0]["id"]
    assert sa["host"]["uuid"] in {p["host_uuid"] for p in exp_a["profiles"]}
    assert sb["host"]["uuid"] not in {p["host_uuid"] for p in exp_a["profiles"]}


def test_cross_customer_cable_is_rejected(two):
    client, a, b = two
    sa = seed(client, a)
    sb = seed(client, b)
    res = client.post("/api/netdoc/links", headers=h(a),
                      json={"port_a": sa["ports"][0]["id"], "port_b": sb["ports"][0]["id"]})
    assert res.status_code == 422, res.text
    assert "cross_customer" in res.json()["detail"]


def test_cross_customer_profile_is_rejected(two):
    client, a, b = two
    sa = seed(client, a)
    sb = seed(client, b)
    res = client.post(f"/api/netdoc/devices/{sa['sw']['id']}/profiles", headers=h(a),
                      json={"host_id": sb["host"]["id"]})
    assert res.status_code in (409, 422), res.text


def test_writes_cannot_touch_another_customers_objects(two):
    client, a, b = two
    sa = seed(client, a)
    # customer B tries to rename A's device and to create a rack inside A's site
    assert client.patch(f"/api/netdoc/devices/{sa['sw']['id']}", headers=h(b),
                        json={"name": "hijack"}).status_code in (404, 409)
    assert client.post("/api/netdoc/racks", headers=h(b),
                       json={"site_id": sa["site"]["id"], "name": "R2"}).status_code in (404, 409)


def test_legacy_data_lands_in_unclassified_customer(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    auth_client.post("/api/hosts", json={"label": "legacy", "hostname": "10.0.0.1", "protocol": "ssh"})
    names = [c["name"] for c in auth_client.get("/api/customers").json()]
    assert "Μη ταξινομημένος πελάτης" in names
    unclassified = next(c for c in auth_client.get("/api/customers").json()
                        if c["name"] == "Μη ταξινομημένος πελάτης")
    assert len(auth_client.get("/api/hosts", headers=h(unclassified)).json()) == 1
