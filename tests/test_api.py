import os
import stat
import time
from pathlib import Path

import pytest

from conftest import HEADERS, PASSWORD
from sshdeck import config, db, security

CRED = {"name": "prod-root", "kind": "password", "username": "root", "secret": "s3cret-passphrase-value"}


def test_status_uninitialized(client):
    body = client.get("/api/status").json()
    assert body["initialized"] is False
    assert body["authenticated"] is False


def test_setup_creates_session_and_logout_clears_it(client):
    res = client.post("/api/setup", json={"username": "admin", "password": PASSWORD})
    assert res.status_code == 200
    assert client.get("/api/status").json()["authenticated"] is True
    assert client.get("/api/hosts").status_code == 200
    client.post("/api/logout", headers=HEADERS)
    assert client.get("/api/status").json()["authenticated"] is False
    assert client.get("/api/hosts").status_code == 401


def test_setup_twice_conflicts(auth_client):
    res = auth_client.post("/api/setup", json={"username": "admin", "password": PASSWORD})
    assert res.status_code == 409


def test_short_password_rejected(client):
    res = client.post("/api/setup", json={"username": "admin", "password": "short"})
    assert res.status_code == 422


def test_login_requires_correct_password(auth_client):
    auth_client.post("/api/logout", headers=HEADERS)
    bad = auth_client.post("/api/login", json={"password": "wrong-password"})
    assert bad.status_code == 401
    good = auth_client.post("/api/login", json={"password": PASSWORD})
    assert good.status_code == 200
    assert auth_client.get("/api/status").json()["authenticated"] is True


def test_unauthenticated_api_is_closed(client):
    assert client.get("/api/hosts").status_code == 401
    assert client.get("/api/credentials").status_code == 401
    assert client.get("/api/events").status_code == 401


def test_mutation_without_csrf_header_refused(auth_client):
    res = auth_client.post("/api/snippets", json={"label": "x", "command": "uptime"})
    assert res.status_code == 403


def test_credentials_are_encrypted_at_rest_and_masked(auth_client):
    res = auth_client.post("/api/credentials", json=CRED, headers=HEADERS)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["preview"].endswith("alue") and body["preview"].startswith("••••")
    assert "secret" not in body
    raw = Path(str(config.DB_PATH)).read_bytes()
    assert b"s3cret-passphrase-value" not in raw
    assert not any(b"s3cret-passphrase-value" in p.read_bytes() for p in config.DATA_DIR.iterdir() if p.is_file())
    key_mode = stat.S_IMODE(os.stat(config.VAULT_KEY_PATH).st_mode)
    assert key_mode == 0o600
    assert security.open_sealed(
        db.connect().execute("SELECT secret_enc FROM credentials").fetchone()[0]
    ) == "s3cret-passphrase-value"


def test_private_key_validation(auth_client):
    bad = auth_client.post("/api/credentials", json={"name": "k", "kind": "key", "secret": "not-a-key"}, headers=HEADERS)
    assert bad.status_code == 422


def test_host_crud_and_credential_binding(auth_client):
    cred = auth_client.post("/api/credentials", json=CRED, headers=HEADERS).json()
    payload = {
        "label": "web-01", "hostname": "10.10.10.250", "port": 22, "username": "root",
        "credential_id": cred["id"], "group_name": "prod", "tags": "nginx, stage", "color": "#3ddc97",
        "notes": "",
    }
    created = auth_client.post("/api/hosts", json=payload, headers=HEADERS)
    assert created.status_code == 201, created.text
    host = created.json()
    assert host["tags"] == ["nginx", "stage"]
    assert host["credential_name"] == "prod-root"
    assert host["fingerprint"] is None

    listed = auth_client.get("/api/hosts").json()
    assert [h["label"] for h in listed] == ["web-01"]

    payload["label"] = "web-01-renamed"
    updated = auth_client.put(f"/api/hosts/{host['id']}", json=payload, headers=HEADERS)
    assert updated.json()["label"] == "web-01-renamed"

    in_use = auth_client.delete(f"/api/credentials/{cred['id']}", headers=HEADERS)
    assert in_use.status_code == 409

    assert auth_client.delete(f"/api/hosts/{host['id']}", headers=HEADERS).status_code == 200
    assert auth_client.get("/api/hosts").json() == []
    assert auth_client.delete(f"/api/credentials/{cred['id']}", headers=HEADERS).status_code == 200


def test_snippet_lifecycle(auth_client):
    created = auth_client.post("/api/snippets", json={"label": "Uptime", "command": "uptime"}, headers=HEADERS)
    assert created.status_code == 201
    sid = created.json()["id"]
    assert len(auth_client.get("/api/snippets").json()) == 1
    updated = auth_client.put(f"/api/snippets/{sid}", json={"label": "Up", "command": "uptime -p"}, headers=HEADERS)
    assert updated.json()["command"] == "uptime -p"
    assert auth_client.delete(f"/api/snippets/{sid}", headers=HEADERS).status_code == 200
    assert auth_client.get("/api/snippets").json() == []


SSH_CONFIG = """
Host bastion
    HostName 10.0.0.1
    User ops
    Port 2222

Host db-primary
    HostName db.example.net
    User postgres
    IdentityFile ~/.ssh/id_db

Host *
    ServerAliveInterval 30
"""


def test_import_ssh_config_skips_wildcards_and_duplicates(auth_client):
    res = auth_client.post("/api/import/ssh-config", json={"text": SSH_CONFIG}, headers=HEADERS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["imported"] == 2
    hosts = {h["label"]: h for h in auth_client.get("/api/hosts").json()}
    assert set(hosts) == {"bastion", "db-primary"}
    assert hosts["bastion"]["port"] == 2222
    assert hosts["db-primary"]["hostname"] == "db.example.net"
    again = auth_client.post("/api/import/ssh-config", json={"text": SSH_CONFIG}, headers=HEADERS).json()
    assert again["imported"] == 0 and again["skipped"] == 2


def test_password_change_revokes_sessions(auth_client):
    res = auth_client.post("/api/password", json={"current": PASSWORD, "new": "brand-new-secret"}, headers=HEADERS)
    assert res.status_code == 200
    assert auth_client.get("/api/hosts").status_code == 401
    assert auth_client.post("/api/login", json={"password": PASSWORD}).status_code == 401
    assert auth_client.post("/api/login", json={"password": "brand-new-secret"}).status_code == 200


def test_expired_session_is_rejected(auth_client):
    conn = db.connect()
    conn.execute("UPDATE sessions SET expires_at = ?", (time.time() - 5,))
    conn.commit()
    assert auth_client.get("/api/hosts").status_code == 401


def test_echo_websocket_is_gone(client):
    """The leftover test hook must not ship: it echoed anything unauthenticated."""
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws/echo") as ws:
            ws.receive_text()


def test_host_header_outside_loopback_is_rejected(client):
    res = client.get("/healthz", headers={"host": "evil.example.com"})
    assert res.status_code == 403
    assert res.json()["detail"] == "host_not_allowed"


def test_cross_site_origin_is_rejected(client):
    res = client.get("/healthz", headers={"origin": "https://evil.example.com"})
    assert res.status_code == 403
    ok = client.get("/healthz", headers={"origin": "http://127.0.0.1:8722"})
    assert ok.status_code == 200


def test_terminal_websocket_refuses_foreign_origin(auth_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/ws/terminal/1", headers={"origin": "https://evil.example.com"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_desktop_login_exchanges_single_use_token(client):
    from sshdeck import desktop

    token = desktop.new_token()
    res = client.post("/api/desktop-login", json={"token": token})
    assert res.status_code == 200, res.text
    assert res.json()["desktop"] is True
    assert client.get("/api/status").json()["authenticated"] is True
    assert client.get("/api/hosts").status_code == 200
    # replay of the same token must be refused
    assert client.post("/api/desktop-login", json={"token": token}).status_code == 401
    # unknown tokens are refused
    assert client.post("/api/desktop-login", json={"token": "x" * 32}).status_code == 401


def test_desktop_login_creates_local_account_without_password_prompt(client):
    from sshdeck import desktop

    res = client.post("/api/desktop-login", json={"token": desktop.new_token()})
    assert res.status_code == 200
    status = client.get("/api/status").json()
    assert status["initialized"] is True
    assert status["username"] == "local"
    # the generated password is unknown, so the web login form cannot be used
    assert client.post("/api/login", json={"password": "local"}).status_code == 401


def test_terminal_websocket_requires_session(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws/terminal/1"):
            pass


def test_events_record_activity(auth_client):
    auth_client.post("/api/credentials", json=CRED, headers=HEADERS)
    events = auth_client.get("/api/events").json()
    kinds = {e["kind"] for e in events}
    assert "login_ok" in kinds or "credential_created" in kinds
    assert "credential_created" in kinds


def test_overview_counts(auth_client):
    auth_client.post("/api/credentials", json=CRED, headers=HEADERS)
    body = auth_client.get("/api/overview").json()
    assert body["credentials"] == 1
    assert body["hosts"] == 0
    assert Path(body["data_dir"]).exists()


def test_security_headers_present(client):
    res = client.get("/api/status")
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]


def test_repeated_bad_logins_are_throttled(auth_client):
    from sshdeck import api as api_module

    api_module._failures.clear()
    codes = []
    for _ in range(api_module.LOGIN_MAX_FAILURES + 1):
        codes.append(auth_client.post("/api/login", json={"password": "nope-nope-nope"}).status_code)
    assert 429 in codes, codes
    assert codes.count(401) == api_module.LOGIN_MAX_FAILURES
    # a correct password is also refused while the window is saturated
    assert auth_client.post("/api/login", json={"password": PASSWORD}).status_code == 429
    api_module._failures.clear()


def test_host_colour_is_sanitised(auth_client):
    payload = {"label": "c", "hostname": "h", "port": 22, "username": "root", "color": "red;background:url(x)"}
    host = auth_client.post("/api/hosts", json=payload, headers=HEADERS).json()
    assert host["color"] == "#3ddc97"


def test_serial_flow_control_survives_create_update_and_reload(auth_client):
    """Regression: the select saved nothing — flow was dropped on insert/update/load."""
    auth_client.headers.update(HEADERS)
    base = {"label": "plc", "protocol": "serial", "serial_port": "/dev/null", "baud": 9600}
    created = auth_client.post("/api/hosts", json={**base, "flow": "rtscts"}).json()
    assert created["flow"] == "rtscts"

    rows = auth_client.get("/api/hosts").json()
    assert next(h for h in rows if h["id"] == created["id"])["flow"] == "rtscts"

    with db.db() as conn:
        stored = conn.execute("SELECT flow FROM hosts WHERE id = ?", (created["id"],)).fetchone()["flow"]
    assert stored == "rtscts"

    updated = auth_client.put(f"/api/hosts/{created['id']}", json={**base, "flow": "xonxoff"}).json()
    assert updated["flow"] == "xonxoff"

    from sshdeck import sshconn

    with db.db() as conn:
        target = sshconn.Target(
            host_id=created["id"], label="t", hostname="", port=0, username="", kind="none",
            serial_port="/dev/null", baud=9600, flow=conn.execute(
                "SELECT flow FROM hosts WHERE id = ?", (created["id"],)).fetchone()["flow"],
        )
    assert target.flow == "xonxoff"

    bad = auth_client.post("/api/hosts", json={**base, "flow": "carrier-pigeon"})
    assert bad.status_code == 422
