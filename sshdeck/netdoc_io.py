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


def import_payload(conn: sqlite3.Connection, payload: dict, dry_run: bool = False) -> dict:
    counters = validate(payload)
    report = {"dry_run": dry_run, "created": {"sites": 0, "racks": 0, "devices": 0, "ports": 0, "links": 0},
              "skipped": {"devices": 0, "ports": 0, "links": 0}, "source": counters}

    site_map: dict[int, int] = {}
    rack_map: dict[int, int] = {}
    device_map: dict[int, int | None] = {}
    port_map: dict[int, int] = {}

    for site in payload["sites"]:
        name = str(site.get("name") or "").strip()
        if not name:
            continue
        if dry_run:
            exists = conn.execute("SELECT id FROM sites WHERE name = ?", (name,)).fetchone()
            site_map[site["id"]] = exists["id"] if exists else -1
            report["created"]["sites"] += 0 if exists else 1
            continue
        before = conn.execute("SELECT COUNT(*) AS n FROM sites").fetchone()["n"]
        site_map[site["id"]] = _site_key(conn, name)
        after = conn.execute("SELECT COUNT(*) AS n FROM sites").fetchone()["n"]
        report["created"]["sites"] += after - before

    for rack in payload["racks"]:
        new_site = site_map.get(rack.get("site_id"))
        if not new_site or new_site == -1:
            continue
        if dry_run:
            exists = conn.execute(
                "SELECT id FROM racks WHERE site_id = ? AND name = ?", (new_site, rack.get("name"))
            ).fetchone()
            rack_map[rack["id"]] = exists["id"] if exists else -1
            report["created"]["racks"] += 0 if exists else 1
            continue
        before = conn.execute("SELECT COUNT(*) AS n FROM racks").fetchone()["n"]
        rack_map[rack["id"]] = _rack_key(conn, new_site, str(rack.get("name") or "R1"), int(rack.get("units") or 42))
        after = conn.execute("SELECT COUNT(*) AS n FROM racks").fetchone()["n"]
        report["created"]["racks"] += after - before

    for device in payload["devices"]:
        new_rack = rack_map.get(device.get("rack_id"))
        if not new_rack or new_rack == -1:
            continue
        if dry_run:
            exists = conn.execute(
                "SELECT id FROM devices WHERE rack_id = ? AND name = ?", (new_rack, device.get("name"))
            ).fetchone()
            device_map[device["id"]] = exists["id"] if exists else None
            report["created"]["devices"] += 0 if exists else 1
            report["skipped"]["devices"] += 1 if exists else 0
            continue
        new_id = _device_key(conn, new_rack, device)
        device_map[device["id"]] = new_id
        if new_id is None:
            report["skipped"]["devices"] += 1
        else:
            report["created"]["devices"] += 1

    for port in payload["ports"]:
        new_device = device_map.get(port.get("device_id"))
        if not new_device:
            report["skipped"]["ports"] += 1
            continue
        exists = conn.execute(
            "SELECT id FROM ports WHERE device_id = ? AND name = ?", (new_device, port.get("name"))
        ).fetchone()
        if exists:
            port_map[port["id"]] = exists["id"]
            report["skipped"]["ports"] += 1
            continue
        if dry_run:
            port_map[port["id"]] = -1
            report["created"]["ports"] += 1
            continue
        cur = conn.execute(
            "INSERT INTO ports (device_id, name, kind) VALUES (?, ?, ?)",
            (new_device, str(port.get("name") or "p1"), port.get("kind") or "rj45"),
        )
        port_map[port["id"]] = cur.lastrowid
        report["created"]["ports"] += 1

    for link in payload["links"]:
        a, b = port_map.get(link.get("port_a")), port_map.get(link.get("port_b"))
        if not a or not b:
            report["skipped"]["links"] += 1
            continue
        busy = conn.execute(
            "SELECT id FROM links WHERE port_a IN (?, ?) OR port_b IN (?, ?)", (a, b, a, b)
        ).fetchone()
        if busy:
            report["skipped"]["links"] += 1
            continue
        if dry_run:
            report["created"]["links"] += 1
            continue
        conn.execute(
            "INSERT INTO links (port_a, port_b, cable, length_m) VALUES (?, ?, ?, ?)",
            (a, b, link.get("cable") or "", float(link.get("length_m") or 0)),
        )
        report["created"]["links"] += 1

    if not dry_run:
        conn.commit()
    else:
        conn.rollback()
    return report
