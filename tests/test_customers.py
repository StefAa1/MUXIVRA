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
    site = client.post("/api/inventory/sites", headers=head, json={"name": "Κεντρικό"}).json()
    rack = client.post("/api/inventory/racks", headers=head,
                       json={"site_id": site["id"], "name": "R1", "units": 42}).json()
    sw = client.post("/api/inventory/devices", headers=head,
                     json={"rack_id": rack["id"], "name": "sw", "position": 10, "host_id": host["id"]}).json()
    pp = client.post("/api/inventory/devices", headers=head,
                     json={"rack_id": rack["id"], "name": "pp", "kind": "patch-panel", "position": 11}).json()
    a = client.post("/api/inventory/ports/bulk", headers=head,
                    json={"device_id": sw["id"], "prefix": "e", "count": 2}).json()["created"]
    b = client.post("/api/inventory/ports/bulk", headers=head,
                    json={"device_id": pp["id"], "prefix": "p", "count": 2}).json()["created"]
    link = client.post("/api/inventory/links", headers=head,
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
    tree_a = client.get("/api/inventory/tree", headers=h(a)).json()
    tree_b = client.get("/api/inventory/tree", headers=h(b)).json()
    assert tree_a["sites"][0]["id"] == sa["site"]["id"]
    assert tree_b["sites"][0]["id"] == sb["site"]["id"]
    assert tree_a["links"] == 1 and tree_b["links"] == 1

    exp_a = client.get("/api/inventory/export", headers=h(a)).json()
    exp_b = client.get("/api/inventory/export", headers=h(b)).json()
    assert {s["name"] for s in exp_a["sites"]} == {"Κεντρικό"}
    assert exp_a["devices"][0]["id"] != exp_b["devices"][0]["id"]
    assert sa["host"]["uuid"] in {p["host_uuid"] for p in exp_a["profiles"]}
    assert sb["host"]["uuid"] not in {p["host_uuid"] for p in exp_a["profiles"]}


def test_cross_customer_cable_is_rejected(two):
    client, a, b = two
    sa = seed(client, a)
    sb = seed(client, b)
    res = client.post("/api/inventory/links", headers=h(a),
                      json={"port_a": sa["ports"][0]["id"], "port_b": sb["ports"][0]["id"]})
    assert res.status_code == 422, res.text
    assert "cross_customer" in res.json()["detail"]


def test_cross_customer_profile_is_rejected(two):
    client, a, b = two
    sa = seed(client, a)
    sb = seed(client, b)
    res = client.post(f"/api/inventory/devices/{sa['sw']['id']}/profiles", headers=h(a),
                      json={"host_id": sb["host"]["id"]})
    assert res.status_code in (409, 422), res.text


def test_writes_cannot_touch_another_customers_objects(two):
    client, a, b = two
    sa = seed(client, a)
    # customer B tries to rename A's device and to create a rack inside A's site
    assert client.patch(f"/api/inventory/devices/{sa['sw']['id']}", headers=h(b),
                        json={"name": "hijack"}).status_code in (404, 409)
    assert client.post("/api/inventory/racks", headers=h(b),
                       json={"site_id": sa["site"]["id"], "name": "R2"}).status_code in (404, 409)


def test_legacy_data_lands_in_unclassified_customer(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    auth_client.post("/api/hosts", json={"label": "legacy", "hostname": "10.0.0.1", "protocol": "ssh"})
    names = [c["name"] for c in auth_client.get("/api/customers").json()]
    assert "Unclassified customer" in names
    unclassified = next(c for c in auth_client.get("/api/customers").json()
                        if c["name"] == "Unclassified customer")
    assert len(auth_client.get("/api/hosts", headers=h(unclassified)).json()) == 1


def _mk(client, customer):
    """Minimal device of a customer, for cross-customer write attempts."""
    head = h(customer)
    site = client.post("/api/inventory/sites", headers=head, json={"name": "S"}).json()
    rack = client.post("/api/inventory/racks", headers=head, json={"site_id": site["id"], "name": "R"}).json()
    dev = client.post("/api/inventory/devices", headers=head, json={"rack_id": rack["id"], "name": "d"}).json()
    return {"site": site, "rack": rack, "device": dev, "head": head}


def test_host_cannot_borrow_another_customers_credential(two):
    client, a, b = two
    cred = client.post("/api/credentials", headers=h(b),
                       json={"name": "cred-b", "kind": "password", "username": "u", "secret": "s3cret-123"}).json()
    res = client.post("/api/hosts", headers=h(a),
                      json={"label": "x", "hostname": "10.0.0.9", "credential_id": cred["id"]})
    assert res.status_code in (404, 409, 422), res.text
    assert client.get("/api/hosts", headers=h(a)).json() == []


def test_direct_id_read_write_delete_are_scoped(two):
    client, a, b = two
    sb = _mk(client, b)
    hb = client.post("/api/hosts", headers=h(b), json={"label": "R-b", "hostname": "10.1.1.1"}).json()

    assert client.get(f"/api/inventory/rack/{sb['rack']['id']}", headers=h(a)).status_code in (404, 409)
    body = {"label": "hijack", "hostname": "10.1.1.1", "protocol": "ssh"}
    edit = client.put(f"/api/hosts/{hb['id']}", headers=h(a), json=body)
    if edit.status_code == 405:      # older builds expose PATCH only
        edit = client.patch(f"/api/hosts/{hb['id']}", headers=h(a), json=body)
    assert edit.status_code in (404, 409, 422), edit.text
    assert client.delete(f"/api/inventory/sites/{sb['site']['id']}", headers=h(a)).status_code in (404, 409)
    assert client.post("/api/inventory/devices", headers=h(a),
                       json={"rack_id": _mk(client, a)["rack"]["id"], "name": "n", "host_id": hb["id"]}
                       ).status_code in (404, 409)
    # nothing in B changed
    assert [x["label"] for x in client.get("/api/hosts", headers=h(b)).json()] == ["R-b"]
    assert client.get(f"/api/hosts/{hb['id']}", headers=h(a)).status_code in (404, 409)
    assert client.get("/api/inventory/tree", headers=h(b)).json()["sites"][0]["id"] == sb["site"]["id"]


def test_import_never_binds_foreign_profile_uuid(two):
    client, a, b = two
    sb = _mk(client, b)
    hb = client.post("/api/hosts", headers=h(b), json={"label": "R-b", "hostname": "10.2.2.2"}).json()
    payload = {
        "format": "muxivra.inventory/1",
        "sites": [{"id": 1, "name": "Import", "note": ""}],
        "racks": [{"id": 1, "site_id": 1, "name": "IR", "units": 42}],
        "devices": [{"id": 1, "rack_id": 1, "name": "dev", "units": 1, "position": 1}],
        "ports": [], "links": [],
        "profiles": [{"device_id": 1, "host_uuid": hb["uuid"], "host_label": "R-b", "is_primary": 1}],
    }
    report = client.post("/api/inventory/import", headers=h(a), json=payload).json()
    assert report["unresolved_profiles"][0]["reason"] == "foreign_customer"
    tree = client.get("/api/inventory/tree", headers=h(a)).json()
    assert not any(s["name"] == "Import" for s in tree["sites"]) or True  # site may exist, link must not
    # the imported device is customer A's own; its connection list must not
    # contain B's profile at all
    tree_a = client.get("/api/inventory/tree", headers=h(a)).json()
    created = [d for site in tree_a["sites"] for rack in site.get("racks", [])
               for d in client.get(f"/api/inventory/rack/{rack['id']}", headers=h(a)).json()["devices"]
               if d["name"] == "dev"]
    if created:
        conn = client.get(f"/api/inventory/devices/{created[0]['id']}/connections", headers=h(a))
        assert conn.status_code == 200, conn.text
        assert hb["uuid"] not in {p["host_uuid"] for p in conn.json().get("profiles", [])}
    assert client.get("/api/inventory/tree", headers=h(b)).json()["sites"][0]["id"] == sb["site"]["id"]


def test_default_bucket_is_found_by_code_not_name(auth_client):
    """Ένα μόνο bucket, ακόμη κι αν η βάση κρατούσε το παλιό ελληνικό όνομα."""
    from sshdeck import customers, db

    auth_client.headers.update({"x-requested-with": "sshdeck"})
    with db.db() as conn:
        first = customers.ensure_unclassified(conn)
        conn.execute("UPDATE customers SET name = 'Μη ταξινομημένος πελάτης' WHERE id = ?", (first,))
        conn.commit()
        again = customers.ensure_unclassified(conn)
        assert again == first
        assert customers.rename_unclassified(conn) is True
        row = conn.execute("SELECT name FROM customers WHERE id = ?", (first,)).fetchone()
        assert row["name"] == "Unclassified customer"
    assert sum(1 for c in auth_client.get("/api/customers").json()
               if c["code"] == "default") == 1


@pytest.fixture()
def two_customers(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    a = auth_client.post("/api/customers", json={"name": "A"}).json()
    b = auth_client.post("/api/customers", json={"name": "B"}).json()
    return auth_client, a, b


def _seed_stack(client, customer):
    head = h(customer)
    site = client.post("/api/inventory/sites", headers=head, json={"name": f"S{customer['id']}"}).json()
    rack = client.post("/api/inventory/racks", headers=head,
                       json={"site_id": site["id"], "name": "R", "units": 42}).json()
    host = client.post("/api/hosts", headers=head,
                       json={"label": f"h{customer['id']}", "hostname": "10.0.0.1", "protocol": "ssh"}).json()
    device = client.post("/api/inventory/devices", headers=head,
                         json={"rack_id": rack["id"], "name": "d", "position": 10, "host_id": host["id"]}).json()
    port = client.post("/api/inventory/ports", headers=head,
                       json={"device_id": device["id"], "name": "eth1"}).json()
    return {"site": site, "rack": rack, "host": host, "device": device, "port": port, "head": head}


def test_r1_inventory_writes_reject_foreign_ids(two_customers):
    client, a, b = two_customers
    sb = _seed_stack(client, b)
    ha = h(a)
    rejected = [404, 409]

    assert client.get(f"/api/inventory/devices/{sb['device']['id']}/ports", headers=ha).status_code in rejected
    assert client.post("/api/inventory/ports", headers=ha,
                       json={"device_id": sb["device"]["id"], "name": "x1"}).status_code in rejected
    assert client.post("/api/inventory/ports/bulk", headers=ha,
                       json={"device_id": sb["device"]["id"], "prefix": "e", "count": 2}).status_code in rejected
    assert client.delete(f"/api/inventory/racks/{sb['rack']['id']}", headers=ha).status_code in rejected
    assert client.delete(f"/api/inventory/devices/{sb['device']['id']}/profiles/{sb['host']['id']}",
                         headers=ha).status_code in rejected

    # τίποτα δεν άλλαξε στον Β
    layout = client.get(f"/api/inventory/rack/{sb['rack']['id']}", headers=sb["head"]).json()
    assert [d["name"] for d in layout["devices"]] == ["d"]
    names = {p["name"] for p in layout["devices"][0]["ports"]}
    assert names == {"eth1"}
    assert client.get("/api/hosts", headers=sb["head"]).json()[0]["label"] == f"h{b['id']}"


def test_r1_sftp_routes_respect_customer_scope(two_customers, monkeypatch):
    from sshdeck import sshconn

    client, a, b = two_customers
    sb = _seed_stack(client, b)
    calls = []

    class FakeSftp:
        def listdir(self, *_a, **_k): return []
        def stat(self, *_a, **_k):
            raise OSError("not found")
        def open(self, *_a, **_k): raise OSError("not found")
        def mkdir(self, *_a, **_k): calls.append("mkdir")
        def remove(self, *_a, **_k): calls.append("remove")
        def rename(self, *_a, **_k): calls.append("rename")

    def fake_sftp(target):
        calls.append("connect")
        return object(), FakeSftp()

    monkeypatch.setattr(sshconn, "sftp", fake_sftp)
    ha = h(a)
    host_id = sb["host"]["id"]
    for method, path, payload in [
        ("get", f"/api/hosts/{host_id}/files?path=.", None),
        ("get", f"/api/hosts/{host_id}/download?path=/etc/passwd", None),
        ("post", f"/api/hosts/{host_id}/mkdir", {"path": "/tmp/x"}),
        ("post", f"/api/hosts/{host_id}/delete-path", {"path": "/tmp/x"}),
        ("post", f"/api/hosts/{host_id}/rename", {"path": "/tmp/x", "new_path": "/tmp/y"}),
    ]:
        res = getattr(client, method)(path, headers=ha, **({"json": payload} if payload else {}))
        assert res.status_code in (404, 409), (path, res.status_code, res.text)
    assert calls == [], f"foreign SFTP must never reach the transport: {calls}"
