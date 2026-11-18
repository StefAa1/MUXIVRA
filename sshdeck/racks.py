"""Rack & cabling documentation (NETDOC-style): sites, racks, devices, ports, links.

A port may be documented in at most one link — that is what makes duplicate
port assignments impossible to save silently.
"""
from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import sqlite3

from . import db
from .api import guard

router = APIRouter(prefix="/api/netdoc", dependencies=[Depends(guard)])

DeviceKind = Literal["switch", "router", "patch-panel", "server", "pdu", "other"]
PortKind = Literal["rj45", "sfp", "fiber", "power", "other"]


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=400)


class RackIn(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=80)
    units: int = Field(default=42, ge=1, le=60)


class DeviceIn(BaseModel):
    rack_id: int
    name: str = Field(min_length=1, max_length=80)
    kind: DeviceKind = "switch"
    units: int = Field(default=1, ge=1, le=48)
    position: int = Field(default=1, ge=1, le=60)
    note: str = Field(default="", max_length=400)
    host_id: int | None = None
    vendor: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=60)
    mgmt_ip: str = Field(default="", max_length=64)


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: DeviceKind | None = None
    units: int | None = Field(default=None, ge=1, le=48)
    position: int | None = Field(default=None, ge=1, le=60)
    note: str | None = Field(default=None, max_length=400)
    host_id: int | None = None
    vendor: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    mgmt_ip: str | None = Field(default=None, max_length=64)


class PortIn(BaseModel):
    device_id: int
    name: str = Field(min_length=1, max_length=40)
    kind: PortKind = "rj45"


class LinkIn(BaseModel):
    port_a: int
    port_b: int
    cable: str = Field(default="", max_length=80)
    length_m: float = Field(default=0, ge=0, le=10000)


def _conn():
    return db.db()


def _one(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return row


# ------------------------------------------------------------------- sites
@router.get("/tree")
def tree():
    with _conn() as conn:
        sites = [dict(r) for r in conn.execute("SELECT * FROM sites ORDER BY name")]
        racks = [dict(r) for r in conn.execute("SELECT * FROM racks ORDER BY name")]
        devices = [dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY position DESC")]
        links = conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]
    for rack in racks:
        rack["devices"] = [d for d in devices if d["rack_id"] == rack["id"]]
    for site in sites:
        site["racks"] = [r for r in racks if r["site_id"] == site["id"]]
    return {"sites": sites, "links": links}


@router.post("/sites")
def create_site(payload: SiteIn):
    with _conn() as conn:
        cur = conn.execute("INSERT INTO sites (name, note) VALUES (?, ?)", (payload.name.strip(), payload.note))
        conn.commit()
        return dict(conn.execute("SELECT * FROM sites WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/sites/{site_id}")
def delete_site(site_id: int):
    with _conn() as conn:
        _one(conn, "SELECT id FROM sites WHERE id = ?", (site_id,))
        rack_ids = [r["id"] for r in conn.execute("SELECT id FROM racks WHERE site_id = ?", (site_id,))]
        for rack_id in rack_ids:
            _delete_rack(conn, rack_id)
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    return {"ok": True}


@router.post("/racks")
def create_rack(payload: RackIn):
    with _conn() as conn:
        _one(conn, "SELECT id FROM sites WHERE id = ?", (payload.site_id,))
        cur = conn.execute(
            "INSERT INTO racks (site_id, name, units) VALUES (?, ?, ?)",
            (payload.site_id, payload.name.strip(), payload.units),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM racks WHERE id = ?", (cur.lastrowid,)).fetchone())


def _delete_rack(conn, rack_id: int) -> None:
    for device in conn.execute("SELECT id FROM devices WHERE rack_id = ?", (rack_id,)).fetchall():
        for port in conn.execute("SELECT id FROM ports WHERE device_id = ?", (device["id"],)).fetchall():
            conn.execute("DELETE FROM links WHERE port_a = ? OR port_b = ?", (port["id"], port["id"]))
            conn.execute("DELETE FROM ports WHERE id = ?", (port["id"],))
        conn.execute("DELETE FROM devices WHERE id = ?", (device["id"],))
    conn.execute("DELETE FROM racks WHERE id = ?", (rack_id,))


@router.delete("/racks/{rack_id}")
def delete_rack(rack_id: int):
    with _conn() as conn:
        _one(conn, "SELECT id FROM racks WHERE id = ?", (rack_id,))
        _delete_rack(conn, rack_id)
        conn.commit()
    return {"ok": True}


# ----------------------------------------------------------------- devices
@router.post("/devices")
def create_device(payload: DeviceIn):
    with _conn() as conn:
        rack = _one(conn, "SELECT units FROM racks WHERE id = ?", (payload.rack_id,))
        if payload.position + payload.units - 1 > rack["units"]:
            raise HTTPException(status_code=422, detail="device_outside_rack")
        clash = conn.execute(
            """SELECT name FROM devices WHERE rack_id = ? AND position < ? AND (position + units) > ?""",
            (payload.rack_id, payload.position + payload.units, payload.position),
        ).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail=f"position_taken:{clash['name']}")
        if payload.host_id is not None:
            _one(conn, "SELECT id FROM hosts WHERE id = ?", (payload.host_id,))
        cur = conn.execute(
            """INSERT INTO devices (rack_id, name, kind, units, position, note, host_id, vendor, model, mgmt_ip)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.rack_id, payload.name.strip(), payload.kind, payload.units, payload.position,
                payload.note, payload.host_id, payload.vendor.strip(), payload.model.strip(), payload.mgmt_ip.strip(),
            ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM devices WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/devices/{device_id}")
def delete_device(device_id: int):
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        for port in conn.execute("SELECT id FROM ports WHERE device_id = ?", (device_id,)).fetchall():
            conn.execute("DELETE FROM links WHERE port_a = ? OR port_b = ?", (port["id"], port["id"]))
        conn.execute("DELETE FROM ports WHERE device_id = ?", (device_id,))
        conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------- ports
@router.get("/devices/{device_id}/ports")
def list_ports(device_id: int):
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        rows = conn.execute(
            """SELECT p.*,
                      (SELECT l.id FROM links l WHERE l.port_a = p.id OR l.port_b = p.id) AS link_id
               FROM ports p WHERE p.device_id = ? ORDER BY p.name""",
            (device_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/ports")
def create_port(payload: PortIn):
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (payload.device_id,))
        exists = conn.execute(
            "SELECT id FROM ports WHERE device_id = ? AND name = ?", (payload.device_id, payload.name.strip())
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="port_exists")
        cur = conn.execute(
            "INSERT INTO ports (device_id, name, kind) VALUES (?, ?, ?)",
            (payload.device_id, payload.name.strip(), payload.kind),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM ports WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.post("/ports/bulk")
def bulk_ports(payload: dict):
    """Create eth1..ethN in one go: {"device_id":1,"prefix":"eth","count":24,"kind":"rj45"}."""
    device_id = int(payload.get("device_id") or 0)
    prefix = str(payload.get("prefix") or "eth")[:12]
    count = int(payload.get("count") or 0)
    kind = str(payload.get("kind") or "rj45")
    if not 1 <= count <= 96 or kind not in {"rj45", "sfp", "fiber", "power", "other"}:
        raise HTTPException(status_code=422, detail="bad_bulk_request")
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        created = []
        for index in range(1, count + 1):
            name = f"{prefix}{index}"
            if conn.execute("SELECT id FROM ports WHERE device_id = ? AND name = ?", (device_id, name)).fetchone():
                continue
            cur = conn.execute("INSERT INTO ports (device_id, name, kind) VALUES (?, ?, ?)", (device_id, name, kind))
            created.append(dict(conn.execute("SELECT * FROM ports WHERE id = ?", (cur.lastrowid,)).fetchone()))
        conn.commit()
    return {"created": created}


# ------------------------------------------------------------------- links
@router.get("/links")
def list_links():
    with _conn() as conn:
        rows = conn.execute(
            """SELECT l.id, l.cable, l.length_m, l.port_a, l.port_b,
                      pa.name AS port_a_name, pb.name AS port_b_name,
                      da.id AS device_a_id, db.id AS device_b_id,
                      da.name AS device_a, db.name AS device_b,
                      ra.name AS rack_a, rb.name AS rack_b, ra.id AS rack_a_id, rb.id AS rack_b_id
               FROM links l
               JOIN ports pa ON pa.id = l.port_a
               JOIN ports pb ON pb.id = l.port_b
               JOIN devices da ON da.id = pa.device_id
               JOIN devices db ON db.id = pb.device_id
               JOIN racks ra ON ra.id = da.rack_id
               JOIN racks rb ON rb.id = db.rack_id
               ORDER BY l.id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/links")
def create_link(payload: LinkIn):
    if payload.port_a == payload.port_b:
        raise HTTPException(status_code=422, detail="same_port")
    with _conn() as conn:
        for port_id in (payload.port_a, payload.port_b):
            port = _one(conn, "SELECT id FROM ports WHERE id = ?", (port_id,))
            busy = conn.execute(
                "SELECT id FROM links WHERE port_a = ? OR port_b = ?", (port["id"], port["id"])
            ).fetchone()
            if busy:
                raise HTTPException(status_code=409, detail=f"port_already_linked:{port_id}")
        try:
            cur = conn.execute(
                "INSERT INTO links (port_a, port_b, cable, length_m) VALUES (?, ?, ?, ?)",
                (payload.port_a, payload.port_b, payload.cable.strip(), payload.length_m),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"port_already_linked:{payload.port_a}") from exc
        conn.commit()
        return dict(conn.execute("SELECT * FROM links WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/links/{link_id}")
def delete_link(link_id: int):
    with _conn() as conn:
        _one(conn, "SELECT id FROM links WHERE id = ?", (link_id,))
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
        conn.commit()
    return {"ok": True}


@router.get("/links.csv", response_class=PlainTextResponse)
def export_links():
    rows = list_links()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "device_a", "port_a", "device_b", "port_b", "rack_a", "rack_b", "cable", "length_m"])
    for row in rows:
        writer.writerow([
            row["id"], row["device_a"], row["port_a_name"], row["device_b"],
            row["port_b_name"], row["rack_a"], row["rack_b"], row["cable"], row["length_m"],
        ])
    return PlainTextResponse(buffer.getvalue(), headers={"content-disposition": 'attachment; filename="links.csv"'})


# ------------------------------------------------------------------- views
@router.get("/rack/{rack_id}")
def rack_layout(rack_id: int):
    """Everything the visual rack view needs: devices in U order with their ports."""
    with _conn() as conn:
        rack = _one(conn, "SELECT * FROM racks WHERE id = ?", (rack_id,))
        site = _one(conn, "SELECT name FROM sites WHERE id = ?", (rack["site_id"],))
        devices = [
            dict(r)
            for r in conn.execute(
                """SELECT d.*, h.label AS host_label, h.hostname AS host_hostname
                     FROM devices d LEFT JOIN hosts h ON h.id = d.host_id
                    WHERE d.rack_id = ? ORDER BY d.position DESC""",
                (rack_id,),
            )
        ]
        for device in devices:
            ports = [
                dict(r)
                for r in conn.execute(
                    """SELECT p.id, p.name, p.kind,
                              l.id AS link_id,
                              (SELECT d2.name || ' · ' || p2.name
                                 FROM links l2
                                 JOIN ports p2 ON p2.id = CASE WHEN l2.port_a = p.id THEN l2.port_b ELSE l2.port_a END
                                 JOIN devices d2 ON d2.id = p2.device_id
                                WHERE l2.port_a = p.id OR l2.port_b = p.id) AS peer
                       FROM ports p
                       LEFT JOIN links l ON l.port_a = p.id OR l.port_b = p.id
                      WHERE p.device_id = ?
                      ORDER BY p.id""",
                    (device["id"],),
                )
            ]
            device["ports"] = ports
    return {"rack": dict(rack), "site": site["name"], "devices": devices}


@router.get("/audit")
def audit():
    """Documentation problems worth fixing: overlaps, orphans, duplicates."""
    issues = []
    with _conn() as conn:
        racks = [dict(r) for r in conn.execute("SELECT * FROM racks")]
        for rack in racks:
            devices = [dict(r) for r in conn.execute(
                "SELECT * FROM devices WHERE rack_id = ? ORDER BY position", (rack["id"],))]
            occupied: list[tuple[int, str]] = []
            for device in devices:
                top = device["position"] + device["units"] - 1
                if top > rack["units"]:
                    issues.append({
                        "kind": "outside_rack", "rack": rack["name"], "device": device["name"],
                        "detail": f"U{device['position']}–U{top} > {rack['units']}U",
                    })
                for unit in range(device["position"], top + 1):
                    for owner_unit, owner in occupied:
                        if owner_unit == unit:
                            issues.append({
                                "kind": "overlap", "rack": rack["name"], "device": device["name"],
                                "detail": f"U{unit} μοιράζεται με {owner}",
                            })
                    occupied.append((unit, device["name"]))
            names: dict[str, int] = {}
            for device in devices:
                names[device["name"]] = names.get(device["name"], 0) + 1
            for name, count in names.items():
                if count > 1:
                    issues.append({
                        "kind": "duplicate_device", "rack": rack["name"], "device": name,
                        "detail": f"{count} συσκευές με το ίδιο όνομα",
                    })
        orphans = conn.execute(
            """SELECT d.name AS device, r.name AS rack, COUNT(p.id) AS ports
                 FROM devices d JOIN racks r ON r.id = d.rack_id
                 LEFT JOIN ports p ON p.device_id = d.id AND p.id NOT IN (
                    SELECT port_a FROM links UNION SELECT port_b FROM links)
                GROUP BY d.id HAVING ports > 0 ORDER BY ports DESC LIMIT 50"""
        ).fetchall()
        for row in orphans:
            issues.append({
                "kind": "unlinked_ports", "rack": row["rack"], "device": row["device"],
                "detail": f"{row['ports']} ports χωρίς σύνδεση",
            })
        duplicates = conn.execute(
            """SELECT port_a AS port FROM links GROUP BY port_a HAVING COUNT(*) > 1
               UNION SELECT port_b FROM links GROUP BY port_b HAVING COUNT(*) > 1"""
        ).fetchall()
        for row in duplicates:
            issues.append({"kind": "duplicate_port", "rack": "", "device": "", "detail": f"port #{row['port']}"})
        total_ports = conn.execute("SELECT COUNT(*) AS n FROM ports").fetchone()["n"]
        total_links = conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]
    return {
        "issues": issues,
        "summary": {
            "racks": len(racks), "ports": total_ports, "links": total_links,
            "issues": len(issues), "unlinked": sum(i["detail"].count("ports") for i in issues if i["kind"] == "unlinked_ports"),
        },
    }


@router.patch("/devices/{device_id}")
def update_device(device_id: int, payload: DevicePatch):
    with _conn() as conn:
        device = _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))
        rack = _one(conn, "SELECT units FROM racks WHERE id = ?", (device["rack_id"],))
        merged = {**dict(device), **{k: v for k, v in payload.model_dump().items() if v is not None}}
        top = merged["position"] + merged["units"] - 1
        if top > rack["units"]:
            raise HTTPException(status_code=422, detail="device_outside_rack")
        clash = conn.execute(
            """SELECT name FROM devices
                WHERE rack_id = ? AND id <> ? AND position < ? AND (position + units) > ?""",
            (device["rack_id"], device_id, top + 1, merged["position"]),
        ).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail=f"position_taken:{clash['name']}")
        if merged.get("host_id") is not None:
            _one(conn, "SELECT id FROM hosts WHERE id = ?", (merged["host_id"],))
        conn.execute(
            """UPDATE devices SET name = ?, kind = ?, units = ?, position = ?, note = ?,
                      host_id = ?, vendor = ?, model = ?, mgmt_ip = ?
                WHERE id = ?""",
            (
                merged["name"], merged["kind"], merged["units"], merged["position"], merged["note"],
                merged.get("host_id"), merged.get("vendor") or "", merged.get("model") or "",
                merged.get("mgmt_ip") or "", device_id,
            ),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


@router.get("/devices/{device_id}/dependents")
def device_dependents(device_id: int):
    """What disappears if this device is deleted — shown before confirming."""
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        ports = conn.execute("SELECT COUNT(*) AS n FROM ports WHERE device_id = ?", (device_id,)).fetchone()["n"]
        links = conn.execute(
            """SELECT COUNT(*) AS n FROM links
                WHERE port_a IN (SELECT id FROM ports WHERE device_id = ?)
                   OR port_b IN (SELECT id FROM ports WHERE device_id = ?)""",
            (device_id, device_id),
        ).fetchone()["n"]
    return {"ports": ports, "links": links}


@router.get("/ports")
def search_ports(q: str = "", limit: int = 200, offset: int = 0):
    """Port lookup across every rack — feeds cross-rack Quick Connect."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    like = f"%{q.strip()}%"
    with _conn() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.kind, d.id AS device_id, d.name AS device,
                      r.id AS rack_id, r.name AS rack, s.name AS site,
                      (SELECT COUNT(*) FROM links l WHERE l.port_a = p.id OR l.port_b = p.id) AS linked
                 FROM ports p
                 JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id
                 JOIN sites s ON s.id = r.site_id
                WHERE (? = '%%' OR d.name LIKE ? OR p.name LIKE ? OR r.name LIKE ? OR s.name LIKE ?)
                ORDER BY s.name, r.name, d.name, p.id
                LIMIT ? OFFSET ?""",
            (like, like, like, like, like, limit, offset),
        ).fetchall()
        total = conn.execute(
            """SELECT COUNT(*) AS n FROM ports p JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id JOIN sites s ON s.id = r.site_id
                WHERE (? = '%%' OR d.name LIKE ? OR p.name LIKE ? OR r.name LIKE ? OR s.name LIKE ?)""",
            (like, like, like, like, like),
        ).fetchone()["n"]
    return {"ports": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.patch("/links/{link_id}")
def update_link(link_id: int, payload: dict):
    """Relabel or re-route a cable (both endpoints must stay free)."""
    with _conn() as conn:
        link = _one(conn, "SELECT * FROM links WHERE id = ?", (link_id,))
        cable = str(payload.get("cable", link["cable"]))[:80]
        length = float(payload.get("length_m", link["length_m"]) or 0)
        port_a = int(payload.get("port_a", link["port_a"]))
        port_b = int(payload.get("port_b", link["port_b"]))
        if port_a == port_b:
            raise HTTPException(status_code=422, detail="same_port")
        for port_id in (port_a, port_b):
            _one(conn, "SELECT id FROM ports WHERE id = ?", (port_id,))
        try:
            conn.execute(
                "UPDATE links SET port_a = ?, port_b = ?, cable = ?, length_m = ? WHERE id = ?",
                (port_a, port_b, cable, length, link_id),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"port_already_linked:{port_b}") from exc
        conn.commit()
        return dict(conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone())


@router.get("/links/page")
def page_links(q: str = "", limit: int = 100, offset: int = 0):
    """Paged cable table for search/filter UI (never silently hides rows)."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    like = f"%{q.strip()}%"
    rows = list_links()
    if q.strip():
        rows = [r for r in rows if like.strip("%").lower() in " ".join(
            str(v) for v in (r["device_a"], r["port_a_name"], r["device_b"], r["port_b_name"], r["cable"])
        ).lower()]
    return {"links": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}
