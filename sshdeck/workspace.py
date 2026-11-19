"""Configuration workspace: read, version, diff, apply and reconcile supported
logical elements into the documented topology.

Rules that the rest of the app relies on:

* A configuration is only ever *read* unless the operator presses Apply with an
  exact fingerprint of the version they saw (``expected_sha256``). A stale
  version is refused instead of silently overwriting somebody's change.
* Nothing here invents physical cabling. Interfaces become ports, addresses
  become ``mgmt_ip``, VLANs become metadata — a cable is never derived from an
  IP address, a config or a node's neighbourhood.
* Vendors without an adapter keep the interactive terminal; they never get a
  fake structured editor.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass, field

from . import security

# --------------------------------------------------------------------- vendors
SECRET_LINE = re.compile(
    r"(password|passwd|secret|pre-shared-key|community|key-string|snmp-community)",
    re.IGNORECASE,
)


# ------------------------------------------------------------------- redaction
# Κάθε vendor γράφει τα μυστικά με δικό του τρόπο. Ό,τι δεν αναλύεται με
# βεβαιότητα κρύβεται ολόκληρο — ποτέ «μισή» γραμμή με ορατό secret.
def _mask(keyword: str, value: str) -> str:
    return f"{keyword} <redacted>"


# 1) key=value (RouterOS export, YAML-ish, generic CLIs), με quoted τιμές
KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|pre-shared-key|preshared-key|"
    r"private-key|authentication-password|community|key-string|snmp-community|"
    r"wpa-pre-shared-key|wpa2-pre-shared-key|radius-secret|tacacs-key)\b"
    r"(\s*=\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)

# 2) keyword value [extra...] — Cisco IOS / NX-OS / Aruba
SPACE_SECRET = [
    re.compile(r"(?i)^(\s*snmp-server\s+community\s+)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*(?:enable|username\s+\S+\s+privilege\s+\d+)\s+(?:secret|password)\s+(?:\d+\s+)?)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*key-string\s+(?:\d+\s+)?)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*key\s+(?:\d+\s+)?)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*(?:password|secret)\s+(?:\d+\s+)?)(\"[^\"]*\"|\S+)(.*)$"),
    re.compile(r"(?i)^(\s*crypto\s+isakmp\s+key\s+)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*neighbor\s+\S+\s+password\s+(?:\d+\s+)?)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*community\s+)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*authentication-key\s+)(\S+)(.*)$"),
    re.compile(r"(?i)^(\s*(?:tacacs-server|radius-server)\s+key\s+(?:\d+\s+)?)(\S+)(.*)$"),
]

SECRET_LINE = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|pre-shared-key|preshared-key|private-key|"
    r"key-string|community|authentication-key|authentication-password)\b"
)


def redact_line(line: str) -> str:
    """Mask the secret value(s) of one configuration line."""
    if SECRET_LINE.search(line):
        # escaped ή μη ισορροπημένα quotes: καμία αξιόπιστη ανάλυση → κρύψε τη γραμμή
        if '\\"' in line or line.count('"') % 2 == 1:
            indent = line[: len(line) - len(line.lstrip())]
            return f"{indent}<redacted line>"
    masked = KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", line)
    if masked != line:
        return masked
    for pattern in SPACE_SECRET:
        match = pattern.match(masked)
        if match:
            lead, _value, tail = match.group(1), match.group(2), match.group(3)
            return f"{lead}<redacted>{tail}"
    if SECRET_LINE.search(masked):
        # δεν αναλύεται με βεβαιότητα: κρύψε ολόκληρη τη γραμμή
        indent = masked[: len(masked) - len(masked.lstrip())]
        return f"{indent}<redacted line>"
    return masked


def redact(text: str) -> str:
    """Mask secret values in configuration text, keeping the rest readable."""
    return "\n".join(redact_line(line) for line in (text or "").splitlines())


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


@dataclass
class Adapter:
    name: str
    label: str
    read_commands: list[str]
    apply_prefix: list[str] = field(default_factory=list)   # e.g. ["/"]
    supported: tuple[str, ...] = ()


ADAPTERS: dict[str, Adapter] = {
    "mikrotik": Adapter(
        name="mikrotik",
        label="MikroTik RouterOS",
        read_commands=["/export terse show-sensitive=no"],
        apply_prefix=[],
        supported=("interfaces", "addresses", "vlans", "bridges"),
    ),
    "cisco": Adapter(
        name="cisco",
        label="Cisco IOS",
        read_commands=["terminal length 0", "show running-config"],
        apply_prefix=["configure terminal"],
        supported=("interfaces", "addresses", "vlans"),
    ),
    "generic": Adapter(name="generic", label="Generic (raw only)", read_commands=[], supported=()),
}


def adapter_for(vendor: str) -> Adapter:
    key = (vendor or "").strip().lower()
    if "mikrotik" in key or "routeros" in key:
        return ADAPTERS["mikrotik"]
    if "cisco" in key or "ios" in key:
        return ADAPTERS["cisco"]
    return ADAPTERS["generic"]


# --------------------------------------------------------------------- parsing
def parse_mikrotik(text: str) -> dict:
    """Interfaces, addresses, VLANs and bridges from ``/export terse``."""
    data = {"interfaces": [], "addresses": [], "vlans": [], "bridges": []}
    for line in text.splitlines():
        line = line.strip()
        name = re.search(r"add (?:name=(\S+))", line)
        if "/interface ethernet" in line or "/interface bridge add" in line:
            item = {"name": name.group(1) if name else ""}
            if "comment=" in line:
                item["comment"] = line.split("comment=", 1)[1].strip('"')
            if item["name"]:
                data["interfaces" if "bridge add" not in line else "bridges"].append(item)
        elif "/ip address add" in line:
            addr = re.search(r"address=(\S+)", line)
            iface = re.search(r"interface=(\S+)", line)
            if addr:
                data["addresses"].append({
                    "address": addr.group(1),
                    "interface": iface.group(1) if iface else "",
                })
        elif "/interface vlan add" in line:
            vid = re.search(r"vlan-id=(\d+)", line)
            vname = re.search(r"name=(\S+)", line)
            if vid:
                data["vlans"].append({"id": int(vid.group(1)), "name": vname.group(1) if vname else ""})
    return data


def parse_cisco(text: str) -> dict:
    """Interfaces, addresses and VLANs from ``show running-config``."""
    data = {"interfaces": [], "addresses": [], "vlans": [], "bridges": []}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^interface (\S+)", line)
        if m:
            current = m.group(1)
            data["interfaces"].append({"name": current, "description": ""})
            continue
        if current:
            desc = re.match(r"^\s+description (.+)$", line)
            if desc:
                data["interfaces"][-1]["description"] = desc.group(1).strip()
            ip = re.match(r"^\s+ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)", line)
            if ip:
                data["addresses"].append({
                    "address": f"{ip.group(1)}/{ip.group(2)}", "interface": current,
                })
            if line.startswith("!"):
                current = None
        vlan = re.match(r"^vlan (\d+)$", line.strip())
        if vlan:
            data["vlans"].append({"id": int(vlan.group(1)), "name": ""})
    return data


PARSERS = {"mikrotik": parse_mikrotik, "cisco": parse_cisco}


def parse(vendor: str, text: str) -> dict:
    adapter = adapter_for(vendor)
    parser = PARSERS.get(adapter.name)
    return parser(text) if parser else {"interfaces": [], "addresses": [], "vlans": [], "bridges": []}


# ------------------------------------------------------------------- retrieval
class ConfigError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def read_commands(vendor: str) -> list[str]:
    adapter = adapter_for(vendor)
    if not adapter.read_commands:
        raise ConfigError(
            "vendor_not_supported",
            "No adapter for this vendor — use the terminal.",
        )
    return list(adapter.read_commands)


def fetch(target, vendor: str, runner=None, timeout: int = 25) -> str:
    """Run the adapter's read commands. ``runner(cmd) -> str`` makes this testable
    against fixtures without a real device."""
    commands = read_commands(vendor)
    if runner is None:
        runner = _ssh_runner(target, timeout)
    chunks = []
    for command in commands:
        output = runner(command)
        chunks.append(str(output or ""))
    return "\n".join(chunks).strip() + "\n"


def _ssh_runner(target, timeout: int):
    def run(command: str) -> str:
        from . import sshconn

        client = sshconn.connect(target, timeout=timeout)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            return stdout.read().decode("utf-8", "replace") or stderr.read().decode("utf-8", "replace")
        finally:
            client.close()

    return run


# -------------------------------------------------------------------- versions
def store_version(conn, device_id: int, host_id: int | None, vendor: str, raw: str,
                  source: str = "ssh", label: str = "") -> dict:
    now = time.time()
    digest = sha256_of(raw)
    cur = conn.execute(
        "INSERT INTO config_versions(device_id, host_id, vendor, source, raw_enc, sha256, size,"
        " label, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, host_id, vendor, source, security.seal(raw), digest, len(raw),
         label[:120], now),
    )
    conn.commit()
    return {"id": cur.lastrowid, "sha256": digest, "size": len(raw), "created_at": now,
            "vendor": vendor, "source": source, "label": label}


def list_versions(conn, device_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, vendor, source, sha256, size, label, created_at FROM config_versions"
        " WHERE device_id = ? ORDER BY id DESC LIMIT ?", (device_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def version_text(conn, version_id: int) -> str:
    row = conn.execute("SELECT raw_enc FROM config_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise ConfigError("not_found", "Unknown configuration version.")
    return security.open_sealed(row["raw_enc"]) or ""


def diff(conn, left_id: int, right_id: int, context: int = 3) -> dict:
    # redaction ΠΡΙΝ το diff: αλλιώς το "+"/"-" σπάει το anchored matching
    left = redact(version_text(conn, left_id))
    right = redact(version_text(conn, right_id))
    lines = list(difflib.unified_diff(
        left.splitlines(), right.splitlines(),
        fromfile=f"version-{left_id}", tofile=f"version-{right_id}", lineterm="", n=context,
    ))
    return {
        "left": left_id, "right": right_id,
        "changed": bool(lines),
        "added": sum(1 for l in lines if l.startswith("+") and not l.startswith("+++")),
        "removed": sum(1 for l in lines if l.startswith("-") and not l.startswith("---")),
        "diff": "\n".join(lines),
    }


# ----------------------------------------------------------------------- apply
def apply(conn, device_id: int, text: str, expected_sha256: str, runner=None,
          confirm: bool = False) -> dict:
    """Apply only when the operator confirms AND the device still matches what
    they were looking at. Never reports success without reading the result back."""
    if not confirm:
        raise ConfigError("confirmation_required", "Applying requires explicit confirmation.")
    latest = conn.execute(
        "SELECT sha256 FROM config_versions WHERE device_id = ? ORDER BY id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    if not latest or latest["sha256"] != expected_sha256:
        raise ConfigError("stale_config", "The configuration changed since you loaded it.")
    commands = [line for line in text.splitlines() if line.strip()]
    if not commands:
        raise ConfigError("empty_apply", "No commands to apply.")
    if runner is None:
        raise ConfigError("no_session", "No active session to apply to.")
    results = []
    failed = None
    for command in commands:
        try:
            output = runner(command)
            results.append({"command": redact(command), "output": redact(str(output or ""))[:400]})
        except Exception as exc:                     # noqa: BLE001 - reported, not hidden
            # το exception μπορεί να περιέχει την ίδια την εντολή με το password
            failed = {"command": redact(command), "error": redact(str(exc))[:300]}
            break
    return {
        "applied": [r["command"] for r in results],   # already redacted
        "failed": failed,
        "partial": bool(failed and results),
        "output": results,
    }


# ------------------------------------------------------------------- reconcile
def upsert_observed(conn, device_id: int, host_id: int | None, vendor: str, parsed: dict) -> dict:
    conn.execute(
        "INSERT INTO device_observed(device_id, host_id, vendor, data_json, collected_at)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET"
        " host_id = excluded.host_id, vendor = excluded.vendor,"
        " data_json = excluded.data_json, collected_at = excluded.collected_at",
        (device_id, host_id, vendor, json.dumps(parsed, ensure_ascii=False), time.time()),
    )
    conn.commit()
    return observed(conn, device_id)


def observed(conn, device_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM device_observed WHERE device_id = ?", (device_id,)).fetchone()
    if not row:
        return None
    return {"vendor": row["vendor"], "collected_at": row["collected_at"],
            "data": json.loads(row["data_json"] or "{}")}


def reconcile(conn, device_id: int, apply_changes: bool = False) -> dict:
    """Compare documented state with what the device reported.

    Interfaces map to ports (added, never deleted silently), the first IPv4
    address maps to ``mgmt_ip`` when it is empty, VLANs are reported as
    metadata. Cables are never touched.
    """
    current = observed(conn, device_id)
    if not current:
        return {"device_id": device_id, "changed": False, "reason": "no_observation", "actions": []}
    device = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if not device:
        raise ConfigError("not_found", "Unknown device.")
    data = current["data"]
    documented_ports = {
        r["name"] for r in conn.execute("SELECT name FROM ports WHERE device_id = ?", (device_id,))
    }
    actions = []
    logical = list(data.get("interfaces", [])) + list(data.get("bridges", []))
    for iface in logical:
        name = (iface.get("name") or "").strip()
        if name and name not in documented_ports:
            actions.append({"kind": "port_missing", "port": name, "safe": True})
            if apply_changes:
                conn.execute(
                    "INSERT INTO ports(device_id, name, kind) VALUES (?, ?, 'rj45')",
                    (device_id, name[:40]),
                )
    address = (data.get("addresses") or [{}])[0].get("address", "")
    ip_only = address.split("/")[0] if address else ""
    if ip_only and not (device["mgmt_ip"] or "").strip():
        actions.append({"kind": "mgmt_ip", "value": ip_only, "safe": True})
        if apply_changes:
            conn.execute("UPDATE devices SET mgmt_ip = ? WHERE id = ?", (ip_only, device_id))
    documented_vlans = {
        r["name"] for r in conn.execute("SELECT name FROM ports WHERE device_id = ? AND kind = 'vlan'",
                                        (device_id,))
    }
    for vlan in data.get("vlans", []):
        label = vlan.get("name") or f"vlan{vlan.get('id')}"
        if label not in documented_vlans:
            actions.append({"kind": "vlan", "vlan": vlan.get("id"), "name": label, "safe": True})
    observed_names = {(i.get("name") or "").strip() for i in logical}
    for name in sorted(documented_ports - observed_names):
        actions.append({"kind": "port_not_observed", "port": name, "safe": False})
    if apply_changes:
        conn.commit()
    return {"device_id": device_id, "changed": bool(actions), "vendor": current["vendor"],
            "collected_at": current["collected_at"], "applied": apply_changes, "actions": actions}
