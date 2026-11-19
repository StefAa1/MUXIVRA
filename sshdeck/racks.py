"""Rack & cabling documentation (INVENTORY-style): sites, racks, devices, ports, links.

A port may be documented in at most one link — that is what makes duplicate
port assignments impossible to save silently.
"""
from __future__ import annotations

import csv
import time
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import json
import sqlite3

from . import customers, db
from .api import guard

router = APIRouter(prefix="/api/inventory", dependencies=[Depends(guard)])

DeviceKind = Literal["switch", "router", "firewall", "ap", "patch-panel", "server", "pdu", "other"]
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
    origin: Literal["documented", "planned"] = "documented"
    model_key: str = Field(default="", max_length=60)


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    origin: Literal["documented", "planned"] | None = None
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


def _cid(conn, request) -> int:
    return customers.request_customer(conn, request)


def _ensure_same(conn, customer_id, *pairs) -> None:
    try:
        customers.assert_same_customer(conn, customer_id, *pairs)
    except customers.CustomerError as exc:
        raise HTTPException(status_code=409 if exc.code == "cross_customer" else 404, detail=exc.code)


def _conn():
    return db.db()


def _one(conn, sql: str, params=()):
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return row


# ------------------------------------------------------------------- sites
@router.get("/tree")
def tree(request: Request):
    with _conn() as conn:
        cid = _cid(conn, request)
        sites = [dict(r) for r in conn.execute(
            "SELECT * FROM sites WHERE customer_id = ? ORDER BY name", (cid,))]
        racks = [dict(r) for r in conn.execute("SELECT * FROM racks ORDER BY name")]
        devices = [dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY position DESC")]
        links = conn.execute(
            """SELECT COUNT(*) AS n FROM links l
                 JOIN ports p ON p.id = l.port_a
                 JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id
                 JOIN sites st ON st.id = r.site_id
                WHERE st.customer_id = ?""",
            (cid,),
        ).fetchone()["n"]
    for rack in racks:
        rack["devices"] = [d for d in devices if d["rack_id"] == rack["id"]]
    for site in sites:
        site["racks"] = [r for r in racks if r["site_id"] == site["id"]]
    return {"sites": sites, "links": links}


@router.post("/sites")
def create_site(payload: SiteIn, request: Request):
    with _conn() as conn:
        cid = _cid(conn, request)
        cur = conn.execute(
            "INSERT INTO sites (name, note, customer_id) VALUES (?, ?, ?)",
            (payload.name.strip(), payload.note, cid),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM sites WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.patch("/sites/{site_id}")
def update_site(site_id: int, payload: dict, request: Request):
    with _conn() as conn:
        _one(conn, "SELECT id FROM sites WHERE id = ?", (site_id,))
        _ensure_same(conn, _cid(conn, request), ("site", site_id))
        fields, values = [], []
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise HTTPException(status_code=422, detail="empty_name")
            fields.append("name = ?")
            values.append(name[:80])
        if "note" in payload:
            fields.append("note = ?")
            values.append(str(payload["note"])[:400])
        if "customer_id" in payload:
            # explicit transfer between customers of the same operator
            try:
                customers.resolve(conn, int(payload["customer_id"]))
            except customers.CustomerError as exc:
                raise HTTPException(status_code=422, detail=exc.code) from exc
            fields.append("customer_id = ?")
            values.append(int(payload["customer_id"]))
        if not fields:
            return dict(_one(conn, "SELECT * FROM sites WHERE id = ?", (site_id,)))
        values.append(site_id)
        conn.execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        return dict(conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone())


@router.delete("/sites/{site_id}")
def delete_site(site_id: int, request: Request):
    with _conn() as conn:
        _one(conn, "SELECT id FROM sites WHERE id = ?", (site_id,))
        _ensure_same(conn, _cid(conn, request), ("site", site_id))
        rack_ids = [r["id"] for r in conn.execute("SELECT id FROM racks WHERE site_id = ?", (site_id,))]
        for rack_id in rack_ids:
            _delete_rack(conn, rack_id)
        conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
    return {"ok": True}


@router.post("/racks")
def create_rack(payload: RackIn, request: Request):
    with _conn() as conn:
        _one(conn, "SELECT id FROM sites WHERE id = ?", (payload.site_id,))
        _ensure_same(conn, _cid(conn, request), ("site", payload.site_id))
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
def create_device(payload: DeviceIn, request: Request):
    with _conn() as conn:
        rack = _one(conn, "SELECT units FROM racks WHERE id = ?", (payload.rack_id,))
        _ensure_same(conn, _cid(conn, request), ("rack", payload.rack_id))
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
            _ensure_same(conn, _cid(conn, request), ("host", payload.host_id))
        cur = conn.execute(
            """INSERT INTO devices (rack_id, name, kind, units, position, note, host_id, vendor, model,
                                  mgmt_ip, origin)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.rack_id, payload.name.strip(), payload.kind, payload.units, payload.position,
                payload.note, payload.host_id, payload.vendor.strip(), payload.model.strip(),
                payload.mgmt_ip.strip(), payload.origin,
            ),
        )
        device_id = cur.lastrowid
        if payload.model_key:
            from . import catalog as _catalog

            try:
                model = _catalog.get(payload.model_key)
            except _catalog.CatalogError as exc:
                raise HTTPException(status_code=422, detail=exc.code) from exc
            conn.execute(
                "UPDATE devices SET vendor = ?, model = ?, origin = ? WHERE id = ?",
                (model.vendor, model.name, payload.origin, device_id),
            )
            conn.commit()
            _catalog.create_ports(conn, device_id, payload.model_key)
        return dict(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


@router.delete("/devices/{device_id}")
def delete_device(device_id: int, request: Request):
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id))
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
def list_links(request: Request):
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
        cid = _cid(conn, request)
        rack_ids = {r["id"] for r in conn.execute(
            "SELECT r.id FROM racks r JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?", (cid,)
        )}
    return [dict(r) for r in rows if r["rack_a_id"] in rack_ids and r["rack_b_id"] in rack_ids]


@router.post("/links")
def create_link(payload: LinkIn, request: Request):
    if payload.port_a == payload.port_b:
        raise HTTPException(status_code=422, detail="same_port")
    with _conn() as conn:
        cid = _cid(conn, request)
        # a cable may not join two customers, and both ends must be ours
        try:
            customers.assert_same_customer(conn, cid, ("port", payload.port_a), ("port", payload.port_b))
        except customers.CustomerError as exc:
            raise HTTPException(status_code=422 if exc.code == "cross_customer" else 404,
                                detail=f"{exc.code}: {exc.message}")
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


@router.get("/site/{site_id}/dependents")
def site_dependents(site_id: int, request: Request):
    """What a site delete would remove — shown before confirming, never guessed."""
    with _conn() as conn:
        _ensure_same(conn, _cid(conn, request), ("site", site_id))
        racks = conn.execute("SELECT COUNT(*) AS n FROM racks WHERE site_id = ?", (site_id,)).fetchone()["n"]
        devices = conn.execute(
            "SELECT COUNT(*) AS n FROM devices WHERE rack_id IN (SELECT id FROM racks WHERE site_id = ?)",
            (site_id,)).fetchone()["n"]
        ports = conn.execute(
            """SELECT COUNT(*) AS n FROM ports WHERE device_id IN (
                 SELECT d.id FROM devices d JOIN racks r ON r.id = d.rack_id WHERE r.site_id = ?)""",
            (site_id,)).fetchone()["n"]
        links = conn.execute(
            """SELECT COUNT(*) AS n FROM links WHERE port_a IN (
                 SELECT p.id FROM ports p JOIN devices d ON d.id = p.device_id
                  JOIN racks r ON r.id = d.rack_id WHERE r.site_id = ?)""",
            (site_id,)).fetchone()["n"]
    return {"site_id": site_id, "racks": racks, "devices": devices, "ports": ports, "links": links}


@router.get("/rack/{rack_id}")
def rack_layout(rack_id: int, request: Request):
    """Everything the visual rack view needs: devices in U order with their ports."""
    with _conn() as conn:
        _ensure_same(conn, _cid(conn, request), ("rack", rack_id))
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
            device["ports"] = [
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
    return {"rack": dict(rack), "site": site["name"], "devices": devices}


@router.get("/ports")
def search_ports(request: Request, q: str = "", limit: int = 200, offset: int = 0):
    """Port lookup inside the active customer — feeds cross-rack Quick Connect."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    like = f"%{q.strip()}%"
    with _conn() as conn:
        cid = _cid(conn, request)
        rows = conn.execute(
            """SELECT p.id, p.name, p.kind, d.id AS device_id, d.name AS device,
                      r.id AS rack_id, r.name AS rack, s.name AS site,
                      (SELECT COUNT(*) FROM links l WHERE l.port_a = p.id OR l.port_b = p.id) AS linked
                 FROM ports p
                 JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id
                 JOIN sites s ON s.id = r.site_id
                WHERE s.customer_id = ?
                  AND (? = '%%' OR d.name LIKE ? OR p.name LIKE ? OR r.name LIKE ? OR s.name LIKE ?)
                ORDER BY s.name, r.name, d.name, p.id
                LIMIT ? OFFSET ?""",
            (cid, like, like, like, like, like, limit, offset),
        ).fetchall()
        total = conn.execute(
            """SELECT COUNT(*) AS n FROM ports p JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id JOIN sites s ON s.id = r.site_id
                WHERE s.customer_id = ?
                  AND (? = '%%' OR d.name LIKE ? OR p.name LIKE ? OR r.name LIKE ? OR s.name LIKE ?)""",
            (cid, like, like, like, like, like),
        ).fetchone()["n"]
    return {"ports": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/links.csv", response_class=PlainTextResponse)
def export_links(request: Request):
    with _conn() as conn:
        cid = _cid(conn, request)
        rack_ids = {r["id"] for r in conn.execute(
            "SELECT r.id FROM racks r JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?", (cid,)
        )}
    rows = [r for r in list_links(request) if r.get("rack_a_id") in rack_ids and r.get("rack_b_id") in rack_ids]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "device_a", "port_a", "device_b", "port_b", "rack_a", "rack_b", "cable", "length_m"])
    for row in rows:
        writer.writerow([
            row["id"], row["device_a"], row["port_a_name"], row["device_b"],
            row["port_b_name"], row["rack_a"], row["rack_b"], row["cable"], row["length_m"],
        ])
    return PlainTextResponse(buffer.getvalue(), headers={"content-disposition": 'attachment; filename="links.csv"'})


@router.get("/links/page")
def page_links(request: Request, q: str = "", limit: int = 100, offset: int = 0):
    """Paged cable table for search/filter UI (never silently hides rows)."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    like = f"%{q.strip()}%"
    rows = list_links(request)
    if q.strip():
        rows = [r for r in rows if like.strip("%").lower() in " ".join(
            str(v) for v in (r["device_a"], r["port_a_name"], r["device_b"], r["port_b_name"], r["cable"])
        ).lower()]
    return {"links": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}


# ------------------------------------------------------- device → connection
def _winbox_module():
    try:
        from . import winbox

        return winbox
    except Exception:  # module not installed yet / unsupported platform
        return None


@router.get("/devices/{device_id}/connections")
def device_connections(device_id: int, request: Request):
    """The device's registered SSH/Telnet/COM profiles plus WinBox availability."""
    with _conn() as conn:
        device = _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id))
        # explicit relations only: a shared private IP must never imply a profile
        rows = conn.execute(
            """SELECT h.id, h.label, h.hostname, h.port, h.protocol, h.username
                 FROM hosts h
                WHERE h.id = ? OR h.id IN (SELECT host_id FROM device_profiles WHERE device_id = ?)
                ORDER BY (h.id = ?) DESC, h.label""",
            (device["host_id"] or -1, device_id, device["host_id"] or -1),
        ).fetchall()
    module = _winbox_module()
    return {
        "device": dict(device),
        "hosts": [dict(r) for r in rows],
        "winbox": module.describe() if module else {"available": False, "reason": "module_missing"},
    }


@router.post("/devices/{device_id}/winbox")
def launch_winbox(device_id: int, request: Request, payload: dict | None = None):
    """Open the official WinBox client in its own window for this device."""
    payload = payload or {}
    with _conn() as conn:
        device = _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id))
    module = _winbox_module()
    if module is None:
        raise HTTPException(status_code=409, detail="winbox:module_missing")
    address = str(payload.get("address") or device["mgmt_ip"] or "").strip()
    if not address:
        raise HTTPException(status_code=422, detail="winbox:address_required")
    try:
        result = module.launch(address, username=payload.get("username"))
    except Exception as exc:  # WinBoxUnavailable and friends
        code = getattr(exc, "code", "winbox_unavailable")
        message = getattr(exc, "message", str(exc))
        raise HTTPException(status_code=409, detail=f"{code}: {message}")
    db.log_event(None, "winbox_launched", f"{device['name']} {address}")
    return {"ok": True, **result}


# --------------------------------------------------------- backup / import
@router.get("/audit")
def audit(request: Request):
    """Documentation problems worth fixing, inside the active customer."""
    issues = []
    with _conn() as conn:
        cid = _cid(conn, request)
        racks = [dict(r) for r in conn.execute(
            "SELECT r.* FROM racks r JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?", (cid,)
        )]
        for rack in racks:
            devices = [dict(r) for r in conn.execute(
                "SELECT * FROM devices WHERE rack_id = ? ORDER BY position", (rack["id"],))]
            occupied: list[tuple[int, str]] = []
            for device in devices:
                top = device["position"] + device["units"] - 1
                if top > rack["units"]:
                    issues.append({"kind": "outside_rack", "rack": rack["name"], "device": device["name"],
                                   "detail": f"U{device['position']}–U{top} > {rack['units']}U"})
                for unit in range(device["position"], top + 1):
                    for owner_unit, owner in occupied:
                        if owner_unit == unit:
                            issues.append({"kind": "overlap", "rack": rack["name"], "device": device["name"],
                                           "detail": f"U{unit} shared with {owner}"})
                    occupied.append((unit, device["name"]))
        orphans = conn.execute(
            """SELECT d.name AS device, r.name AS rack, COUNT(p.id) AS ports
                 FROM devices d JOIN racks r ON r.id = d.rack_id
                 JOIN sites s ON s.id = r.site_id
                 LEFT JOIN ports p ON p.device_id = d.id AND p.id NOT IN (
                    SELECT port_a FROM links UNION SELECT port_b FROM links)
                WHERE s.customer_id = ?
                GROUP BY d.id HAVING ports > 0 ORDER BY ports DESC LIMIT 50""",
            (cid,),
        ).fetchall()
        for row in orphans:
            issues.append({"kind": "unlinked_ports", "rack": row["rack"], "device": row["device"],
                           "detail": f"{row['ports']} ports with no link"})
        total_ports = conn.execute(
            """SELECT COUNT(*) AS n FROM ports p JOIN devices d ON d.id = p.device_id
                 JOIN racks r ON r.id = d.rack_id JOIN sites s ON s.id = r.site_id
                WHERE s.customer_id = ?""", (cid,)).fetchone()["n"]
        total_links = conn.execute(
            """SELECT COUNT(*) AS n FROM links l JOIN ports p ON p.id = l.port_a
                 JOIN devices d ON d.id = p.device_id JOIN racks r ON r.id = d.rack_id
                 JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?""", (cid,)).fetchone()["n"]
    return {
        "issues": issues,
        "summary": {"racks": len(racks), "ports": total_ports, "links": total_links,
                    "issues": len(issues)},
    }


@router.get("/export")
def export_inventory(request: Request):
    """Versioned JSON backup of THIS customer's documentation (no secrets)."""
    from . import inventory_io

    with _conn() as conn:
        payload = inventory_io.export_payload(conn, _cid(conn, request))
    return payload


@router.post("/import")
def import_inventory(payload: dict, request: Request):
    """Restore a backup. dry_run=true shows what would be created, changing nothing."""
    from . import inventory_io

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    dry_run = bool(payload.get("dry_run"))
    with _conn() as conn:
        try:
            report = inventory_io.import_payload(
                conn, data, dry_run=dry_run, customer_id=_cid(conn, request)
            )
        except inventory_io.ImportError_ as exc:
            raise HTTPException(status_code=422, detail=f"{exc.code}: {exc.message}")
    if not dry_run:
        db.log_event(None, "inventory_import", json.dumps(report["created"]))
    return report


@router.post("/devices/{device_id}/profiles")
def add_device_profile(device_id: int, payload: dict, request: Request):
    """Attach another connection profile explicitly (device ⇄ host, same customer)."""
    host_id = int(payload.get("host_id") or 0)
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        _one(conn, "SELECT id FROM hosts WHERE id = ?", (host_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id), ("host", host_id))
        conn.execute(
            "INSERT OR IGNORE INTO device_profiles (device_id, host_id) VALUES (?, ?)", (device_id, host_id)
        )
        conn.commit()
    return {"ok": True, "host_id": host_id}


@router.delete("/devices/{device_id}/profiles/{host_id}")
def remove_device_profile(device_id: int, host_id: int):
    with _conn() as conn:
        conn.execute(
            "DELETE FROM device_profiles WHERE device_id = ? AND host_id = ?", (device_id, host_id)
        )
        conn.execute(
            "UPDATE devices SET host_id = NULL WHERE id = ? AND host_id = ?", (device_id, host_id)
        )
        conn.commit()
    return {"ok": True}

@router.patch("/devices/{device_id}")
def update_device(device_id: int, payload: DevicePatch, request: Request):
    with _conn() as conn:
        device = _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id))
        rack = _one(conn, "SELECT units FROM racks WHERE id = ?", (device["rack_id"],))
        given = payload.model_dump(exclude_unset=True)
        merged = {**dict(device), **given}   # an explicit null (host_id) clears the field
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
            _ensure_same(conn, _cid(conn, request), ("host", merged["host_id"]))
        conn.execute(
            """UPDATE devices SET name = ?, kind = ?, units = ?, position = ?, note = ?,
                      host_id = ?, vendor = ?, model = ?, mgmt_ip = ?, origin = ?
                WHERE id = ?""",
            (merged["name"], merged["kind"], merged["units"], merged["position"], merged["note"],
             merged.get("host_id"), merged.get("vendor") or "", merged.get("model") or "",
             merged.get("mgmt_ip") or "", merged.get("origin") or "documented", device_id),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone())


@router.get("/devices/{device_id}/dependents")
def device_dependents(device_id: int, request: Request):
    """What disappears if this device is deleted — shown before confirming."""
    with _conn() as conn:
        _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
        _ensure_same(conn, _cid(conn, request), ("device", device_id))
        ports = conn.execute("SELECT COUNT(*) AS n FROM ports WHERE device_id = ?", (device_id,)).fetchone()["n"]
        links = conn.execute(
            """SELECT COUNT(*) AS n FROM links
                WHERE port_a IN (SELECT id FROM ports WHERE device_id = ?)
                   OR port_b IN (SELECT id FROM ports WHERE device_id = ?)""",
            (device_id, device_id),
        ).fetchone()["n"]
    return {"ports": ports, "links": links}


@router.patch("/links/{link_id}")
def update_link(link_id: int, payload: dict, request: Request):
    """Relabel or re-route a cable (both endpoints must stay free and ours)."""
    with _conn() as conn:
        link = _one(conn, "SELECT * FROM links WHERE id = ?", (link_id,))
        cid = _cid(conn, request)
        _ensure_same(conn, cid, ("port", link["port_a"]), ("port", link["port_b"]))
        cable = str(payload.get("cable", link["cable"]))[:80]
        length = float(payload.get("length_m", link["length_m"]) or 0)
        port_a = int(payload.get("port_a", link["port_a"]))
        port_b = int(payload.get("port_b", link["port_b"]))
        if port_a == port_b:
            raise HTTPException(status_code=422, detail="same_port")
        for port_id in (port_a, port_b):
            _one(conn, "SELECT id FROM ports WHERE id = ?", (port_id,))
        try:
            conn.execute("UPDATE links SET port_a = ?, port_b = ?, cable = ?, length_m = ? WHERE id = ?",
                         (port_a, port_b, cable, length, link_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"port_already_linked:{port_b}") from exc
        conn.commit()
        return dict(conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone())

# ------------------------------------------------------------------- catalog
@router.get("/catalog")
def catalog_index():
    """Vendors and models with their real port layouts (local data, no device contact)."""
    from . import catalog

    return {"vendors": catalog.vendors()}


@router.get("/catalog/{vendor}")
def catalog_vendor(vendor: str):
    from . import catalog

    return {"vendor": vendor, "models": catalog.models_for(vendor)}


# ---------------------------------------------------------- configuration workspace
class ConfigFetchIn(BaseModel):
    vendor: str = ""
    label: str = ""


class ConfigObserveIn(BaseModel):
    text: str
    vendor: str = ""


class ReconcileIn(BaseModel):
    apply: bool = False


def _device_or_404(conn, device_id: int, request: Request):
    device = _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))
    _ensure_same(conn, _cid(conn, request), ("device", device_id))
    return device


def _vendor_of(conn, device) -> str:
    if (device["vendor"] or "").strip():
        return device["vendor"].strip()
    if device["host_id"]:
        row = conn.execute("SELECT * FROM hosts WHERE id = ?", (device["host_id"],)).fetchone()
        if row:
            keys = row.keys()
            for column in ("vendor", "model"):
                if column in keys and row[column]:
                    return row[column]
    return ""


@router.post("/devices/{device_id}/config/fetch")
def config_fetch(device_id: int, payload: ConfigFetchIn, request: Request):
    """Read the running configuration with the vendor adapter (SSH only)."""
    with _conn() as conn:
        device = _device_or_404(conn, device_id, request)
        vendor = payload.vendor or _vendor_of(conn, device)
        host_id = device["host_id"]
        if not host_id:
            raise HTTPException(status_code=422, detail="device_without_profile")
        from . import workspace
        from .api import load_target

        target = load_target(conn, host_id)
        if target.protocol != "ssh":
            raise HTTPException(status_code=422, detail="config_requires_ssh")
        try:
            raw = workspace.fetch(target, vendor)
        except workspace.ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        except Exception as exc:                       # noqa: BLE001 - surfaced, not hidden
            raise HTTPException(status_code=502, detail=f"fetch_failed: {exc}") from exc
        info = workspace.store_version(conn, device_id, host_id, vendor, raw, "ssh", payload.label)
        parsed = workspace.parse(vendor, raw)
        workspace.upsert_observed(conn, device_id, host_id, vendor, parsed)
    return {**info, "vendor_used": vendor}


@router.post("/devices/{device_id}/config/paste")
def config_paste(device_id: int, payload: ConfigObserveIn, request: Request):
    """Store a configuration pasted by the operator (no device contact)."""
    from . import workspace

    with _conn() as conn:
        device = _device_or_404(conn, device_id, request)
        vendor = payload.vendor or _vendor_of(conn, device)
        info = workspace.store_version(conn, device_id, device["host_id"], vendor,
                                       payload.text, "paste", "pasted")
        workspace.upsert_observed(conn, device_id, device["host_id"], vendor,
                                  workspace.parse(vendor, payload.text))
    return {**info, "vendor_used": vendor}


@router.get("/devices/{device_id}/config/versions")
def config_versions(device_id: int, request: Request):
    from . import workspace

    with _conn() as conn:
        device = _device_or_404(conn, device_id, request)
        return {"device_id": device_id, "vendor": _vendor_of(conn, device),
                "versions": workspace.list_versions(conn, device_id)}


@router.get("/config/versions/{version_id}")
def config_version_text(version_id: int, request: Request):
    from . import workspace

    with _conn() as conn:
        row = _one(conn, "SELECT device_id FROM config_versions WHERE id = ?", (version_id,))
        _ensure_same(conn, _cid(conn, request), ("device", row["device_id"]))
        text = workspace.version_text(conn, version_id)
    return {"id": version_id, "text": text, "sha256": workspace.sha256_of(text)}


@router.get("/config/diff")
def config_diff(left: int, right: int, request: Request):
    from . import workspace

    with _conn() as conn:
        for version_id in (left, right):
            row = _one(conn, "SELECT device_id FROM config_versions WHERE id = ?", (version_id,))
            _ensure_same(conn, _cid(conn, request), ("device", row["device_id"]))
        return workspace.diff(conn, left, right)


@router.get("/devices/{device_id}/observed")
def device_observed(device_id: int, request: Request):
    from . import workspace

    with _conn() as conn:
        _device_or_404(conn, device_id, request)
        return {"device_id": device_id, "observed": workspace.observed(conn, device_id)}


@router.post("/devices/{device_id}/reconcile")
def device_reconcile(device_id: int, payload: ReconcileIn, request: Request):
    """Preview (default) or apply the supported logical elements into documentation."""
    from . import workspace

    with _conn() as conn:
        _device_or_404(conn, device_id, request)
        try:
            return workspace.reconcile(conn, device_id, apply_changes=payload.apply)
        except workspace.ConfigError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc


# ------------------------------------------------------------- planned topology
class PlannedLinkIn(BaseModel):
    from_device: int
    to_device: int
    from_port: str = ""
    to_port: str = ""
    label: str = ""
    note: str = ""


@router.get("/planned-links")
def list_planned_links(request: Request):
    with _conn() as conn:
        cid = _cid(conn, request)
        rows = conn.execute(
            """SELECT pl.* FROM planned_links pl
                 JOIN devices d ON d.id = pl.from_device
                 JOIN racks r ON r.id = d.rack_id
                 JOIN sites s ON s.id = r.site_id
                WHERE s.customer_id = ? ORDER BY pl.id""", (cid,),
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/planned-links", status_code=201)
def create_planned_link(payload: PlannedLinkIn, request: Request):
    with _conn() as conn:
        cid = _cid(conn, request)
        for device_id in (payload.from_device, payload.to_device):
            _one(conn, "SELECT id FROM devices WHERE id = ?", (device_id,))
            _ensure_same(conn, cid, ("device", device_id))
        if payload.from_device == payload.to_device:
            raise HTTPException(status_code=422, detail="same_device")
        cur = conn.execute(
            "INSERT INTO planned_links(from_device, to_device, from_port, to_port, label, note,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (payload.from_device, payload.to_device, payload.from_port[:40], payload.to_port[:40],
             payload.label[:80], payload.note[:200], time.time()),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM planned_links WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.delete("/planned-links/{link_id}")
def delete_planned_link(link_id: int, request: Request):
    with _conn() as conn:
        row = _one(conn, "SELECT * FROM planned_links WHERE id = ?", (link_id,))
        _ensure_same(conn, _cid(conn, request), ("device", row["from_device"]))
        conn.execute("DELETE FROM planned_links WHERE id = ?", (link_id,))
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------- device web management
class WebSurfaceIn(BaseModel):
    url: str = ""
    trust_sha256: str = ""


@router.post("/devices/{device_id}/web")
def open_device_web(device_id: int, payload: WebSurfaceIn, request: Request):
    """Open the device's own admin page in an isolated, separate surface."""
    from . import webview_surface

    with _conn() as conn:
        device = _device_or_404(conn, device_id, request)
        cid = _cid(conn, request)
        url = payload.url.strip() or webview_surface.default_url(device)
    try:
        webview_surface.validate_url(url)          # validated here, not only inside launch
        result = webview_surface.launch(url, cid, device["name"], payload.trust_sha256 or None)
    except webview_surface.SurfaceError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return {**result, "device_id": device_id, "device": device["name"]}


@router.get("/devices/{device_id}/web/config")
def device_web_config(device_id: int, request: Request, url: str = ""):
    """What the surface would use — no window is opened (inspectable)."""
    from . import webview_surface

    with _conn() as conn:
        device = _device_or_404(conn, device_id, request)
        cid = _cid(conn, request)
        target = url.strip() or webview_surface.default_url(device)
    try:
        return webview_surface.surface_config(target, cid, device["name"])
    except webview_surface.SurfaceError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
