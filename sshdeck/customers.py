"""Customers: logical ownership for sites, hosts (profiles) and credentials.

This is single-operator separation (one local user, several customers), not
multi-user security isolation. Every query that can cross customers must pass
through this module instead of trusting the UI.
"""
from __future__ import annotations

import sqlite3
import time
import uuid

UNCLASSIFIED_NAME = "Μη ταξινομημένος πελάτης"


class CustomerError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def ensure_unclassified(conn: sqlite3.Connection) -> int:
    """The customer that adopts all pre-0.5 data. Created once, never archived."""
    row = conn.execute("SELECT id FROM customers WHERE name = ?", (UNCLASSIFIED_NAME,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO customers (uuid, name, code, color, notes, archived, created_at)"
        " VALUES (?, ?, 'default', '#7c9cff', '', 0, ?)",
        (uuid.uuid4().hex, UNCLASSIFIED_NAME, time.time()),
    )
    return cur.lastrowid


def backfill_ownership(conn: sqlite3.Connection) -> int:
    """Give every existing site/host/credential to the unclassified customer."""
    default_id = ensure_unclassified(conn)
    changed = 0
    for table in ("sites", "hosts", "credentials"):
        cur = conn.execute(
            f"UPDATE {table} SET customer_id = ? WHERE customer_id IS NULL", (default_id,)
        )
        changed += cur.rowcount or 0
    return changed


def list_customers(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM customers"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY archived, name COLLATE NOCASE"
    return [dict(r) for r in conn.execute(sql)]


def create_customer(conn: sqlite3.Connection, name: str, code: str = "", color: str = "#3ddc97",
                    notes: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise CustomerError("name_required", "Ο πελάτης χρειάζεται όνομα.")
    if conn.execute("SELECT id FROM customers WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
        raise CustomerError("duplicate_name", "Υπάρχει ήδη πελάτης με αυτό το όνομα.")
    cur = conn.execute(
        "INSERT INTO customers (uuid, name, code, color, notes, archived, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?)",
        (uuid.uuid4().hex, name, (code or "").strip()[:12], color, notes or "", time.time()),
    )
    return dict(conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_customer(conn: sqlite3.Connection, customer_id: int | None) -> dict | None:
    if not customer_id:
        return None
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


def resolve(conn: sqlite3.Connection, customer_id: int | None = None,
            customer_uuid: str | None = None) -> int:
    """Resolve the request's customer. Falls back to the unclassified one."""
    if customer_uuid:
        row = conn.execute("SELECT id FROM customers WHERE uuid = ?", (customer_uuid,)).fetchone()
        if not row:
            raise CustomerError("unknown_customer", "Άγνωστος πελάτης.")
        return row["id"]
    if customer_id:
        row = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not row:
            raise CustomerError("unknown_customer", "Άγνωστος πελάτης.")
        return row["id"]
    return ensure_unclassified(conn)


def request_customer(conn: sqlite3.Connection, request=None, explicit: int | None = None) -> int:
    """Customer for this request only — never a process-wide mutable setting."""
    if explicit:
        return resolve(conn, explicit)
    if request is not None:
        header = request.headers.get("x-customer-id") or request.headers.get("x-customer")
        query = request.query_params.get("customer") or request.query_params.get("customer_uuid")
        if header and str(header).isdigit():
            return resolve(conn, int(header))
        if query:
            if str(query).isdigit():
                return resolve(conn, int(query))
            return resolve(conn, customer_uuid=str(query))
    return ensure_unclassified(conn)


def site_owner(conn: sqlite3.Connection, site_id: int) -> int | None:
    row = conn.execute("SELECT customer_id FROM sites WHERE id = ?", (site_id,)).fetchone()
    return row["customer_id"] if row else None


def rack_owner(conn: sqlite3.Connection, rack_id: int) -> int | None:
    row = conn.execute(
        "SELECT s.customer_id FROM racks r JOIN sites s ON s.id = r.site_id WHERE r.id = ?", (rack_id,)
    ).fetchone()
    return row["customer_id"] if row else None


def device_owner(conn: sqlite3.Connection, device_id: int) -> int | None:
    row = conn.execute(
        "SELECT s.customer_id FROM devices d JOIN racks r ON r.id = d.rack_id"
        " JOIN sites s ON s.id = r.site_id WHERE d.id = ?",
        (device_id,),
    ).fetchone()
    return row["customer_id"] if row else None


def port_owner(conn: sqlite3.Connection, port_id: int) -> int | None:
    row = conn.execute(
        "SELECT s.customer_id FROM ports p JOIN devices d ON d.id = p.device_id"
        " JOIN racks r ON r.id = d.rack_id JOIN sites s ON s.id = r.site_id WHERE p.id = ?",
        (port_id,),
    ).fetchone()
    return row["customer_id"] if row else None


def assert_same_customer(conn: sqlite3.Connection, customer_id: int, *pairs) -> None:
    """pairs: (kind, id) where kind is site|rack|device|port|host."""
    lookups = {
        "site": site_owner,
        "rack": rack_owner,
        "device": device_owner,
        "port": port_owner,
        "host": lambda c, i: (c.execute("SELECT customer_id FROM hosts WHERE id = ?", (i,)).fetchone() or {})["customer_id"]
        if c.execute("SELECT 1 FROM hosts WHERE id = ?", (i,)).fetchone() else None,
    }
    for kind, object_id in pairs:
        owner = lookups[kind](conn, object_id)
        if owner is None:
            raise CustomerError("not_found", f"Άγνωστο {kind} #{object_id}.")
        if owner != customer_id:
            raise CustomerError(
                "cross_customer", f"Το {kind} #{object_id} ανήκει σε άλλον πελάτη."
            )


def summary(conn: sqlite3.Connection, customer_id: int) -> dict:
    def count(sql: str) -> int:
        return conn.execute(sql, (customer_id,)).fetchone()["n"]

    return {
        "customer_id": customer_id,
        "sites": count("SELECT COUNT(*) AS n FROM sites WHERE customer_id = ?"),
        "hosts": count("SELECT COUNT(*) AS n FROM hosts WHERE customer_id = ?"),
        "credentials": count("SELECT COUNT(*) AS n FROM credentials WHERE customer_id = ?"),
        "racks": count(
            "SELECT COUNT(*) AS n FROM racks r JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?"
        ),
        "devices": count(
            "SELECT COUNT(*) AS n FROM devices d JOIN racks r ON r.id = d.rack_id"
            " JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?"
        ),
        "links": count(
            "SELECT COUNT(*) AS n FROM links l JOIN ports p ON p.id = l.port_a"
            " JOIN devices d ON d.id = p.device_id JOIN racks r ON r.id = d.rack_id"
            " JOIN sites s ON s.id = r.site_id WHERE s.customer_id = ?"
        ),
    }
