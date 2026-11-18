"""HTTP + WebSocket API."""
from __future__ import annotations

import asyncio
import hashlib
import base64
import os
import posixpath
import re
import secrets
import tempfile
import threading
import time

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field, field_validator, model_validator

from . import config, db, security, sshconfig, sshconn, transports, update
from .sshconn import SshError, Target

MAX_DOWNLOAD = 200 * 1024 * 1024

# ------------------------------------------------------------------ models
class SetupIn(BaseModel):
    username: str = "admin"
    password: str = Field(min_length=8, max_length=256)


class DesktopLoginIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8, max_length=256)


class CredentialIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(password|key)$")
    username: str = ""
    secret: str = ""
    passphrase: str = ""


class HostIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    protocol: str = "ssh"
    hostname: str = ""
    port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    credential_id: int | None = None
    group_name: str = "General"
    tags: str = ""
    color: str = "#3ddc97"
    notes: str = ""
    serial_port: str = ""
    baud: int = Field(default=115200, ge=50, le=4_000_000)
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0

    @field_validator("group_name")
    @classmethod
    def _group(cls, v: str) -> str:
        return (v or "General").strip() or "General"

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        v = (v or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
            return "#3ddc97"
        return v

    @field_validator("protocol")
    @classmethod
    def _protocol(cls, v: str) -> str:
        v = (v or "ssh").strip().lower()
        if v not in ("ssh", "telnet", "serial"):
            raise ValueError("unsupported_protocol")
        return v

    @field_validator("parity")
    @classmethod
    def _parity(cls, v: str) -> str:
        v = (v or "N").strip().upper()[:1]
        return v if v in ("N", "E", "O", "M", "S") else "N"

    @field_validator("bytesize")
    @classmethod
    def _bytesize(cls, v: int) -> int:
        return v if v in (5, 6, 7, 8) else 8

    @field_validator("stopbits")
    @classmethod
    def _stopbits(cls, v: float) -> float:
        return v if float(v) in (1.0, 1.5, 2.0) else 1.0

    @model_validator(mode="after")
    def _required_fields(self):
        if self.protocol == "serial":
            if not self.serial_port.strip():
                raise ValueError("serial_port_required")
        elif not self.hostname.strip():
            raise ValueError("hostname_required")
        return self


class SnippetIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=8000)


class ImportIn(BaseModel):
    text: str = ""
    path: str = ""
    credential_id: int | None = None
    group_name: str = "Imported"


class UpdateRepoIn(BaseModel):
    repo: str = Field(max_length=120)


class UpdateCheckIn(BaseModel):
    repo: str = ""


class UpdateAssetIn(BaseModel):
    url: str
    name: str = "update.zip"
    size: int = 0
    digest: str = ""


class UpdateReleaseIn(BaseModel):
    latest: str
    asset: dict | None = None
    manifest: dict | None = None
    signature: dict | None = None


class PathIn(BaseModel):
    path: str


class RenameIn(BaseModel):
    path: str
    new_path: str


# ----------------------------------------------------------------- helpers
def guard(request: Request) -> None:
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get(config.CSRF_HEADER, "").lower() != config.CSRF_VALUE:
            raise HTTPException(status_code=403, detail="csrf_header_missing")
    with db.db() as conn:
        if not security.validate_session(conn, request.cookies.get(config.COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="unauthorized")


def _is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        config.COOKIE_NAME,
        token,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )


def row_to_host(row, credential_name: str | None = None) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "protocol": row["protocol"] or "ssh",
        "hostname": row["hostname"],
        "port": row["port"],
        "username": row["username"],
        "credential_id": row["credential_id"],
        "credential_name": credential_name,
        "group_name": row["group_name"],
        "tags": [t for t in (row["tags"] or "").split(",") if t],
        "color": row["color"],
        "notes": row["notes"],
        "serial_port": row["serial_port"] or "",
        "baud": row["baud"] or 115200,
        "bytesize": row["bytesize"] or 8,
        "parity": row["parity"] or "N",
        "stopbits": row["stopbits"] or 1,
        "last_connected_at": row["last_connected_at"],
        "updated_at": row["updated_at"],
        "fingerprint": (
            sshconn.known_fingerprint(row["hostname"], row["port"])
            if (row["protocol"] or "ssh") == "ssh"
            else None
        ),
    }


def fetch_host(conn, host_id: int):
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="host_not_found")
    return row


def credential_name_for(conn, credential_id) -> str | None:
    if not credential_id:
        return None
    row = conn.execute("SELECT name FROM credentials WHERE id = ?", (credential_id,)).fetchone()
    return row["name"] if row else None


def load_target(conn, host_id: int) -> Target:
    row = fetch_host(conn, host_id)
    cred = None
    if row["credential_id"]:
        cred = conn.execute("SELECT * FROM credentials WHERE id = ?", (row["credential_id"],)).fetchone()
    kind = cred["kind"] if cred else "none"
    secret = security.open_sealed(cred["secret_enc"]) if cred else ""
    passphrase = (
        security.open_sealed(cred["passphrase_enc"]) if cred and cred["passphrase_enc"] else ""
    )
    username = row["username"] or (cred["username"] if cred else "")
    return Target(
        host_id=row["id"],
        label=row["label"],
        hostname=row["hostname"],
        port=row["port"],
        username=username or ("root" if (row["protocol"] or "ssh") == "ssh" else ""),
        kind=kind,
        secret=secret,
        passphrase=passphrase,
        protocol=row["protocol"] or "ssh",
        serial_port=row["serial_port"] or "",
        baud=row["baud"] or 115200,
        bytesize=row["bytesize"] or 8,
        parity=row["parity"] or "N",
        stopbits=row["stopbits"] or 1.0,
    )


def target_or_404(host_id: int) -> Target:
    with db.db() as conn:
        return load_target(conn, host_id)


def sftp_target(host_id: int) -> Target:
    target = target_or_404(host_id)
    if target.protocol != "ssh":
        raise HTTPException(status_code=400, detail="sftp_requires_ssh")
    return target


# --------------------------------------------------------- login throttling
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_FAILURES = 8
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def throttle_login(key: str) -> None:
    now = time.time()
    with _failures_lock:
        stamps = [t for t in _failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        _failures[key] = stamps
        if len(stamps) >= LOGIN_MAX_FAILURES:
            raise HTTPException(status_code=429, detail="too_many_attempts")


def record_login_failure(key: str) -> None:
    with _failures_lock:
        _failures.setdefault(key, []).append(time.time())


def reset_login_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)


# ------------------------------------------------------------ public routes
public = APIRouter(prefix="/api")


@public.get("/status")
def status(request: Request):
    with db.db() as conn:
        initialized = security.user_exists(conn)
        authed = security.validate_session(conn, request.cookies.get(config.COOKIE_NAME))
        username = None
        if initialized:
            username = conn.execute("SELECT username FROM users WHERE id = 1").fetchone()["username"]
    return {
        "initialized": initialized,
        "authenticated": authed,
        "username": username,
        "version": __import__("sshdeck").__version__,
        "demo": bool(os.environ.get("SSHDECK_DEMO")),
    }


@public.post("/setup")
def setup(payload: SetupIn, request: Request, response: Response):
    with db.db() as conn:
        if security.user_exists(conn):
            raise HTTPException(status_code=409, detail="already_initialized")
        security.create_user(conn, payload.username, payload.password)
        token = security.create_session(conn, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token)
    return {"ok": True}


@public.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    throttle_login(client_ip)
    with db.db() as conn:
        if not security.user_exists(conn):
            raise HTTPException(status_code=409, detail="not_initialized")
        if not security.check_login(conn, payload.password):
            record_login_failure(client_ip)
            db.log_event(None, "login_failed", client_ip, conn=conn)
            raise HTTPException(status_code=401, detail="bad_password")
        security.purge_expired(conn)
        token = security.create_session(conn, request.headers.get("user-agent", ""))
        db.log_event(None, "login_ok", "", conn=conn)
        reset_login_failures(client_ip)
    set_session_cookie(response, request, token)
    return {"ok": True}


@public.post("/desktop-login")
def desktop_login(payload: DesktopLoginIn, request: Request, response: Response):
    """Exchange a single-use desktop token (created in-process) for a session."""
    from . import desktop

    if not desktop.consume_token(payload.token):
        raise HTTPException(status_code=401, detail="invalid_desktop_token")
    with db.db() as conn:
        if not security.user_exists(conn):
            # Desktop install: the OS session is the trust boundary, so the local
            # account gets an unguessable password that nobody needs to type.
            security.create_user(conn, "local", secrets.token_urlsafe(32))
        session_token = security.create_session(conn, "desktop", ttl=30 * 24 * 3600)
        db.log_event(None, "desktop_login", "", conn=conn)
    set_session_cookie(response, request, session_token)
    return {"ok": True, "desktop": True}


@public.post("/logout")
def logout(request: Request, response: Response):
    with db.db() as conn:
        security.drop_session(conn, request.cookies.get(config.COOKIE_NAME))
    response.delete_cookie(config.COOKIE_NAME, path="/")
    return {"ok": True}


# ----------------------------------------------------------- guarded routes
api = APIRouter(prefix="/api", dependencies=[Depends(guard)])


@api.post("/password")
def change_password(payload: PasswordIn):
    with db.db() as conn:
        if not security.check_login(conn, payload.current):
            raise HTTPException(status_code=401, detail="bad_password")
        security.change_password(conn, payload.new)
        conn.execute("DELETE FROM sessions")
    return {"ok": True}


# -------------------------------------------------------------- credentials
def _credential_out(row) -> dict:
    secret = ""
    try:
        secret = security.open_sealed(row["secret_enc"])
    except security.VaultError:
        secret = ""
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "username": row["username"],
        "preview": security.mask(secret),
        "has_passphrase": bool(row["passphrase_enc"]),
        "updated_at": row["updated_at"],
    }


@api.get("/vault")
def vault_state():
    security.enforce_auto_lock()
    return security.vault_state()


@api.post("/vault/lock")
def vault_lock():
    security.lock()
    with db.db() as conn:
        db.log_event(None, "vault_locked", "", conn=conn)
    return {"ok": True, "locked": True}


@api.post("/vault/unlock")
def vault_unlock():
    """Re-open the vault (DPAPI/0600 key access) and prove it works."""
    try:
        security.unlock()
        proof = security.seal("unlock-probe")
        security.open_sealed(proof)
    except security.VaultError as exc:
        raise HTTPException(status_code=409, detail=f"vault:{exc}")
    return {"ok": True, "locked": False, **security.vault_state()}


@api.get("/credentials")
def list_credentials():
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM credentials ORDER BY name COLLATE NOCASE").fetchall()
        return [_credential_out(r) for r in rows]


@api.post("/credentials", status_code=201)
def create_credential(payload: CredentialIn):
    if not payload.secret.strip():
        raise HTTPException(status_code=422, detail="secret_required")
    if payload.kind == "key" and "PRIVATE KEY" not in payload.secret:
        raise HTTPException(status_code=422, detail="not_a_private_key")
    now = db.now()
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO credentials(name, kind, username, secret_enc, passphrase_enc, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload.name.strip(),
                payload.kind,
                payload.username.strip(),
                security.seal(payload.secret.strip()),
                security.seal(payload.passphrase) if payload.passphrase else None,
                now,
                now,
            ),
        )
        db.log_event(None, "credential_created", payload.name.strip(), conn=conn)
        row = conn.execute("SELECT * FROM credentials WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _credential_out(row)


@api.put("/credentials/{credential_id}")
def update_credential(credential_id: int, payload: CredentialIn):
    with db.db() as conn:
        row = conn.execute("SELECT * FROM credentials WHERE id = ?", (credential_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="credential_not_found")
        secret_enc = security.seal(payload.secret.strip()) if payload.secret.strip() else row["secret_enc"]
        passphrase_enc = security.seal(payload.passphrase) if payload.passphrase else row["passphrase_enc"]
        conn.execute(
            "UPDATE credentials SET name = ?, kind = ?, username = ?, secret_enc = ?, passphrase_enc = ?, "
            "updated_at = ? WHERE id = ?",
            (
                payload.name.strip(),
                payload.kind,
                payload.username.strip(),
                secret_enc,
                passphrase_enc,
                db.now(),
                credential_id,
            ),
        )
        row = conn.execute("SELECT * FROM credentials WHERE id = ?", (credential_id,)).fetchone()
        return _credential_out(row)


@api.delete("/credentials/{credential_id}")
def delete_credential(credential_id: int):
    with db.db() as conn:
        used = conn.execute("SELECT COUNT(*) c FROM hosts WHERE credential_id = ?", (credential_id,)).fetchone()["c"]
        if used:
            raise HTTPException(status_code=409, detail=f"in_use_by_{used}_hosts")
        conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
        db.log_event(None, "credential_deleted", str(credential_id), conn=conn)
    return {"ok": True}


# -------------------------------------------------------------------- hosts
@api.get("/hosts")
def list_hosts():
    with db.db() as conn:
        rows = conn.execute(
            "SELECT h.*, c.name AS credential_name FROM hosts h "
            "LEFT JOIN credentials c ON c.id = h.credential_id "
            "ORDER BY h.group_name COLLATE NOCASE, h.label COLLATE NOCASE"
        ).fetchall()
        return [row_to_host(r, r["credential_name"]) for r in rows]


@api.post("/hosts", status_code=201)
def create_host(payload: HostIn):
    now = db.now()
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO hosts(label, protocol, hostname, port, username, credential_id, group_name, tags, "
            "color, notes, serial_port, baud, bytesize, parity, stopbits, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload.label.strip(),
                payload.protocol,
                payload.hostname.strip(),
                payload.port,
                payload.username.strip(),
                payload.credential_id,
                payload.group_name,
                ",".join(t.strip() for t in payload.tags.split(",") if t.strip()),
                payload.color,
                payload.notes,
                payload.serial_port.strip(),
                payload.baud,
                payload.bytesize,
                payload.parity,
                payload.stopbits,
                now,
                now,
            ),
        )
        db.log_event(cur.lastrowid, "host_created", payload.label.strip(), conn=conn)
        row = fetch_host(conn, cur.lastrowid)
        return row_to_host(row, credential_name_for(conn, row["credential_id"]))


@api.put("/hosts/{host_id}")
def update_host(host_id: int, payload: HostIn):
    with db.db() as conn:
        fetch_host(conn, host_id)
        conn.execute(
            "UPDATE hosts SET label = ?, protocol = ?, hostname = ?, port = ?, username = ?, "
            "credential_id = ?, group_name = ?, tags = ?, color = ?, notes = ?, serial_port = ?, "
            "baud = ?, bytesize = ?, parity = ?, stopbits = ?, updated_at = ? WHERE id = ?",
            (
                payload.label.strip(),
                payload.protocol,
                payload.hostname.strip(),
                payload.port,
                payload.username.strip(),
                payload.credential_id,
                payload.group_name,
                ",".join(t.strip() for t in payload.tags.split(",") if t.strip()),
                payload.color,
                payload.notes,
                payload.serial_port.strip(),
                payload.baud,
                payload.bytesize,
                payload.parity,
                payload.stopbits,
                db.now(),
                host_id,
            ),
        )
        row = fetch_host(conn, host_id)
        return row_to_host(row, credential_name_for(conn, row["credential_id"]))


@api.delete("/hosts/{host_id}")
def delete_host(host_id: int):
    with db.db() as conn:
        fetch_host(conn, host_id)
        conn.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
        db.log_event(None, "host_deleted", str(host_id), conn=conn)
    return {"ok": True}


@api.post("/hosts/{host_id}/trust")
def trust_host_key(host_id: int, payload: dict):
    """Approve the exact fingerprint the user was shown."""
    target = target_or_404(host_id)
    fingerprint = str(payload.get("fingerprint") or "").strip()
    if not fingerprint:
        raise HTTPException(status_code=422, detail="fingerprint_required")
    try:
        stored = sshconn.trust_pending_key(host_id, fingerprint)
    except SshError as exc:
        raise HTTPException(status_code=409, detail=f"{exc.code}: {exc.message}")
    with db.db() as conn:
        db.log_event(host_id, "host_key_trusted", f"{target.hostname} {stored}", conn=conn)
    return {"ok": True, "fingerprint": stored}


@api.delete("/hosts/{host_id}/trust")
def forget_host_key(host_id: int):
    """Drop trust for this host:port only — other ports of the same host keep theirs."""
    target = target_or_404(host_id)
    removed = sshconn.forget_host_key(target.hostname, int(target.port or 22))
    with db.db() as conn:
        db.log_event(host_id, "host_key_forgotten", f"{target.hostname}:{target.port}", conn=conn)
    return {"ok": True, "removed": removed}


@api.get("/hosts/{host_id}/trust")
def host_trust_state(host_id: int):
    target = target_or_404(host_id)
    return {
        "hostname": target.hostname,
        "port": int(target.port or 22),
        "fingerprint": sshconn.known_fingerprint(target.hostname, int(target.port or 22)),
    }


@api.post("/hosts/{host_id}/ping")
async def ping_host(host_id: int):
    target = target_or_404(host_id)
    if target.protocol == "serial":
        known = {p["device"] for p in transports.serial_ports()}
        exists = target.serial_port in known or os.path.exists(target.serial_port)
        return {"reachable": exists, "protocol": "serial", "port": target.serial_port}
    try:
        info = await asyncio.to_thread(transports.tcp_probe, target.hostname, target.port)
        if target.protocol == "ssh":
            info["fingerprint"] = sshconn.known_fingerprint(target.hostname, target.port)
        return {"reachable": True, "protocol": target.protocol, **info}
    except OSError as exc:
        return {"reachable": False, "protocol": target.protocol, "error": str(exc)}


@api.get("/serial/ports")
def list_serial_ports():
    import sys

    return {"ports": transports.serial_ports(), "platform": sys.platform}


@api.post("/hosts/{host_id}/forget-host-key")
def forget_host_key(host_id: int):
    with db.db() as conn:
        row = fetch_host(conn, host_id)
        removed = sshconn.forget_host_keys(row["hostname"], row["port"])
        db.log_event(host_id, "host_key_forgotten", f"{row['hostname']} ({removed} lines)", conn=conn)
    return {"ok": True, "removed": removed}


@api.post("/hosts/{host_id}/touch")
def touch_host(host_id: int):
    with db.db() as conn:
        fetch_host(conn, host_id)
        conn.execute("UPDATE hosts SET last_connected_at = ? WHERE id = ?", (db.now(), host_id))
    return {"ok": True}


# ----------------------------------------------------------------- snippets
@api.get("/snippets")
def list_snippets():
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM snippets ORDER BY label COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]


@api.post("/snippets", status_code=201)
def create_snippet(payload: SnippetIn):
    now = db.now()
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO snippets(label, command, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (payload.label.strip(), payload.command, now, now),
        )
        return dict(conn.execute("SELECT * FROM snippets WHERE id = ?", (cur.lastrowid,)).fetchone())


@api.put("/snippets/{snippet_id}")
def update_snippet(snippet_id: int, payload: SnippetIn):
    with db.db() as conn:
        if not conn.execute("SELECT 1 FROM snippets WHERE id = ?", (snippet_id,)).fetchone():
            raise HTTPException(status_code=404, detail="snippet_not_found")
        conn.execute(
            "UPDATE snippets SET label = ?, command = ?, updated_at = ? WHERE id = ?",
            (payload.label.strip(), payload.command, db.now(), snippet_id),
        )
        return dict(conn.execute("SELECT * FROM snippets WHERE id = ?", (snippet_id,)).fetchone())


@api.delete("/snippets/{snippet_id}")
def delete_snippet(snippet_id: int):
    with db.db() as conn:
        conn.execute("DELETE FROM snippets WHERE id = ?", (snippet_id,))
    return {"ok": True}


# ---------------------------------------------------------------- updates
@api.get("/update/state")
def update_state():
    with db.db() as conn:
        repo = db.get_setting(conn, "update_repo", "") or ""
        last = db.get_setting(conn, "update_last_check", "") or ""
    return {
        "current": __import__("sshdeck").__version__,
        "repo": repo,
        "last_check": last,
        "platform": "windows-portable" if (config.BASE_DIR / "runtime" / "python").exists() else "source",
        "vault_key": security.vault_key_status(),
        "signing_key": _signing_key_state(),
    }


def _signing_key_state() -> dict:
    """Whether this install can verify release signatures at all (fail closed)."""
    try:
        key = update.trusted_key()
    except update.UpdateError as exc:
        return {"configured": False, "reason": exc.code}
    return {"configured": True, "fingerprint": hashlib.sha256(key).hexdigest()[:16]}


@api.post("/update/repo")
def update_set_repo(payload: UpdateRepoIn):
    repo = payload.repo.strip()
    if repo and not update.REPO_RE.match(repo):
        raise HTTPException(status_code=422, detail="invalid_repo")
    with db.db() as conn:
        db.set_setting(conn, "update_repo", repo)
    return {"ok": True, "repo": repo}


@api.post("/update/check")
async def update_check(payload: UpdateCheckIn):
    with db.db() as conn:
        repo = payload.repo.strip() or (db.get_setting(conn, "update_repo", "") or "")
    if not repo:
        raise HTTPException(status_code=422, detail="repo_not_set")
    try:
        result = await asyncio.to_thread(update.check, repo, __import__("sshdeck").__version__)
    except update.UpdateError as exc:
        raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")
    with db.db() as conn:
        db.set_setting(conn, "update_last_check", time.strftime("%Y-%m-%d %H:%M:%S"))
        if payload.repo.strip():
            db.set_setting(conn, "update_repo", repo)
    return result


@api.post("/update/prepare")
async def update_prepare(payload: UpdateReleaseIn):
    """Verify the signed release, stage it OUTSIDE the bundle, write apply-update.cmd."""
    release = payload.model_dump()
    release["latest"] = payload.latest
    try:
        manifest = await asyncio.to_thread(update.fetch_manifest, release)
        archive = await asyncio.to_thread(
            update.download_verified, release, manifest, config.STAGING_DIR
        )
        root = await asyncio.to_thread(update.stage, archive, config.STAGING_DIR)
        script = await asyncio.to_thread(
            update.write_apply_script,
            update.bundle_root(),
            root,
            os.getpid(),
            config.STAGING_DIR,
        )
    except update.UpdateError as exc:
        with db.db() as conn:
            db.log_event(conn=conn, host_id=None, kind="update_rejected", detail=f"{exc.code}")
        raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")
    with db.db() as conn:
        db.log_event(None, "update_prepared", f"{manifest['version']} {manifest['sha256'][:12]}", conn=conn)
    return {
        "version": manifest["version"],
        "archive": str(archive),
        "staged": str(root),
        "script": str(script),
        "pid": os.getpid(),
    }


# --------------------------------------------------------------------- SFTP
@api.get("/hosts/{host_id}/files")
async def list_files(host_id: int, path: str = "."):
    target = sftp_target(host_id)

    def _work():
        client, sftp = sshconn.sftp(target)
        try:
            entries = sshconn.listdir(sftp, path)
            try:
                cwd = sftp.normalize(path or ".")
            except Exception:
                cwd = path or "."
            return {"cwd": cwd, "entries": entries}
        finally:
            sftp.close()
            client.close()

    try:
        result = await asyncio.to_thread(_work)
    except SshError as exc:
        raise HTTPException(status_code=502, detail=f"{exc.code}: {exc.message}")
    except IOError as exc:
        raise HTTPException(status_code=404, detail=f"sftp_io: {exc}")
    return result


@api.get("/hosts/{host_id}/download")
async def download_file(host_id: int, path: str):
    target = sftp_target(host_id)

    def _read() -> bytes:
        client, sftp = sshconn.sftp(target)
        try:
            attr = sftp.stat(path)
            if attr.st_size and attr.st_size > MAX_DOWNLOAD:
                raise HTTPException(status_code=413, detail="file_too_large")
            with sftp.open(path, "rb") as fh:
                return fh.read()
        finally:
            sftp.close()
            client.close()

    data = await asyncio.to_thread(_read)
    filename = posixpath.basename(path) or "download"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api.post("/hosts/{host_id}/upload")
async def upload_file(host_id: int, path: str, file: UploadFile = File(...)):
    target = sftp_target(host_id)
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(config.DATA_DIR))
    try:
        while chunk := await file.read(1024 * 256):
            tmp.write(chunk)
        tmp.close()

        def _put():
            client, sftp = sshconn.sftp(target)
            try:
                sftp.put(tmp.name, path)
            finally:
                sftp.close()
                client.close()

        await asyncio.to_thread(_put)
        return {"ok": True, "path": path, "name": file.filename}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@api.post("/hosts/{host_id}/mkdir")
async def make_dir(host_id: int, payload: PathIn):
    target = sftp_target(host_id)

    def _work():
        client, sftp = sshconn.sftp(target)
        try:
            sftp.mkdir(payload.path)
        finally:
            sftp.close()
            client.close()

    await asyncio.to_thread(_work)
    return {"ok": True}


@api.post("/hosts/{host_id}/delete-path")
async def delete_path(host_id: int, payload: PathIn):
    target = sftp_target(host_id)

    def _work():
        client, sftp = sshconn.sftp(target)
        try:
            attr = sftp.stat(payload.path)
            import stat as stat_mod

            if stat_mod.S_ISDIR(attr.st_mode or 0):
                sftp.rmdir(payload.path)
            else:
                sftp.remove(payload.path)
        finally:
            sftp.close()
            client.close()

    await asyncio.to_thread(_work)
    return {"ok": True}


@api.post("/hosts/{host_id}/rename")
async def rename_path(host_id: int, payload: RenameIn):
    target = sftp_target(host_id)

    def _work():
        client, sftp = sshconn.sftp(target)
        try:
            sftp.rename(payload.path, payload.new_path)
        finally:
            sftp.close()
            client.close()

    await asyncio.to_thread(_work)
    return {"ok": True}


# ------------------------------------------------------------------- import
@api.post("/import/ssh-config")
def import_ssh_config(payload: ImportIn):
    text = payload.text
    if not text and payload.path:
        expanded = os.path.expanduser(payload.path)
        if not os.access(expanded, os.R_OK):
            raise HTTPException(status_code=400, detail="config_unreadable")
        with open(expanded, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    parsed = sshconfig.parse(text or "")
    if not parsed:
        return {"imported": 0, "skipped": 0, "hosts": []}
    now = db.now()
    imported = []
    skipped = 0
    with db.db() as conn:
        existing = {r["label"].lower() for r in conn.execute("SELECT label FROM hosts").fetchall()}
        for item in parsed:
            if item.alias.lower() in existing:
                skipped += 1
                continue
            cur = conn.execute(
                "INSERT INTO hosts(label, hostname, port, username, credential_id, group_name, tags, color, "
                "notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.alias,
                    item.hostname or item.alias,
                    item.port,
                    item.user or "root",
                    payload.credential_id,
                    payload.group_name,
                    "imported",
                    "#7c9cff",
                    f"IdentityFile: {item.identity_file}" if item.identity_file else "",
                    now,
                    now,
                ),
            )
            imported.append({"id": cur.lastrowid, "label": item.alias})
            existing.add(item.alias.lower())
        db.log_event(None, "ssh_config_imported", f"{len(imported)} hosts", conn=conn)
    return {"imported": len(imported), "skipped": skipped, "hosts": imported}


# ------------------------------------------------------------------ events
@api.get("/events")
def list_events(limit: int = 40):
    with db.db() as conn:
        rows = conn.execute(
            "SELECT e.*, h.label AS host_label FROM events e LEFT JOIN hosts h ON h.id = e.host_id "
            "ORDER BY e.created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(r) for r in rows]


@api.get("/overview")
def overview():
    with db.db() as conn:
        hosts = conn.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"]
        groups = conn.execute("SELECT COUNT(DISTINCT group_name) c FROM hosts").fetchone()["c"]
        creds = conn.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"]
        snippets = conn.execute("SELECT COUNT(*) c FROM snippets").fetchone()["c"]
        recent = conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
    return {
        "hosts": hosts,
        "groups": groups,
        "credentials": creds,
        "snippets": snippets,
        "recent": [dict(r) for r in recent],
        "data_dir": str(config.DATA_DIR),
    }


# --------------------------------------------------------- terminal websocket
def _ws_authorized(ws: WebSocket) -> bool:
    with db.db() as conn:
        return security.validate_session(conn, ws.cookies.get(config.COOKIE_NAME))


async def terminal_endpoint(ws: WebSocket, host_id: int, cols: int = 120, rows: int = 32) -> None:
    if not _ws_authorized(ws):
        await ws.close(code=4401)
        return
    try:
        target = target_or_404(host_id)
    except HTTPException:
        await ws.close(code=4404)
        return

    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    try:
        session = await asyncio.to_thread(transports.open_session, target, cols, rows)
    except sshconn.HostKeyUnknown as exc:
        await ws.send_json({
            "type": "error",
            "code": "host_key_unknown",
            "message": exc.message,
            "fingerprint": exc.fingerprint,
            "hostname": exc.hostname,
            "key_type": exc.key_type,
        })
        await ws.close(code=4000)
        return
    except (SshError, transports.TransportError) as exc:
        await ws.send_json({"type": "error", "code": exc.code, "message": exc.message})
        await ws.close(code=4000)
        return
    except Exception as exc:  # pragma: no cover - unexpected transport failure
        await ws.send_json({"type": "error", "code": "connect_failed", "message": str(exc)})
        await ws.close(code=4000)
        return

    session.on_data = lambda chunk: loop.call_soon_threadsafe(queue.put_nowait, ("data", chunk))
    session.on_close = lambda: loop.call_soon_threadsafe(queue.put_nowait, ("close", None))
    session.start()

    with db.db() as conn:
        conn.execute("UPDATE hosts SET last_connected_at = ? WHERE id = ?", (db.now(), host_id))
        db.log_event(host_id, "session_open", target.describe(), conn=conn)

    await ws.send_json(
        {
            "type": "ready",
            "host": target.label,
            "protocol": target.protocol,
            "target": target.describe(),
            "fingerprint": (
                sshconn.known_fingerprint(target.hostname, target.port)
                if target.protocol == "ssh"
                else None
            ),
            "cols": cols,
            "rows": rows,
            "started_at": time.time(),
        }
    )

    async def pump() -> None:
        while True:
            kind, chunk = await queue.get()
            if kind == "close":
                await ws.send_json({"type": "closed"})
                return
            await ws.send_json(
                {"type": "data", "b64": base64.b64encode(chunk).decode("ascii")}
            )

    async def listen() -> None:
        while True:
            message = await ws.receive_json()
            mtype = message.get("type")
            if mtype == "input":
                session.write(message.get("data", ""))
            elif mtype == "resize":
                session.resize(int(message.get("cols", cols)), int(message.get("rows", rows)))
            elif mtype == "ping":
                await ws.send_json({"type": "pong"})

    tasks = [asyncio.create_task(pump()), asyncio.create_task(listen())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        session.close()
        with db.db() as conn:
            db.log_event(host_id, "session_close", f"{target.username}@{target.hostname}", conn=conn)
