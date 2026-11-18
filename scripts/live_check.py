"""Live integration checks against the local SSH daemon (real network, real auth).

Run with: .venv/bin/python scripts/live_check.py
Exits non-zero if any check fails.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sshdeck import config, sshconn  # noqa: E402

KEY = (ROOT / "testdata/deploy_key").read_text()
PASSWORD = (ROOT / "runtime/deploy.pass").read_text().strip()
results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, "PASS", detail))
    except Exception as exc:  # noqa: BLE001
        results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))


def shell_check():
    target = sshconn.Target(1, "local", "127.0.0.1", 22, "deploy", "key", KEY)
    session = sshconn.ShellSession(target, cols=100, rows=30)
    try:
        session.write("echo LIVE-SHELL-$(date +%s)\n")
        buffer = b""
        deadline = time.time() + 12
        while time.time() < deadline:
            chunk = session.channel.recv(4096)
            if chunk:
                buffer += chunk
            if b"LIVE-SHELL-" in buffer and buffer.count(b"$") >= 1 and buffer.rstrip().endswith(b"$"):
                break
        text = buffer.decode("utf-8", "replace")
        assert "LIVE-SHELL-" in text, text[-400:]
        return text.strip().splitlines()[-2][:120]
    finally:
        session.close()


def password_check():
    target = sshconn.Target(2, "local-pw", "127.0.0.1", 22, "deploy", "password", PASSWORD)
    client = sshconn.connect(target)
    try:
        _, stdout, _ = client.exec_command("id -un")
        user = stdout.read().decode().strip()
        assert user == "deploy", user
        return f"password auth ok as {user}"
    finally:
        client.close()


def sftp_check():
    target = sshconn.Target(1, "local", "127.0.0.1", 22, "deploy", "key", KEY)
    client, sftp = sshconn.sftp(target)
    try:
        entries = sshconn.listdir(sftp, "/home/deploy")
        names = {e["name"] for e in entries}
        assert ".ssh" in names, names
        stats = sftp.stat("/home/deploy/.ssh")
        return f"{len(entries)} entries, .ssh mode {oct(stats.st_mode)}"
    finally:
        sftp.close()
        client.close()


def tofu_rejects_changed_key():
    """Replace the stored key of the *same* algorithm with another key."""
    import base64
    import subprocess
    import tempfile

    path = Path(sshconn._ensure_known_hosts())
    backup = path.with_suffix(".bak")
    shutil.copy2(path, backup)
    tmpdir = tempfile.mkdtemp()
    try:
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{tmpdir}/impostor"],
            check=True,
        )
        pub = Path(f"{tmpdir}/impostor.pub").read_text().split()
        line = f"127.0.0.1 {pub[0]} {pub[1]}\n"
        kept = [l for l in path.read_text().splitlines(True) if not l.startswith("127.0.0.1 ")]
        path.write_text("".join(kept) + line)
        target = sshconn.Target(1, "local", "127.0.0.1", 22, "deploy", "key", KEY)
        try:
            sshconn.connect(target)
        except sshconn.HostKeyChanged:
            return "connect refused after host key change"
        raise AssertionError("connection accepted with a changed host key")
    finally:
        shutil.move(str(backup), str(path))


def bad_password_check():
    target = sshconn.Target(3, "local", "127.0.0.1", 22, "deploy", "password", "definitely-wrong-password")
    try:
        sshconn.connect(target)
    except sshconn.SshError as exc:
        assert exc.code == "auth_failed", exc.code
        return "wrong password rejected with auth_failed"
    raise AssertionError("wrong password accepted")


def closed_port_check():
    target = sshconn.Target(4, "dead", "127.0.0.1", 9, "deploy", "key", KEY)
    try:
        sshconn.connect(target, timeout=3)
    except sshconn.SshError as exc:
        assert exc.code in ("network", "timeout", "ssh_error"), exc.code
        return f"unreachable host -> {exc.code}"
    raise AssertionError("connected to a closed port")


# --------------------------------------------------------------- telnet / serial
def _wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"mock device did not open port {port}")


def telnet_check():
    from sshdeck import transports

    port = 2400 + (os.getpid() % 200)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/fake_device.py"), "--telnet", str(port),
         "--log", str(ROOT / "runtime/telnet-device.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_port(port)
        target = sshconn.Target(None, "mock-telnet", "127.0.0.1", port, "", "none", protocol="telnet")
        session = transports.TelnetSession(target, cols=100, rows=30)
        chunks = []
        session.on_data = chunks.append
        session.start()
        session.write("status\r")
        deadline = time.time() + 8
        while time.time() < deadline and b"relays=4/4" not in b"".join(chunks):
            time.sleep(0.05)
        session.close()
        payload = b"".join(chunks)
        assert b"Mock Device" in payload, payload[-200:]
        assert b"relays=4/4" in payload, payload[-200:]
        return "telnet banner + command reply ok"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def serial_check():
    from sshdeck import transports

    slave_file = ROOT / "runtime/live-pty-name"
    if slave_file.exists():
        slave_file.unlink()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/fake_device.py"), "--serial",
         "--slave-file", str(slave_file), "--log", str(ROOT / "runtime/serial-device.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 8
        while time.time() < deadline and not (slave_file.exists() and slave_file.read_text().strip()):
            time.sleep(0.1)
        device = slave_file.read_text().strip()
        assert device, "no pty allocated"
        target = sshconn.Target(None, "mock-serial", "", 0, "", "none",
                                protocol="serial", serial_port=device, baud=115200)
        session = transports.SerialSession(target, cols=100, rows=30)
        chunks = []
        session.on_data = chunks.append
        session.start()
        session.write("version\r")
        deadline = time.time() + 8
        while time.time() < deadline and b"firmware 1.0.3" not in b"".join(chunks):
            time.sleep(0.05)
        session.close()
        payload = b"".join(chunks)
        assert b"firmware 1.0.3" in payload, payload[-200:]
        return f"serial {device} banner + command reply ok"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


check("interactive shell over key auth", shell_check)
check("password auth", password_check)
check("sftp listing", sftp_check)
check("wrong password rejected", bad_password_check)
check("changed host key rejected (TOFU)", tofu_rejects_changed_key)
check("closed port reported", closed_port_check)
check("telnet session", telnet_check)
check("serial (COM) session", serial_check)

width = max(len(name) for name, _, _ in results)
failed = 0
for name, status, detail in results:
    if status != "PASS":
        failed += 1
    print(f"{status:4} {name:<{width}}  {detail}")
print(f"\n{len(results) - failed}/{len(results)} live checks passed")
sys.exit(1 if failed else 0)
