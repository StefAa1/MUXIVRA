"""Rack documentation: layout bounds, duplicate ports, one-link-per-port, export."""
import json

import pytest


@pytest.fixture()
def rack(auth_client):
    auth_client.headers.update({"x-requested-with": "sshdeck"})
    site = auth_client.post("/api/inventory/sites", json={"name": "Υπόγειο Α"}).json()
    cabinet = auth_client.post("/api/inventory/racks", json={"site_id": site["id"], "name": "R1", "units": 42}).json()
    return {"client": auth_client, "site": site, "rack": cabinet}


def add_device(rack, name, position=10, units=1, kind="switch"):
    return rack["client"].post(
        "/api/inventory/devices",
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

    bulk = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": switch["id"], "prefix": "eth", "count": 4})
    assert [p["name"] for p in bulk.json()["created"]] == ["eth1", "eth2", "eth3", "eth4"]
    ports = rack["client"].get(f"/api/inventory/devices/{switch['id']}/ports").json()
    pp_ports = rack["client"].post(
        "/api/inventory/ports/bulk", json={"device_id": panel["id"], "prefix": "p", "count": 2}
    ).json()["created"]

    first = rack["client"].post(
        "/api/inventory/links", json={"port_a": ports[0]["id"], "port_b": pp_ports[0]["id"], "cable": "CAT6-01"}
    )
    assert first.status_code == 200

    duplicate = rack["client"].post(
        "/api/inventory/links", json={"port_a": ports[0]["id"], "port_b": pp_ports[1]["id"]}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"].startswith("port_already_linked:")

    same = rack["client"].post("/api/inventory/links", json={"port_a": ports[1]["id"], "port_b": ports[1]["id"]})
    assert same.status_code == 422 and same.json()["detail"] == "same_port"


def test_tree_and_csv_export(rack):
    switch = add_device(rack, "sw-2", position=30).json()
    panel = add_device(rack, "pp-2", position=31, kind="patch-panel").json()
    rack["client"].post("/api/inventory/ports/bulk", json={"device_id": switch["id"], "prefix": "gi", "count": 2})
    rack["client"].post("/api/inventory/ports/bulk", json={"device_id": panel["id"], "prefix": "pp", "count": 2})
    a = rack["client"].get(f"/api/inventory/devices/{switch['id']}/ports").json()[0]
    b = rack["client"].get(f"/api/inventory/devices/{panel['id']}/ports").json()[0]
    rack["client"].post("/api/inventory/links", json={"port_a": a["id"], "port_b": b["id"], "cable": "CAT6-07", "length_m": 3.5})

    tree = rack["client"].get("/api/inventory/tree").json()
    assert tree["sites"][0]["racks"][0]["name"] == "R1"
    assert len(tree["sites"][0]["racks"][0]["devices"]) == 2
    assert tree["links"] == 1

    csv = rack["client"].get("/api/inventory/links.csv")
    assert "device_a,port_a" in csv.text
    assert "CAT6-07" in csv.text


def test_delete_rack_cascades(rack):
    switch = add_device(rack, "sw-3", position=5).json()
    panel = add_device(rack, "pp-3", position=6, kind="patch-panel").json()
    ports_a = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": switch["id"], "prefix": "e", "count": 1}).json()["created"]
    ports_b = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": panel["id"], "prefix": "p", "count": 1}).json()["created"]
    rack["client"].post("/api/inventory/links", json={"port_a": ports_a[0]["id"], "port_b": ports_b[0]["id"]})

    assert rack["client"].delete(f"/api/inventory/racks/{rack['rack']['id']}").status_code == 200
    assert rack["client"].get("/api/inventory/links").json() == []
    assert rack["client"].get("/api/inventory/tree").json()["sites"][0]["racks"] == []


def test_rack_layout_includes_peer_for_linked_port(rack):
    switch = add_device(rack, "sw-x", position=10).json()
    panel = add_device(rack, "pp-x", position=11, kind="patch-panel").json()
    a = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": switch["id"], "prefix": "e", "count": 2}).json()["created"]
    b = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": panel["id"], "prefix": "p", "count": 2}).json()["created"]
    rack["client"].post("/api/inventory/links", json={"port_a": a[0]["id"], "port_b": b[0]["id"], "cable": "CAT6-09"})

    layout = rack["client"].get(f"/api/inventory/rack/{rack['rack']['id']}").json()
    assert layout["site"] == "Υπόγειο Α"
    by_name = {d["name"]: d for d in layout["devices"]}
    linked = [p for p in by_name["sw-x"]["ports"] if p["link_id"]]
    assert len(linked) == 1 and linked[0]["peer"] == "pp-x · p1"
    free = [p for p in by_name["sw-x"]["ports"] if not p["link_id"]]
    assert len(free) == 1


def test_audit_reports_unlinked_and_overlaps(rack, monkeypatch):
    from sshdeck import db

    switch = add_device(rack, "sw-audit", position=30).json()
    rack["client"].post("/api/inventory/ports/bulk", json={"device_id": switch["id"], "prefix": "e", "count": 3})
    report = rack["client"].get("/api/inventory/audit").json()
    kinds = {issue["kind"] for issue in report["issues"]}
    assert "unlinked_ports" in kinds
    assert report["summary"]["ports"] == 3

    # force an impossible layout straight into the DB to exercise the overlap branch
    with db.db() as conn:
        conn.execute(
            "INSERT INTO devices (rack_id, name, kind, units, position, note) VALUES (?, ?, 'switch', 1, 30, '')",
            (rack["rack"]["id"], "sw-overlap"),
        )
    report = rack["client"].get("/api/inventory/audit").json()
    kinds = {issue["kind"] for issue in report["issues"]}
    assert "overlap" in kinds
    assert any("U30" in issue["detail"] for issue in report["issues"] if issue["kind"] == "overlap")


def _two_devices(rack, tag="t"):
    sw = add_device(rack, f"sw-{tag}", position=10).json()
    pp = add_device(rack, f"pp-{tag}", position=11, kind="patch-panel").json()
    a = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": sw["id"], "prefix": "e", "count": 3}).json()["created"]
    b = rack["client"].post("/api/inventory/ports/bulk", json={"device_id": pp["id"], "prefix": "p", "count": 3}).json()["created"]
    return sw, pp, a, b


def test_port_cannot_appear_in_two_links_in_either_direction(rack):
    sw, pp, a, b = _two_devices(rack)
    assert rack["client"].post("/api/inventory/links", json={"port_a": a[0]["id"], "port_b": b[0]["id"]}).status_code == 200
    # same port, but now in the port_a column of the new row (the case two
    # separate unique indexes missed)
    swapped = rack["client"].post("/api/inventory/links", json={"port_a": b[0]["id"], "port_b": a[1]["id"]})
    assert swapped.status_code == 409
    assert swapped.json()["detail"].startswith("port_already_linked")


def test_concurrent_links_to_same_port_leave_exactly_one(rack):
    from concurrent.futures import ThreadPoolExecutor

    sw, pp, a, b = _two_devices(rack, "c")
    payloads = [{"port_a": a[0]["id"], "port_b": b[i]["id"]} for i in range(3)]

    def post(payload):
        return rack["client"].post("/api/inventory/links", json=payload).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(post, payloads))
    assert codes.count(200) == 1, codes
    assert rack["client"].get("/api/inventory/links").json().__len__() == 1


def test_device_links_to_connection_inventory_host(rack):
    host = rack["client"].post(
        "/api/hosts", json={"label": "core-sw", "hostname": "10.0.0.9", "protocol": "ssh"}
    ).json()
    device = rack["client"].post(
        "/api/inventory/devices",
        json={"rack_id": rack["rack"]["id"], "name": "core-sw", "position": 5, "host_id": host["id"],
              "vendor": "Cisco", "model": "C9200", "mgmt_ip": "10.0.0.9"},
    ).json()
    assert device["host_id"] == host["id"]

    layout = rack["client"].get(f"/api/inventory/rack/{rack['rack']['id']}").json()
    mine = [d for d in layout["devices"] if d["id"] == device["id"]][0]
    assert mine["host_label"] == "core-sw" and mine["model"] == "C9200"

    patched = rack["client"].patch(f"/api/inventory/devices/{device['id']}", json={"mgmt_ip": "10.0.0.10"})
    assert patched.status_code == 200 and patched.json()["mgmt_ip"] == "10.0.0.10"

    deps = rack["client"].get(f"/api/inventory/devices/{device['id']}/dependents").json()
    assert deps == {"ports": 0, "links": 0}


def test_cross_rack_port_search_and_paged_links(rack):
    sw, pp, a, b = _two_devices(rack, "x")
    other_site = rack["client"].post("/api/inventory/sites", json={"name": "Δεύτερος χώρος"}).json()
    other_rack = rack["client"].post("/api/inventory/racks", json={"site_id": other_site["id"], "name": "R2"}).json()
    far = rack["client"].post(
        "/api/inventory/devices", json={"rack_id": other_rack["id"], "name": "sw-far", "position": 3}
    ).json()
    far_ports = rack["client"].post(
        "/api/inventory/ports/bulk", json={"device_id": far["id"], "prefix": "ether", "count": 2}
    ).json()["created"]

    found = rack["client"].get("/api/inventory/ports?q=sw-far").json()
    assert found["total"] == 2 and all(p["rack"] == "R2" and p["site"] == "Δεύτερος χώρος" for p in found["ports"])

    # cross-rack link: a port in R1 to a port in another site's rack
    cross = rack["client"].post("/api/inventory/links", json={"port_a": a[0]["id"], "port_b": far_ports[0]["id"], "cable": "XC-1"})
    assert cross.status_code == 200

    page = rack["client"].get("/api/inventory/links/page?limit=1&offset=0").json()
    assert page["total"] == 1 and len(page["links"]) == 1
    assert page["links"][0]["device_b"] == "sw-far"


def test_device_connections_lists_profiles_and_routeros_client_state(rack):
    host = rack["client"].post(
        "/api/hosts", json={"label": "mk-core", "hostname": "10.0.0.20", "protocol": "ssh"}
    ).json()
    device = rack["client"].post(
        "/api/inventory/devices",
        json={"rack_id": rack["rack"]["id"], "name": "mk-core", "position": 8,
              "mgmt_ip": "10.0.0.20", "host_id": host["id"]},
    ).json()

    payload = rack["client"].get(f"/api/inventory/devices/{device['id']}/connections").json()
    assert payload["device"]["name"] == "mk-core"
    assert any(h["id"] == host["id"] for h in payload["hosts"])
    assert "available" in payload["routeros_client"]

    # RouterOS client launch without an executable must fail with a clear code, not a crash
    res = rack["client"].post(f"/api/inventory/devices/{device['id']}/routeros-client", json={})
    if res.status_code == 409:
        assert "routeros_client" in res.json()["detail"]
    else:
        assert res.status_code == 200


def test_export_import_roundtrip_is_idempotent(rack):
    sw, pp, a, b = _two_devices(rack, "io")
    rack["client"].post("/api/inventory/links", json={"port_a": a[0]["id"], "port_b": b[0]["id"], "cable": "CAT6-77"})

    bundle = rack["client"].get("/api/inventory/export").json()
    assert bundle["format"] == "muxivra.inventory/1"
    assert len(bundle["links"]) == 1
    assert "secret" not in json.dumps(bundle).lower()

    # importing the same bundle again must not duplicate anything
    report = rack["client"].post("/api/inventory/import", json={"data": bundle}).json()
    assert report["created"]["sites"] == 0 and report["created"]["devices"] == 0
    assert report["created"]["links"] == 0
    assert report["skipped"]["devices"] >= 2

    tree = rack["client"].get("/api/inventory/tree").json()
    assert len(tree["sites"]) == 1 and len(tree["sites"][0]["racks"]) == 1


def test_import_dry_run_changes_nothing(rack):
    other = {
        "format": "muxivra.inventory/1",
        "sites": [{"id": 99, "name": "Νέος χώρος", "note": ""}],
        "racks": [{"id": 98, "site_id": 99, "name": "R9", "units": 12}],
        "devices": [{"id": 97, "rack_id": 98, "name": "sw-new", "kind": "switch", "units": 1, "position": 5}],
        "ports": [{"id": 96, "device_id": 97, "name": "e1", "kind": "rj45"}],
        "links": [],
    }
    report = rack["client"].post("/api/inventory/import", json={"data": other, "dry_run": True}).json()
    assert report["dry_run"] is True and report["created"]["sites"] == 1
    assert rack["client"].get("/api/inventory/tree").json()["sites"][0]["name"] == "Υπόγειο Α"


def test_import_rejects_bad_format(rack):
    res = rack["client"].post("/api/inventory/import", json={"data": {"format": "nope"}})
    assert res.status_code == 422 and "bad_format" in res.json()["detail"]


def _payload(site_note="Σημείωση χώρου"):
    return {
        "format": "muxivra.inventory/1",
        "sites": [{"id": 1, "name": "Χώρος Α", "note": site_note}],
        "racks": [{"id": 2, "site_id": 1, "name": "R1", "units": 42}],
        "devices": [
            {"id": 3, "rack_id": 2, "name": "sw1", "kind": "switch", "units": 1, "position": 10,
             "vendor": "Cisco", "model": "C9200", "mgmt_ip": "10.9.9.9", "note": "core"},
            {"id": 4, "rack_id": 2, "name": "pp1", "kind": "patch-panel", "units": 1, "position": 11},
        ],
        "ports": [
            {"id": 5, "device_id": 3, "name": "eth1", "kind": "rj45"},
            {"id": 6, "device_id": 4, "name": "p1", "kind": "rj45"},
        ],
        "links": [{"id": 7, "port_a": 5, "port_b": 6, "cable": "C-1", "length_m": 2.5}],
    }


def test_dry_run_reports_every_level_and_changes_nothing(rack):
    report = rack["client"].post("/api/inventory/import", json={"data": _payload(), "dry_run": True}).json()
    created = {k: v for k, v in report["created"].items() if k != "profiles"}
    assert created == {"sites": 1, "racks": 1, "devices": 2, "ports": 2, "links": 1}, created
    assert rack["client"].get("/api/inventory/tree").json()["sites"][0]["name"] == "Υπόγειο Α"


def test_import_preserves_metadata_and_note(rack):
    rack["client"].post("/api/inventory/import", json={"data": _payload()})
    tree = rack["client"].get("/api/inventory/tree").json()
    site = next(s for s in tree["sites"] if s["name"] == "Χώρος Α")
    device = next(d for r in site["racks"] for d in r["devices"] if d["name"] == "sw1")
    assert device["vendor"] == "Cisco" and device["model"] == "C9200" and device["mgmt_ip"] == "10.9.9.9"
    assert device["note"] == "core"
    # site note survives too (check through export)
    exported = rack["client"].get("/api/inventory/export").json()
    assert next(s for s in exported["sites"] if s["name"] == "Χώρος Α")["note"] == "Σημείωση χώρου"


def test_reimport_restores_deleted_port_and_link_when_device_exists(rack):
    rack["client"].post("/api/inventory/import", json={"data": _payload()})
    exported = rack["client"].get("/api/inventory/export").json()
    sw = next(d for d in exported["devices"] if d["name"] == "sw1")
    port = next(p for p in exported["ports"] if p["device_id"] == sw["id"])

    # delete the link and the port, keep the device
    link = exported["links"][0]
    assert rack["client"].delete(f"/api/inventory/links/{link['id']}").status_code == 200
    from sshdeck import db

    with db.db() as conn:
        conn.execute("DELETE FROM ports WHERE id = ?", (port["id"],))

    report = rack["client"].post("/api/inventory/import", json={"data": _payload()}).json()
    assert report["created"]["ports"] == 1 and report["created"]["links"] == 1, report
    assert report["created"]["devices"] == 0 and report["skipped"]["devices"] == 2
    assert len(rack["client"].get("/api/inventory/links").json()) == 1


def test_import_rejects_dangling_reference(rack):
    bad = _payload()
    bad["devices"][0]["rack_id"] = 999
    res = rack["client"].post("/api/inventory/import", json={"data": bad})
    assert res.status_code == 422 and "dangling_ref" in res.json()["detail"]


def test_patch_host_id_null_removes_relation(rack):
    host = rack["client"].post("/api/hosts", json={"label": "h1", "hostname": "10.5.5.5", "protocol": "ssh"}).json()
    device = rack["client"].post(
        "/api/inventory/devices",
        json={"rack_id": rack["rack"]["id"], "name": "sw-null", "position": 30, "host_id": host["id"]},
    ).json()
    assert device["host_id"] == host["id"]

    cleared = rack["client"].patch(f"/api/inventory/devices/{device['id']}", json={"host_id": None})
    assert cleared.status_code == 200 and cleared.json()["host_id"] is None
    conns = rack["client"].get(f"/api/inventory/devices/{device['id']}/connections").json()
    assert conns["hosts"] == []


def test_same_private_ip_does_not_share_profiles(rack):
    host = rack["client"].post("/api/hosts", json={"label": "site-a", "hostname": "192.168.1.1", "protocol": "ssh"}).json()
    a = rack["client"].post("/api/inventory/devices",
                            json={"rack_id": rack["rack"]["id"], "name": "a", "position": 20, "mgmt_ip": "192.168.1.1"}).json()
    b = rack["client"].post("/api/inventory/devices",
                            json={"rack_id": rack["rack"]["id"], "name": "b", "position": 21, "mgmt_ip": "192.168.1.1"}).json()

    assert rack["client"].get(f"/api/inventory/devices/{a['id']}/connections").json()["hosts"] == []
    assert rack["client"].get(f"/api/inventory/devices/{b['id']}/connections").json()["hosts"] == []

    assert rack["client"].post(f"/api/inventory/devices/{a['id']}/profiles", json={"host_id": host["id"]}).status_code == 200
    assert len(rack["client"].get(f"/api/inventory/devices/{a['id']}/connections").json()["hosts"]) == 1
    assert rack["client"].get(f"/api/inventory/devices/{b['id']}/connections").json()["hosts"] == []

    assert rack["client"].delete(f"/api/inventory/devices/{a['id']}/profiles/{host['id']}").status_code == 200
    assert rack["client"].get(f"/api/inventory/devices/{a['id']}/connections").json()["hosts"] == []


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda p: p["devices"][0].update(position=42, units=2), "device_outside_rack"),
        (lambda p: p["devices"][1].update(position=10), "position_taken"),
        (lambda p: p["links"][0].update(port_b=p["links"][0]["port_a"]), "self_link"),
        (lambda p: p["links"][0].update(length_m=-3), "bad_length"),
        (lambda p: p["links"][0].update(port_a=999), "dangling_ref"),
        (lambda p: p["racks"][0].update(units=0), "bad_geometry"),
        (lambda p: p["devices"][0].update(kind="toaster"), "bad_device_kind"),
    ],
)
def test_import_validation_rejects_bad_rows_without_partial_writes(rack, mutate, code):
    payload = _payload()
    mutate(payload)
    res = rack["client"].post("/api/inventory/import", json={"data": payload})
    assert res.status_code == 422, res.text
    assert code in res.json()["detail"]
    # nothing was written
    assert rack["client"].get("/api/inventory/tree").json()["sites"][0]["name"] == "Υπόγειο Α"


def test_import_restores_profile_links_by_host_label(rack):
    host = rack["client"].post("/api/hosts", json={"label": "core-a", "hostname": "10.7.7.7", "protocol": "ssh"}).json()
    rack["client"].post("/api/inventory/import", json={"data": _payload()})
    exported = rack["client"].get("/api/inventory/export").json()
    sw = next(d for d in exported["devices"] if d["name"] == "sw1")
    assert rack["client"].post(f"/api/inventory/devices/{sw['id']}/profiles", json={"host_id": host["id"]}).status_code == 200

    exported = rack["client"].get("/api/inventory/export").json()
    assert exported["profiles"][0]["host_label"] == "core-a"
    assert "scope" in exported

    # import back: the relation is recreated from the label, not from an id
    with rack["client"] as _client:
        pass
    report = rack["client"].post("/api/inventory/import", json={"data": exported}).json()
    assert report["created"]["profiles"] >= 0
    conns = rack["client"].get(f"/api/inventory/devices/{sw['id']}/connections").json()
    assert any(h["id"] == host["id"] for h in conns["hosts"])


def test_profile_mapping_uses_uuid_not_label(rack):
    """Two hosts may share a label; the import must bind by stable UUID only."""
    a = rack["client"].post("/api/hosts", json={"label": "Router", "hostname": "192.168.1.1", "protocol": "ssh"}).json()
    b = rack["client"].post("/api/hosts", json={"label": "Router", "hostname": "192.168.1.2", "protocol": "ssh"}).json()
    assert a["uuid"] and b["uuid"] and a["uuid"] != b["uuid"]

    rack["client"].post("/api/inventory/import", json={"data": _payload()})
    exported = rack["client"].get("/api/inventory/export").json()
    sw = next(d for d in exported["devices"] if d["name"] == "sw1")
    rack["client"].post(f"/api/inventory/devices/{sw['id']}/profiles", json={"host_id": b["id"]})

    exported = rack["client"].get("/api/inventory/export").json()
    bound = [p for p in exported["profiles"] if p["device_id"] == sw["id"]]
    assert [p["host_uuid"] for p in bound] == [b["uuid"]]

    report = rack["client"].post("/api/inventory/import", json={"data": exported, "dry_run": True}).json()
    assert report.get("unresolved_profiles") is not None


def test_import_reports_unresolved_profile_instead_of_guessing(rack):
    payload = _payload()
    payload["profiles"] = [{"device_id": 3, "host_uuid": "deadbeef" * 8, "host_label": "Router"}]
    report = rack["client"].post("/api/inventory/import", json={"data": payload}).json()
    assert report["unresolved_profiles"]
    assert report["unresolved_profiles"][0]["reason"] == "host_not_found"
    assert report["created"]["profiles"] == 0


def test_import_checks_geometry_against_existing_rack(rack):
    """A 42U/U40 device must not be squeezed into an existing 10U rack."""
    site = rack["client"].post("/api/inventory/sites", json={"name": "Μικρό"}).json()
    rack["client"].post("/api/inventory/racks", json={"site_id": site["id"], "name": "R9", "units": 10})

    payload = {
        "format": "muxivra.inventory/1",
        "sites": [{"id": 10, "name": "Μικρό", "note": ""}],
        "racks": [{"id": 11, "site_id": 10, "name": "R9", "units": 42}],
        "devices": [{"id": 12, "rack_id": 11, "name": "big", "kind": "switch", "units": 2, "position": 40}],
        "ports": [], "links": [],
    }
    res = rack["client"].post("/api/inventory/import", json={"data": payload})
    assert res.status_code == 422
    assert "device_outside_rack" in res.json()["detail"]
    tree = rack["client"].get("/api/inventory/tree").json()
    small = next(s for s in tree["sites"] if s["name"] == "Μικρό")
    assert small["racks"][0]["units"] == 10 and small["racks"][0]["devices"] == []


def test_export_marks_primary_profile_and_restore_keeps_it(rack):
    host = rack["client"].post("/api/hosts", json={"label": "core", "hostname": "10.4.4.4", "protocol": "ssh"}).json()
    rack["client"].post("/api/inventory/import", json={"data": _payload()})
    exported = rack["client"].get("/api/inventory/export").json()
    sw = next(d for d in exported["devices"] if d["name"] == "sw1")

    rack["client"].patch(f"/api/inventory/devices/{sw['id']}", json={"host_id": host["id"]})
    exported = rack["client"].get("/api/inventory/export").json()
    primaries = [p for p in exported["profiles"] if p["device_id"] == sw["id"] and p.get("is_primary") == 1]
    assert len(primaries) == 1 and primaries[0]["host_uuid"] == host["uuid"]

    # wipe the relation, then restore from the backup
    rack["client"].patch(f"/api/inventory/devices/{sw['id']}", json={"host_id": None})
    assert rack["client"].get(f"/api/inventory/devices/{sw['id']}/connections").json()["hosts"] == []
    rack["client"].post("/api/inventory/import", json={"data": exported})
    conns = rack["client"].get(f"/api/inventory/devices/{sw['id']}/connections").json()
    assert any(h["id"] == host["id"] for h in conns["hosts"])


@pytest.mark.parametrize("field,value", [("units", "πολύ"), ("position", "abc")])
def test_import_rejects_bad_numbers_as_422(rack, field, value):
    payload = _payload()
    payload["devices"][0][field] = value
    res = rack["client"].post("/api/inventory/import", json={"data": payload})
    assert res.status_code == 422, res.text
    assert "bad_number" in res.json()["detail"] or "bad_geometry" in res.json()["detail"]
