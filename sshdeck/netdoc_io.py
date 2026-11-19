"""Versioned JSON backup/import for the documentation data (never credentials)."""
from __future__ import annotations

import sqlite3
import time

FORMAT = "muxivra.netdoc/1"


def export_payload(conn: sqlite3.Connection) -> dict:
    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql)]

    return {
        "format": FORMAT,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sites": rows("SELECT id, name, note FROM sites ORDER BY id"),
        "racks": rows("SELECT id, site_id, name, units FROM racks ORDER BY id"),
        "devices": rows(
            "SELECT id, rack_id, name, kind, units, position, note, host_id, vendor, model, mgmt_ip"
            " FROM devices ORDER BY id"
        ),
        "ports": rows("SELECT id, device_id, name, kind FROM ports ORDER BY id"),
        "links": rows("SELECT id, port_a, port_b, cable, length_m FROM links ORDER BY id"),
        # profile relations travel as host *labels*: importing a backup must never
        # bind a device to a host id that belongs to another database
        "profiles": rows(
            """SELECT dp.device_id, h.label AS host_label
                 FROM device_profiles dp JOIN hosts h ON h.id = dp.host_id
                ORDER BY dp.device_id, h.label"""
        ),
        "scope": "documentation data only: sites, racks, devices, ports, links, profile links; "
                 "no credentials, no vault material, no diagram layout (that stays local)",
    }


class ImportError_(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def validate(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ImportError_("bad_format", f"Αναμένεται format {FORMAT}.")
    for key in ("sites", "racks", "devices", "ports", "links"):
        if not isinstance(payload.get(key), list):
            raise ImportError_("bad_format", f"Λείπει η λίστα «{key}».")
    counters = {k: len(payload.get(k) or []) for k in ("sites", "racks", "devices", "ports", "links")}
    if counters["links"] and not counters["ports"]:
        raise ImportError_("bad_format", "Υπάρχουν links χωρίς ports.")
    return counters


def _site_key(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM sites WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO sites (name, note) VALUES (?, '')", (name,))
    return cur.lastrowid


def _rack_key(conn, site_id: int, name: str, units: int) -> int:
    row = conn.execute("SELECT id FROM racks WHERE site_id = ? AND name = ?", (site_id, name)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO racks (site_id, name, units) VALUES (?, ?, ?)", (site_id, name, units))
    return cur.lastrowid


def _device_key(conn, rack_id: int, device: dict) -> int | None:
    row = conn.execute(
        "SELECT id FROM devices WHERE rack_id = ? AND name = ?", (rack_id, device["name"])
    ).fetchone()
    if row:
        return None  # already documented: never duplicate or overwrite
    cur = conn.execute(
        """INSERT INTO devices (rack_id, name, kind, units, position, note, host_id, vendor, model, mgmt_ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rack_id, device.get("name"), device.get("kind") or "switch", int(device.get("units") or 1),
            int(device.get("position") or 1), device.get("note") or "", None,
            device.get("vendor") or "", device.get("model") or "", device.get("mgmt_ip") or "",
        ),
    )
    return cur.lastrowid


KINDS = {"switch", "router", "patch-panel", "server", "pdu", "other"}
PORT_KINDS = {"rj45", "sfp", "fiber", "power", "other"}


def _validate_rows(payload: dict) -> None:
    for collection in ("sites", "racks", "devices", "ports", "links", "profiles"):
        rows = payload.get(collection)
        if rows is None:
            continue
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ImportError_("bad_format", f"Το «{collection}» πρέπει να είναι λίστα από αντικείμενα.")
    for collection in ("sites", "racks", "devices", "ports", "links"):
        ids = [row.get("id") for row in payload[collection]]
        if any(not isinstance(i, int) for i in ids):
            raise ImportError_("bad_format", f"Κάθε εγγραφή στο «{collection}» χρειάζεται ακέραιο id.")
        if len(set(ids)) != len(ids):
            raise ImportError_("duplicate_id", f"Διπλά ids στο «{collection}».")
    rack_ids = {row["id"] for row in payload["racks"]}
    rack_units: dict = {}
    for rack in payload["racks"]:
        units = rack.get("units", 42)
        units = 42 if units is None else int(units)
        if not 1 <= units <= 60:
            raise ImportError_("bad_geometry", f"Το rack #{rack['id']} έχει άκυρο ύψος.")
        rack_units[rack["id"]] = units
    for rack in payload["racks"]:
        if rack.get("site_id") not in {row["id"] for row in payload["sites"]}:
            raise ImportError_("dangling_ref", f"Το rack #{rack['id']} δείχνει σε άγνωστο χώρο.")
    for device in payload["devices"]:
        if device.get("rack_id") not in rack_ids:
            raise ImportError_("dangling_ref", f"Η συσκευή #{device['id']} δείχνει σε άγνωστο rack.")
        if (device.get("kind") or "switch") not in KINDS:
            raise ImportError_("bad_device_kind", f"Άγνωστος τύπος συσκευής: {device.get('kind')}")
        units, position = int(device.get("units") or 1), int(device.get("position") or 1)
        if units < 1 or position < 1:
            raise ImportError_("bad_geometry", f"Άκυρη θέση για τη συσκευή #{device['id']}.")
        if position + units - 1 > rack_units.get(device.get("rack_id"), 42):
            raise ImportError_("device_outside_rack",
                               f"Η συσκευή #{device['id']} βγαίνει έξω από το rack.")
    # overlap check inside the payload itself
    for rack_id in rack_ids:
        spans = sorted(
            (int(d.get("position") or 1), int(d.get("position") or 1) + int(d.get("units") or 1) - 1, d["id"])
            for d in payload["devices"] if d.get("rack_id") == rack_id
        )
        for (start_a, end_a, id_a), (start_b, end_b, id_b) in zip(spans, spans[1:]):
            if start_b <= end_a:
                raise ImportError_("position_taken",
                                   f"Οι συσκευές #{id_a} και #{id_b} επικαλύπτονται στο ίδιο rack.")
    port_ids = {row["id"] for row in payload["ports"]}
    device_ids = {row["id"] for row in payload["devices"]}
    for port in payload["ports"]:
        if port.get("device_id") not in device_ids:
            raise ImportError_("dangling_ref", f"Το port #{port['id']} δείχνει σε άγνωστη συσκευή.")
        if (port.get("kind") or "rj45") not in PORT_KINDS:
            raise ImportError_("bad_port_kind", f"Άγνωστος τύπος port: {port.get('kind')}")
    for link in payload["links"]:
        if link.get("port_a") not in port_ids or link.get("port_b") not in port_ids:
            raise ImportError_("dangling_ref", f"Η σύνδεση #{link['id']} δείχνει σε άγνωστο port.")
        if link.get("port_a") == link.get("port_b"):
            raise ImportError_("self_link", f"Η σύνδεση #{link['id']} ενώνει το port με τον εαυτό του.")
        try:
            length = float(link.get("length_m") or 0)
        except (TypeError, ValueError):
            raise ImportError_("bad_length", f"Άκυρο μήκος στη σύνδεση #{link['id']}.")
        if not 0 <= length <= 10000:
            raise ImportError_("bad_length", f"Άκυρο μήκος στη σύνδεση #{link['id']}.")


def import_payload(conn: sqlite3.Connection, payload: dict, dry_run: bool = False) -> dict:
    """Restore documentation data. Never overwrites: existing rows are reused.

    A dry run performs exactly the same work and then rolls back, so the preview
    counts are the real ones (ids are mapped correctly at every level).
    """
    counters = validate(payload)
    _validate_rows(payload)
    report = {
        "dry_run": dry_run,
        "created": {"sites": 0, "racks": 0, "devices": 0, "ports": 0, "links": 0, "profiles": 0},
        "skipped": {"sites": 0, "racks": 0, "devices": 0, "ports": 0, "links": 0, "profiles": 0},
        "source": counters,
        "conflicts": [],
    }

    site_map: dict[int, int] = {}
    rack_map: dict[int, int] = {}
    device_map: dict[int, int] = {}
    port_map: dict[int, int] = {}

    for site in payload["sites"]:
        name = str(site.get("name") or "").strip()
        if not name:
            raise ImportError_("bad_format", "Ο χώρος χρειάζεται όνομα.")
        row = conn.execute("SELECT id FROM sites WHERE name = ?", (name,)).fetchone()
        if row:
            site_map[site["id"]] = row["id"]
            report["skipped"]["sites"] += 1
            report["conflicts"].append({"kind": "site", "name": name, "action": "kept-existing"})
        else:
            cur = conn.execute("INSERT INTO sites (name, note) VALUES (?, ?)", (name, site.get("note") or ""))
            site_map[site["id"]] = cur.lastrowid
            report["created"]["sites"] += 1

    for rack in payload["racks"]:
        new_site = site_map[rack["site_id"]]
        name = str(rack.get("name") or "R1")
        row = conn.execute(
            "SELECT id FROM racks WHERE site_id = ? AND name = ?", (new_site, name)
        ).fetchone()
        if row:
            rack_map[rack["id"]] = row["id"]
            report["skipped"]["racks"] += 1
            report["conflicts"].append({"kind": "rack", "name": name, "action": "kept-existing"})
        else:
            cur = conn.execute(
                "INSERT INTO racks (site_id, name, units) VALUES (?, ?, ?)",
                (new_site, name, int(rack.get("units") or 42)),
            )
            rack_map[rack["id"]] = cur.lastrowid
            report["created"]["racks"] += 1

    for device in payload["devices"]:
        new_rack = rack_map[device["rack_id"]]
        name = str(device.get("name") or "").strip()
        row = conn.execute(
            "SELECT id FROM devices WHERE rack_id = ? AND name = ?", (new_rack, name)
        ).fetchone()
        if row:
            # existing device: reuse it so its missing ports/links can be restored.
            # host_id is intentionally dropped: it points at a foreign database.
            device_map[device["id"]] = row["id"]
            report["skipped"]["devices"] += 1
            report["conflicts"].append({"kind": "device", "name": name, "action": "kept-existing"})
        else:
            cur = conn.execute(
                """INSERT INTO devices (rack_id, name, kind, units, position, note, host_id, vendor, model, mgmt_ip)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    new_rack, name, device.get("kind") or "switch", int(device.get("units") or 1),
                    int(device.get("position") or 1), device.get("note") or "",
                    device.get("vendor") or "", device.get("model") or "", device.get("mgmt_ip") or "",
                ),
            )
            device_map[device["id"]] = cur.lastrowid
            report["created"]["devices"] += 1

    for port in payload["ports"]:
        new_device = device_map[port["device_id"]]
        name = str(port.get("name") or "").strip()
        row = conn.execute(
            "SELECT id FROM ports WHERE device_id = ? AND name = ?", (new_device, name)
        ).fetchone()
        if row:
            port_map[port["id"]] = row["id"]
            report["skipped"]["ports"] += 1
        else:
            cur = conn.execute(
                "INSERT INTO ports (device_id, name, kind) VALUES (?, ?, ?)",
                (new_device, name, port.get("kind") or "rj45"),
            )
            port_map[port["id"]] = cur.lastrowid
            report["created"]["ports"] += 1

    for link in payload["links"]:
        a, b = port_map[link["port_a"]], port_map[link["port_b"]]
        busy = conn.execute(
            "SELECT id FROM links WHERE port_a IN (?, ?) OR port_b IN (?, ?)", (a, b, a, b)
        ).fetchone()
        if busy:
            report["skipped"]["links"] += 1
            report["conflicts"].append({"kind": "link", "name": f"{a}-{b}", "action": "port-already-linked"})
            continue
        conn.execute(
            "INSERT INTO links (port_a, port_b, cable, length_m, created_at) VALUES (?, ?, ?, ?, ?)",
            (a, b, link.get("cable") or "", float(link.get("length_m") or 0), time.time()),
        )
        report["created"]["links"] += 1

    # profile relations: match hosts by label, never by foreign id
    for profile in payload.get("profiles") or []:
        device_new = device_map.get(profile.get("device_id"))
        label = str(profile.get("host_label") or "").strip()
        if not device_new or not label:
            report["skipped"]["profiles"] = report["skipped"].get("profiles", 0) + 1
            continue
        host = conn.execute("SELECT id FROM hosts WHERE label = ?", (label,)).fetchone()
        if not host:
            report["skipped"]["profiles"] = report["skipped"].get("profiles", 0) + 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO device_profiles (device_id, host_id) VALUES (?, ?)",
            (device_new, host["id"]),
        )
        report["created"]["profiles"] = report["created"].get("profiles", 0) + 1

    if dry_run:
        conn.rollback()      # identical work, nothing kept
    else:
        conn.commit()
    return report
