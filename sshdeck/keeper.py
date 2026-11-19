"""Optional Keeper Secrets Manager (KSM) import.

KSM is a paid Keeper entitlement and is consumed with a per-device One-Time
Access Token. This module maps KSM records ("Login", "Server", "SSH Key")
onto MUXIVRA hosts + vault credentials.

Design constraints:
- read-only: nothing is ever written back to Keeper.
- fail closed: a missing SDK, token or unreadable record is reported, not guessed.
- the token is exchanged once and stored as a bound config file with mode 0600.

Live use needs `pip install keeper-secrets-manager-core` plus the user's token;
the mapping and merge logic below are unit-tested without any Keeper account.
"""
from __future__ import annotations

import uuid

import json
import os
import sqlite3
from dataclasses import dataclass, field

from . import db, security

HOST_FIELDS = ("host", "hostname", "host[hostname]", "ip", "address", "server")
PORT_FIELDS = ("port", "host[port]", "ssh_port")
LOGIN_FIELDS = ("login", "username", "user", "login[username]")
PASSWORD_FIELDS = ("password", "pass", "login[password]")
KEY_FIELDS = ("key", "private_key", "ssh_key", "private key", "key[private key]")
PASSPHRASE_FIELDS = ("passphrase", "key[passphrase]", "key_passphrase")
NOTES_FIELDS = ("notes", "note", "comment")


class KeeperError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class KeeperRecord:
    uid: str
    title: str
    kind: str  # "login" | "server" | "ssh_key"
    host: str = ""
    port: int = 22
    login: str = ""
    password: str = ""
    private_key: str = ""
    passphrase: str = ""
    notes: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.host or self.private_key) and bool(self.password or self.private_key)


def _flatten(raw_fields) -> dict[str, str]:
    """KSM exposes fields as a dict or as a list of {type, value} entries."""
    flat: dict[str, str] = {}
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            if isinstance(value, list):
                value = value[0] if value else ""
            flat[str(key).strip().lower()] = "" if value is None else str(value)
    elif isinstance(raw_fields, list):
        for entry in raw_fields:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("type") or entry.get("name") or "").strip().lower()
            value = entry.get("value")
            if isinstance(value, list):
                value = value[0] if value else ""
            if name:
                flat[name] = "" if value is None else str(value)
    return flat


def _pick(fields: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if fields.get(name):
            return fields[name].strip()
    return ""


def normalise_record(raw) -> KeeperRecord:
    """Accept the dict shape returned by the KSM SDK (`record.dict`)."""
    if not isinstance(raw, dict):
        raise KeeperError("bad_record", f"unexpected record type: {type(raw).__name__}")
    fields = _flatten(raw.get("fields") or {})
    custom = _flatten(raw.get("custom_fields") or {})
    merged = {**custom, **fields}

    rtype = str(raw.get("type") or raw.get("record_type") or "").strip().lower()
    title = str(raw.get("title") or raw.get("uid") or "keeper-record").strip()
    private_key = _pick(merged, KEY_FIELDS)
    password = _pick(merged, PASSWORD_FIELDS)
    port_raw = _pick(merged, PORT_FIELDS)
    try:
        port = int(port_raw) if port_raw else 22
    except ValueError:
        port = 22

    if private_key:
        kind = "ssh_key"
    elif "server" in rtype:
        kind = "server"
    elif port_raw or _pick(merged, ("host", "hostname")):
        kind = "server"
    else:
        kind = "login"

    return KeeperRecord(
        uid=str(raw.get("uid") or ""),
        title=title,
        kind=kind,
        host=_pick(merged, HOST_FIELDS),
        port=port,
        login=_pick(merged, LOGIN_FIELDS),
        password=password,
        private_key=private_key,
        passphrase=_pick(merged, PASSPHRASE_FIELDS),
        notes=str(raw.get("notes") or "").strip() or _pick(merged, NOTES_FIELDS),
    )


def fetch_records(token: str, config_path: str) -> list[dict]:
    """Bind the one-time token and return raw records. Requires the KSM SDK."""
    if not token.strip():
        raise KeeperError("token_missing", "Δεν δόθηκε One-Time Access Token.")
    try:
        from keeper_secrets_manager_core import SecretsManager
        from keeper_secrets_manager_core.storage import FileKeyValueStorage
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise KeeperError(
            "sdk_missing",
            "Λείπει το Keeper SDK: pip install keeper-secrets-manager-core",
        ) from exc

    config_path = os.path.expanduser(config_path)
    os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
    manager = SecretsManager(token=token.strip(), config=FileKeyValueStorage(config_path))
    try:
        records = manager.get_secrets()
    except Exception as exc:  # SDK raises its own hierarchy
        raise KeeperError("bind_failed", f"Keeper bind απέτυχε: {exc}") from exc
    finally:
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass

    out = []
    for record in records:
        data = getattr(record, "dict", None)
        if callable(data):
            data = data()
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                continue
        if isinstance(data, dict):
            out.append(data)
    return out


def import_records(
    conn: sqlite3.Connection,
    records: list[dict],
    group: str = "Keeper",
    dry_run: bool = False,
) -> dict:
    """Upsert KSM records as MUXIVRA hosts with vault credentials."""
    summary = {"seen": 0, "hosts_created": 0, "hosts_skipped": 0, "credentials": 0, "ignored": 0, "details": []}
    existing = {(r["label"] or "").lower() for r in conn.execute("SELECT label FROM hosts")}
    now = db.now()

    for raw in records:
        summary["seen"] += 1
        try:
            rec = normalise_record(raw)
        except KeeperError as exc:
            summary["ignored"] += 1
            summary["details"].append({"record": str(raw)[:60], "reason": exc.code})
            continue
        if not rec.usable:
            summary["ignored"] += 1
            summary["details"].append({"record": rec.title, "reason": "no host or no secret"})
            continue

        label = rec.title
        if label.lower() in existing:
            summary["hosts_skipped"] += 1
            summary["details"].append({"record": label, "reason": "host already exists"})
            continue

        cred_kind = "key" if rec.private_key else "password"
        secret_value = rec.private_key or rec.password
        summary["details"].append(
            {
                "record": label,
                "host": rec.host,
                "port": rec.port,
                "login": rec.login,
                "credential": cred_kind,
            }
        )
        if dry_run:
            continue

        cur = conn.execute(
            "INSERT INTO credentials(name, kind, username, secret_enc, passphrase_enc, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"keeper:{label}",
                cred_kind,
                rec.login,
                security.seal(secret_value),
                security.seal(rec.passphrase) if rec.passphrase else None,
                now,
                now,
            ),
        )
        cred_id = cur.lastrowid
        summary["credentials"] += 1

        conn.execute(
            "INSERT INTO hosts(uuid, label, protocol, hostname, port, username, credential_id, group_name, tags, "
            "color, notes, serial_port, baud, bytesize, parity, stopbits, created_at, updated_at) "
            "VALUES (?, ?, 'ssh', ?, ?, ?, ?, ?, ?, ?, ?, '', 115200, 8, 'N', 1, ?, ?)",
            (
                uuid.uuid4().hex,
                label,
                rec.host,
                rec.port,
                rec.login,
                cred_id,
                group,
                "keeper",
                "#7c9cff",
                rec.notes or f"Keeper record {rec.uid}".strip(),
                now,
                now,
            ),
        )
        summary["hosts_created"] += 1
        existing.add(label.lower())

    if not dry_run:
        db.log_event(None, "keeper_import", f"{summary['hosts_created']} hosts, {summary['credentials']} credentials", conn=conn)
    return summary
