"""Paramiko transport: TOFU host keys, shell channels and SFTP helpers."""
import io
import os
import socket
import stat
import threading
import time
from dataclasses import dataclass

import paramiko

from . import config, security


class SshError(RuntimeError):
    """Connection or authentication failure with a human readable code."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class HostKeyChanged(SshError):
    def __init__(self, message: str):
        super().__init__("host_key_changed", message)


@dataclass
class Target:
    host_id: int | None
    label: str
    hostname: str
    port: int
    username: str
    kind: str  # password | key | none-free
    secret: str = ""
    passphrase: str = ""
    protocol: str = "ssh"  # ssh | telnet | serial
    serial_port: str = ""
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0
    flow: str = "none"          # none | rtscts | xonxoff | dsrdtr
    customer_id: int | None = None  # whose trust store this connection uses

    def describe(self) -> str:
        if self.protocol == "serial":
            return f"{self.serial_port} @{self.baud}"
        if self.protocol == "telnet" or not self.username:
            return f"{self.hostname}:{self.port}"
        return f"{self.username}@{self.hostname}:{self.port}"


# --------------------------------------------------------------- host keys
def _ensure_known_hosts(customer_id: int | None = None) -> str:
    return config.ensure_known_hosts(customer_id)


class HostKeyUnknown(SshError):
    """Raised instead of silently trusting a key the user has never approved."""

    def __init__(self, hostname: str, fingerprint: str, key_type: str, host_id: int | None = None):
        super().__init__(
            "host_key_unknown",
            f"Unknown host key for {hostname} ({key_type}, {fingerprint}) — confirmation required.",
        )
        self.hostname = hostname
        self.fingerprint = fingerprint
        self.key_type = key_type
        self.host_id = host_id


class _PendingKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Refuse an unknown key and remember it, so the user can approve it explicitly."""

    def __init__(self, host_id: int | None = None, customer_id: int | None = None):
        self.host_id = host_id
        self.customer_id = customer_id
        self.key = None
        self.hostname = ""
        self.fingerprint = None

    def missing_host_key(self, client, hostname, key):
        self.key = key
        self.hostname = hostname
        self.fingerprint = security.fingerprint_of(key.asbytes())
        raise HostKeyUnknown(hostname, self.fingerprint, key.get_name(), self.host_id)


# unknown keys awaiting explicit approval: (host_id, fingerprint) -> (expiry, hostname, key)
_PENDING_KEYS: dict[tuple[int | None, str], tuple[float, str, object]] = {}
PENDING_TTL_SECONDS = 300


class TofuPolicy(paramiko.MissingHostKeyPolicy):
    """Opt-in auto-trust (used by scripts/tests); the API path uses _PendingKeyPolicy."""

    def __init__(self, host_id: int | None = None, customer_id: int | None = None):
        self.host_id = host_id
        self.customer_id = customer_id
        self.fingerprint: str | None = None

    def missing_host_key(self, client, hostname, key):
        store_host_key(hostname, key, self.host_id, self.customer_id)
        self.fingerprint = security.fingerprint_of(key.asbytes())


def store_host_key(hostname: str, key, host_id: int | None = None,
                   customer_id: int | None = None) -> str:
    """Persist one host key (0600) and log it. Returns the fingerprint."""
    path = _ensure_known_hosts(customer_id)
    keys = paramiko.HostKeys(path)
    keys.add(hostname, key.get_name(), key)
    keys.save(path)
    os.chmod(path, 0o600)
    fingerprint = security.fingerprint_of(key.asbytes())
    from . import db

    db.log_event(host_id, "host_key_trusted", f"{hostname} {fingerprint}")
    return fingerprint


def remember_pending_key(host_id: int | None, hostname: str, key) -> str:
    fingerprint = security.fingerprint_of(key.asbytes())
    _PENDING_KEYS[(host_id, fingerprint)] = (time.time() + PENDING_TTL_SECONDS, hostname, key)
    return fingerprint


def trust_pending_key(host_id: int | None, fingerprint: str,
                      customer_id: int | None = None) -> str:
    """Approve a key the user just saw, matching the exact fingerprint."""
    entry = _PENDING_KEYS.get((host_id, fingerprint))
    if not entry:
        raise SshError("no_pending_key", "That fingerprint is not pending — retry the connection.")
    expiry, hostname, key = entry
    if expiry < time.time():
        _PENDING_KEYS.pop((host_id, fingerprint), None)
        raise SshError("pending_key_expired", "The confirmation expired — try again.")
    stored = store_host_key(hostname, key, host_id, customer_id)
    _PENDING_KEYS.pop((host_id, fingerprint), None)
    return stored


def forget_host_key(hostname: str, port: int = 22, customer_id: int | None = None) -> bool:
    """Remove trust for exactly this host:port (other ports stay untouched)."""
    path = _ensure_known_hosts(customer_id)
    keys = paramiko.HostKeys(path)
    entries = [hostname] if port == 22 else [f"[{hostname}]:{port}"]
    removed = False
    for entry in entries:
        if keys.lookup(entry):
            del keys[entry]
            removed = True
    if removed:
        keys.save(path)
        os.chmod(path, 0o600)
    return removed


def known_fingerprint(hostname: str, port: int = 22,
                      customer_id: int | None = None) -> str | None:
    path = _ensure_known_hosts(customer_id)
    keys = paramiko.HostKeys(path)
    candidates = [hostname] if port == 22 else []
    candidates.append(f"[{hostname}]:{port}")
    for name in candidates:
        entry = keys.lookup(name)
        if entry:
            # paramiko >= 3.2 returns a mapping of key-type -> PKey
            for key in entry.values():
                return security.fingerprint_of(key.asbytes())
    return None


def forget_host_keys(hostname: str, port: int = 22) -> int:
    """Drop every known_hosts line that matches host:port. Returns lines removed."""
    path = _ensure_known_hosts()
    names = {hostname, f"[{hostname}]:{port}"}
    kept, removed = [], 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                kept.append(line)
                continue
            labels = set(stripped.split()[0].split(","))
            if labels & names:
                removed += 1
                continue
            kept.append(line)
    if removed:
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(kept)
        os.chmod(path, 0o600)
    return removed


# ------------------------------------------------------------------ connect
def _pkey(target: Target) -> paramiko.PKey:
    secret = target.secret.strip()
    passphrase = target.passphrase or None
    errors = []
    for loader in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return loader.from_private_key(io.StringIO(secret), password=passphrase)
        except Exception as exc:  # try the next key type
            errors.append(f"{loader.__name__}: {exc}")
    try:
        return paramiko.RSAKey.from_private_key(io.StringIO(secret), password=passphrase)
    except Exception as exc:
        errors.append(str(exc))
    raise SshError("bad_private_key", "The private key cannot be read: " + "; ".join(errors[:2]))


def connect(target: Target, timeout: int | None = None, trust_unknown: bool = False) -> paramiko.SSHClient:
    """Open an SSH client. Unknown host keys are refused unless trust_unknown."""
    client = paramiko.SSHClient()
    client.load_system_host_keys(_ensure_known_hosts(target.customer_id))
    if trust_unknown:
        policy = TofuPolicy(target.host_id, target.customer_id)
    else:
        policy = _PendingKeyPolicy(target.host_id, target.customer_id)
    client.set_missing_host_key_policy(policy)
    kwargs = dict(
        hostname=target.hostname,
        port=int(target.port or 22),
        username=target.username,
        timeout=timeout or config.CONNECT_TIMEOUT,
        banner_timeout=config.CONNECT_TIMEOUT,
        auth_timeout=config.CONNECT_TIMEOUT,
        # with no stored credential, offer the local ssh-agent (SSH_AUTH_SOCK);
        # Pageant on Windows is not supported
        allow_agent=target.kind == "none",
        look_for_keys=False,
    )
    if target.kind == "key":
        kwargs["pkey"] = _pkey(target)
    elif target.kind == "password":
        kwargs["password"] = target.secret
    try:
        client.connect(**kwargs)
    except paramiko.BadHostKeyException as exc:
        client.close()
        raise HostKeyChanged(
            "The host key changed since the first connection. Connection refused "
            f"(possible MITM). New fingerprint: {security.fingerprint_of(exc.key.asbytes())}"
        ) from exc
    except paramiko.AuthenticationException as exc:
        client.close()
        raise SshError("auth_failed", "Authentication failed — check the username/key.") from exc
    except (socket.timeout, TimeoutError) as exc:
        client.close()
        raise SshError("timeout", f"Timeout connecting to {target.hostname}:{target.port}") from exc
    except HostKeyUnknown as exc:
        client.close()
        remember_pending_key(target.host_id, exc.hostname, policy.key)
        raise
    except paramiko.SSHException as exc:
        client.close()
        raise SshError("ssh_error", str(exc)) from exc
    except OSError as exc:
        client.close()
        raise SshError("network", f"Connection failed: {exc}") from exc
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(config.KEEPALIVE_SECONDS)
    return client


# ------------------------------------------------------------------ sessions
class ShellSession:
    """Interactive shell channel with a background reader thread."""

    def __init__(self, target: Target, cols: int = 120, rows: int = 32):
        self.target = target
        self.client = connect(target)
        self.channel = self.client.invoke_shell(
            term=config.DEFAULT_TERM, width=max(20, cols), height=max(5, rows)
        )
        self.channel.settimeout(0.5)
        self._closed = threading.Event()
        self.on_data = None  # callable(bytes) -> None
        self.on_close = None  # callable()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)

    def start(self) -> None:
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                data = self.channel.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data:
                break
            if self.on_data:
                self.on_data(data)
        self.close()
        if self.on_close:
            self.on_close()

    def write(self, data: str) -> None:
        if self._closed.is_set():
            return
        try:
            self.channel.sendall(data.encode("utf-8"))
        except Exception:
            self.close()

    def resize(self, cols: int, rows: int) -> None:
        try:
            self.channel.resize_pty(width=max(20, cols), height=max(5, rows))
        except Exception:
            pass

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.channel.close()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass

    def describe(self) -> str:
        return f"{self.target.username}@{self.target.hostname}"

    @property
    def closed(self) -> bool:
        return self._closed.is_set()


# ---------------------------------------------------------------------- SFTP
def sftp(target: Target):
    client = connect(target)
    try:
        transport = client.get_transport()
        if transport is None:
            raise SshError("ssh_error", "no transport")
        transport.set_keepalive(config.KEEPALIVE_SECONDS)
        return client, transport.open_sftp_client()
    except SshError:
        client.close()
        raise
    except Exception as exc:
        client.close()
        raise SshError("sftp_unavailable", f"SFTP is not available: {exc}") from exc


def listdir(sftp_client, path: str) -> list[dict]:
    import posixpath

    path = path or "."
    entries = []
    for attr in sftp_client.listdir_attr(path):
        is_dir = stat.S_ISDIR(attr.st_mode or 0)
        entries.append(
            {
                "name": attr.filename,
                "path": posixpath.join(path, attr.filename) if path not in (".", "/") else ("/" + attr.filename if path == "/" else attr.filename),
                "is_dir": is_dir,
                "is_link": stat.S_ISLNK(attr.st_mode or 0),
                "size": attr.st_size or 0,
                "mtime": attr.st_mtime or 0,
                "mode": oct(attr.st_mode or 0)[-4:],
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def sftp_probe(target: Target) -> dict:
    """Cheap reachability check: TCP connect plus host key fingerprint."""
    started = time.time()
    sock = socket.create_connection((target.hostname, int(target.port or 22)), timeout=6)
    sock.close()
    return {
        "reachable": True,
        "latency_ms": int((time.time() - started) * 1000),
        "fingerprint": known_fingerprint(target.hostname, int(target.port or 22)),
    }
