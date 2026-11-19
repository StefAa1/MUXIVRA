"""Versioned JSON backup/import for the documentation data (never credentials)."""
from __future__ import annotations

import sqlite3
import time

FORMAT = "muxivra.inventory/1"
# Files exported before the module rename still import: accept the old label,
# always write the current one.
def _accepted_format(value) -> bool:
    """Current label, plus any earlier muxivra/*/1 export label (import only)."""
    text = str(value or "")
    return text == FORMAT or (text.startswith("muxivra.") and text.endswith("/1"))


def export_payload(conn: sqlite3.Connection, customer_id: int | None = None) -> dict:
    """Everything the customer owns, and nothing else (ids are local to the export)."""
    scope = ""
    params: tuple = ()
    if customer_id:
        scope = " WHERE s.customer_id = ?"
        params = (int(customer_id),)

    sites = [dict(r) for r in conn.execute(
        "SELECT s.id, s.name, s.note FROM sites s" + scope + " ORDER BY s.id", params
    )]
    site_ids = [s["id"] for s in sites]
    placeholders = ",".join("?" * len(site_ids)) or "NULL"

    racks = [dict(r) for r in conn.execute(
        f"SELECT id, site_id, name, units FROM racks WHERE site_id IN ({placeholders}) ORDER BY id",
        site_ids,
    )]
    rack_ids = [r["id"] for r in racks]
    rack_ph = ",".join("?" * len(rack_ids)) or "NULL"

    devices = [dict(r) for r in conn.execute(
        "SELECT id, rack_id, name, kind, units, position, note, host_id, vendor, model, mgmt_ip"
        f" FROM devices WHERE rack_id IN ({rack_ph}) ORDER BY id",
        rack_ids,
    )]
    device_ids = [d["id"] for d in devices]
    device_ph = ",".join("?" * len(device_ids)) or "NULL"

    ports = [dict(r) for r in conn.execute(
        f"SELECT id, device_id, name, kind FROM ports WHERE device_id IN ({device_ph}) ORDER BY id",
        device_ids,
    )]
    port_ids = [p["id"] for p in ports]
    port_ph = ",".join("?" * len(port_ids)) or "NULL"

    links = [dict(r) for r in conn.execute(
        "SELECT id, port_a, port_b, cable, length_m FROM links"
        f" WHERE port_a IN ({port_ph}) AND port_b IN ({port_ph}) ORDER BY id",
        port_ids + port_ids,
    )]

    profiles = [dict(r) for r in conn.execute(
        """SELECT dp.device_id, h.uuid AS host_uuid, h.label AS host_label,
                  CASE WHEN d.host_id = h.id THEN 1 ELSE 0 END AS is_primary
             FROM device_profiles dp
             JOIN hosts h ON h.id = dp.host_id
             JOIN devices d ON d.id = dp.device_id
            UNION
           SELECT d.id AS device_id, h.uuid AS host_uuid, h.label AS host_label, 1 AS is_primary
             FROM devices d JOIN hosts h ON h.id = d.host_id
            ORDER BY device_id, is_primary DESC"""
    )]
    profiles = [p for p in profiles if p["device_id"] in set(device_ids)]

    return {
        "format": FORMAT,
        "customer_id": customer_id,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sites": sites,
        "racks": racks,
        "devices": devices,
        "ports": ports,
        "links": links,
        # identity travels as UUID: labels are not unique (two customers may both
        # have a host called "Router"), so they can never be the mapping key
        "profiles": profiles,
        "scope": "documentation data of ONE customer: sites, racks, devices, ports, links, "
                 "profile links; no credentials, no vault material, no diagram layout",
    }


class ImportError_(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def validate(payload: dict) -> dict:
    if not isinstance(payload, dict) or not _accepted_format(payload.get("format")):
        raise ImportError_("bad_format", f"Expected format {FORMAT}.")
    for key in ("sites", "racks", "devices", "ports", "links"):
        if not isinstance(payload.get(key), list):
            raise ImportError_("bad_format", f'Missing "{key}" list.')
    counters = {k: len(payload.get(k) or []) for k in ("sites", "racks", "devices", "ports", "links")}
    if counters["links"] and not counters["ports"]:
        raise ImportError_("bad_format", "There are links without ports.")
    return counters


def _site_key(conn, name: str, customer_id: int | None = None) -> int:
    row = conn.execute(
        "SELECT id FROM sites WHERE name = ? AND (customer_id IS ? OR ? IS NULL)",
        (name, customer_id, customer_id),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sites (name, note, customer_id) VALUES (?, '', ?)", (name, customer_id)
    )
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


def _num(value, field: str, row_id, default=None):
    """Coerce a JSON number, raising a clean 422 instead of a 500."""
    if value in (None, ""):
        if default is None:
            raise ImportError_("bad_number", f'The "{field}" is missing in row #{row_id}.')
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ImportError_("bad_number", f'The "{field}" is not a number in row #{row_id}.')
    if number != number or number in (float("inf"), float("-inf")):
        raise ImportError_("bad_number", f'The "{field}" is not a valid number (#{row_id}).')
    return number
PORT_KINDS = {"rj45", "sfp", "fiber", "power", "other"}


def _validate_rows(payload: dict) -> None:
    for collection in ("sites", "racks", "devices", "ports", "links", "profiles"):
        rows = payload.get(collection)
        if rows is None:
            continue
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ImportError_("bad_format", f'The "{collection}" must be a list of objects.')
    for collection in ("sites", "racks", "devices", "ports", "links"):
        ids = [row.get("id") for row in payload[collection]]
        if any(not isinstance(i, int) for i in ids):
            raise ImportError_("bad_format", f'Every entry in "{collection}" needs an integer id.')
        if len(set(ids)) != len(ids):
            raise ImportError_("duplicate_id", f'Duplicate ids in "{collection}".')
    rack_ids = {row["id"] for row in payload["racks"]}
    # geometry is validated against the MERGED state: an existing rack may be
    # smaller than the payload claims, and it may already be occupied
    rack_units: dict = {}
    for rack in payload["racks"]:
        units = int(_num(rack.get("units"), "units", rack["id"], 42))
        if not 1 <= units <= 60:
            raise ImportError_("bad_geometry", f"Rack #{rack['id']} has an invalid height.")
        rack_units[rack["id"]] = units

    for device in payload["devices"]:
        if device.get("rack_id") not in rack_ids:
            raise ImportError_("dangling_ref", f"Device #{device['id']} points to an unknown rack.")
        if (device.get("kind") or "switch") not in KINDS:
            raise ImportError_("bad_device_kind", f"Unknown device kind: {device.get('kind')}")
        if _num(device.get("position"), "position", device["id"], 1) < 1 or _num(
            device.get("units"), "units", device["id"], 1
        ) < 1:
            raise ImportError_("bad_geometry", f"Invalid position for device #{device['id']}.")

    port_ids = {row["id"] for row in payload["ports"]}
    device_ids = {row["id"] for row in payload["devices"]}
    for port in payload["ports"]:
        if port.get("device_id") not in device_ids:
            raise ImportError_("dangling_ref", f"Port #{port['id']} points to an unknown device.")
        if (port.get("kind") or "rj45") not in PORT_KINDS:
            raise ImportError_("bad_port_kind", f"Unknown port kind: {port.get('kind')}")
    for link in payload["links"]:
        if link.get("port_a") not in port_ids or link.get("port_b") not in port_ids:
            raise ImportError_("dangling_ref", f"Link #{link['id']} points to an unknown port.")
        if link.get("port_a") == link.get("port_b"):
            raise ImportError_("self_link", f"Link #{link['id']} connects a port to itself.")
        try:
            length = _num(link.get("length_m"), "length_m", link["id"], 0)
        except ImportError_ as exc:
            raise ImportError_("bad_length", exc.message)
        if not 0 <= length <= 10000:
            raise ImportError_("bad_length", f"Invalid length on link #{link['id']}.")


def import_payload(conn: sqlite3.Connection, payload: dict, dry_run: bool = False,
                    customer_id: int | None = None) -> dict:
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
            raise ImportError_("bad_format", "The site needs a name.")
        row = conn.execute(
            "SELECT id FROM sites WHERE name = ? AND (customer_id IS ? OR ? IS NULL)",
            (name, customer_id, customer_id),
        ).fetchone()
        if row:
            site_map[site["id"]] = row["id"]
            report["skipped"]["sites"] += 1
            report["conflicts"].append({"kind": "site", "name": name, "action": "kept-existing"})
        else:
            cur = conn.execute(
                "INSERT INTO sites (name, note, customer_id) VALUES (?, ?, ?)",
                (name, site.get("note") or "", customer_id),
            )
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

    occupancy: dict[int, list[tuple[int, int, str]]] = {}

    def _occupied(rack_id: int):
        if rack_id not in occupancy:
            occupancy[rack_id] = [
                (int(r["position"]), int(r["position"]) + int(r["units"]) - 1, r["name"])
                for r in conn.execute(
                    "SELECT position, units, name FROM devices WHERE rack_id = ?", (rack_id,)
                )
            ]
        return occupancy[rack_id]

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
            # merged-state geometry: real rack height + already placed devices
            rack_row = conn.execute("SELECT units FROM racks WHERE id = ?", (new_rack,)).fetchone()
            actual_units = int(rack_row["units"])
            top = int(device.get("position") or 1) + int(device.get("units") or 1) - 1
            if top > actual_units:
                raise ImportError_(
                    "device_outside_rack",
                    f'Device "{name}" extends beyond the rack ({actual_units}U).',
                )
            for start_u, end_u, owner in _occupied(new_rack):
                if int(device.get("position") or 1) <= end_u and start_u <= top:
                    raise ImportError_(
                        "position_taken", f'Position of "{name}" is already taken by "{owner}".'
                    )
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
            _occupied(new_rack).append((int(device.get("position") or 1), top, name))
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

    # profile relations: match by stable UUID only. An unknown UUID is reported
    # as unresolved — never silently bound to another host that shares a label.
    report["unresolved_profiles"] = []
    seen_pairs: set[tuple[int, int]] = set()
    for profile in payload.get("profiles") or []:
        device_new = device_map.get(profile.get("device_id"))
        host_uuid = str(profile.get("host_uuid") or "").strip()
        if not device_new or not host_uuid:
            report["skipped"]["profiles"] += 1
            report["unresolved_profiles"].append(
                {"device_id": profile.get("device_id"), "host_label": profile.get("host_label"),
                 "reason": "missing_uuid"}
            )
            continue
        # the UUID must resolve inside THIS customer: a foreign profile is never
        # silently bound, it stays unresolved and the operator decides
        host = conn.execute(
            "SELECT id, customer_id FROM hosts WHERE uuid = ?", (host_uuid,)
        ).fetchone()
        if not host:
            report["skipped"]["profiles"] += 1
            report["unresolved_profiles"].append(
                {"device_id": profile.get("device_id"), "host_label": profile.get("host_label"),
                 "reason": "host_not_found"}
            )
            continue
        if customer_id is not None and host["customer_id"] != customer_id:
            report["skipped"]["profiles"] += 1
            report["unresolved_profiles"].append(
                {"device_id": profile.get("device_id"), "host_label": profile.get("host_label"),
                 "reason": "foreign_customer"}
            )
            continue
        if (device_new, host["id"]) in seen_pairs:
            continue        # primary link and device_profiles row describe the same pair
        seen_pairs.add((device_new, host["id"]))
        if int(profile.get("is_primary") or 0) == 1:
            # restore the primary profile the device panel shows in its dropdown
            conn.execute("UPDATE devices SET host_id = ? WHERE id = ?", (host["id"], device_new))
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
