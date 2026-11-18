"""Telnet negotiation, serial (PTY) and protocol-aware API tests."""
import os
import pty
import select
import socket
import threading
import time

import pytest

from sshdeck import sshconn, transports
from sshdeck.transports import (
    DONT,
    DO,
    IAC,
    OPT_ECHO,
    OPT_NAWS,
    OPT_SGA,
    SB,
    SE,
    WILL,
    WONT,
    TelnetParser,
    TelnetSession,
)

HEADERS = {"x-requested-with": "sshdeck"}


# --------------------------------------------------------------- telnet wire
def test_telnet_parser_strips_commands_and_answers_negotiation():
    parser = TelnetParser(100, 30)
    raw = b"hello" + bytes([IAC, WILL, OPT_ECHO]) + b" world" + bytes([IAC, IAC]) + b"!"
    data, reply = parser.feed(raw)
    assert data == b"hello world\xff!"
    assert bytes([IAC, DO, OPT_ECHO]) in reply


def test_telnet_parser_handles_split_commands_and_naws():
    parser = TelnetParser(120, 40)
    first, reply1 = parser.feed(b"abc" + bytes([IAC]))
    assert first == b"abc" and reply1 == b""
    second, reply2 = parser.feed(bytes([DO, OPT_NAWS]))
    assert second == b""
    assert reply2.startswith(bytes([IAC, WILL, OPT_NAWS]))
    assert bytes([IAC, SB, OPT_NAWS, 0, 120, 0, 40, IAC, SE]) in reply2
    resized = parser.resize(132, 43)
    assert bytes([IAC, SB, OPT_NAWS, 0, 132, 0, 43, IAC, SE]) == resized


def test_telnet_parser_refuses_unknown_options():
    parser = TelnetParser()
    _, reply = parser.feed(bytes([IAC, DO, 99, IAC, WILL, 98, IAC, DO, OPT_SGA]))
    assert bytes([IAC, WONT, 99]) in reply
    assert bytes([IAC, DONT, 98]) in reply
    assert bytes([IAC, WILL, OPT_SGA]) in reply


def test_telnet_parser_skips_subnegotiation_payload():
    parser = TelnetParser()
    raw = b"A" + bytes([IAC, SB, 24, 0]) + b"xterm" + bytes([IAC, SE]) + b"B"
    data, _ = parser.feed(raw)
    assert data == b"AB"


class FakeTelnetServer(threading.Thread):
    """Minimal RFC854 server: negotiates, then echoes lines with a prefix."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.ready = threading.Event()

    def run(self):
        conn, _ = self.sock.accept()
        conn.sendall(bytes([IAC, WILL, OPT_ECHO, IAC, DO, OPT_NAWS]))
        conn.sendall(b"login: ")
        self.ready.set()
        conn.settimeout(0.3)
        parser = TelnetParser()
        buffer = b""
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            self.received.append(chunk)
            data, _ = parser.feed(chunk)  # drop the client's negotiation bytes
            buffer += data
            while b"\r\n" in buffer:
                line, buffer = buffer.split(b"\r\n", 1)
                if line:
                    conn.sendall(b"ECHO:" + line + b"\r\n")
        conn.close()
        self.sock.close()


def test_telnet_session_end_to_end():
    server = FakeTelnetServer()
    server.start()
    target = sshconn.Target(None, "telnet-test", "127.0.0.1", server.port, "", "none", protocol="telnet")
    session = TelnetSession(target, cols=100, rows=30)
    chunks = []
    session.on_data = chunks.append
    session.start()
    assert server.ready.wait(4)
    session.write("status\r")
    deadline = time.time() + 6
    while time.time() < deadline and b"ECHO:status" not in b"".join(chunks):
        time.sleep(0.05)
    session.close()
    payload = b"".join(chunks)
    assert b"login:" in payload
    assert b"ECHO:status" in payload
    joined = b"".join(server.received)
    assert b"status\r\n" in joined, joined
    # the client answered the server's NAWS request
    assert bytes([IAC, WILL, OPT_NAWS]) in joined


# ------------------------------------------------------------------- serial
def test_serial_session_roundtrip_over_pty():
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    target = sshconn.Target(
        None, "loop", "", 0, "", "none", protocol="serial", serial_port=slave_name, baud=115200
    )
    session = transports.SerialSession(target)
    chunks = []
    session.on_data = chunks.append
    session.start()
    try:
        os.write(master, b"BOOT OK\r\n")
        deadline = time.time() + 4
        while time.time() < deadline and b"BOOT OK" not in b"".join(chunks):
            time.sleep(0.05)
        assert b"BOOT OK" in b"".join(chunks)

        session.write("reboot\r")
        ready, _, _ = select.select([master], [], [], 4)
        assert ready, "device side received nothing"
        assert b"reboot\r" in os.read(master, 1024)
    finally:
        session.close()
        os.close(master)
        os.close(slave)


def test_serial_session_reports_missing_port():
    target = sshconn.Target(None, "x", "", 0, "", "none", protocol="serial", serial_port="/dev/does-not-exist")
    with pytest.raises(transports.TransportError) as excinfo:
        transports.SerialSession(target)
    assert excinfo.value.code == "serial_open_failed"


def test_open_session_dispatches_by_protocol():
    assert transports.open_session.__module__ == "sshdeck.transports"
    target = sshconn.Target(None, "x", "127.0.0.1", 9, "root", "none", protocol="serial", serial_port="")
    with pytest.raises(transports.TransportError):
        transports.open_session(target)


# ---------------------------------------------------------------------- API
def _host(**kwargs):
    base = {"label": "h", "protocol": "ssh", "hostname": "127.0.0.1", "port": 22, "username": "root"}
    base.update(kwargs)
    return base


def test_serial_host_requires_port(auth_client):
    res = auth_client.post("/api/hosts", json=_host(protocol="serial", serial_port=""), headers=HEADERS)
    assert res.status_code == 422


def test_telnet_and_serial_hosts_roundtrip(auth_client):
    telnet = auth_client.post(
        "/api/hosts", json=_host(label="gw", protocol="telnet", port=2323), headers=HEADERS
    )
    assert telnet.status_code == 201, telnet.text
    body = telnet.json()
    assert body["protocol"] == "telnet" and body["fingerprint"] is None

    serial = auth_client.post(
        "/api/hosts",
        json=_host(label="plc", protocol="serial", serial_port="/dev/ttyUSB0", baud=9600, parity="E", stopbits=2),
        headers=HEADERS,
    )
    assert serial.status_code == 201, serial.text
    ser = serial.json()
    assert (ser["serial_port"], ser["baud"], ser["parity"], ser["stopbits"]) == ("/dev/ttyUSB0", 9600, "E", 2)
    assert ser["fingerprint"] is None

    labels = {h["label"]: h["protocol"] for h in auth_client.get("/api/hosts").json()}
    assert labels == {"gw": "telnet", "plc": "serial"}


def test_unsupported_protocol_rejected(auth_client):
    res = auth_client.post("/api/hosts", json=_host(protocol="rdp"), headers=HEADERS)
    assert res.status_code == 422


def test_ssh_host_still_requires_hostname(auth_client):
    res = auth_client.post("/api/hosts", json=_host(hostname=""), headers=HEADERS)
    assert res.status_code == 422


def test_sftp_rejects_non_ssh_hosts(auth_client):
    host = auth_client.post(
        "/api/hosts", json=_host(label="gw", protocol="telnet", port=2323), headers=HEADERS
    ).json()
    res = auth_client.get(f"/api/hosts/{host['id']}/files", headers=HEADERS)
    assert res.status_code == 400
    assert res.json()["detail"] == "sftp_requires_ssh"


def test_serial_ports_endpoint(auth_client):
    res = auth_client.get("/api/serial/ports", headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert "ports" in body and isinstance(body["ports"], list)
    assert "platform" in body


def test_migration_adds_protocol_columns():
    from sshdeck import db

    with db.db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(hosts)")}
        version = conn.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()["value"]
    assert {"protocol", "serial_port", "baud", "bytesize", "parity", "stopbits"} <= columns
    assert version == str(db.SCHEMA_VERSION)
