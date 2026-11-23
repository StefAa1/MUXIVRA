"""LLDP discovery: ποιος είναι συνδεδεμένος σε κάθε θύρα, από τα μάτια του switch.

Το MUXIVRA συνδέεται **μόνο με τον switch** (SSH/Telnet profile ή paste του
output) και διαβάζει το LLDP/CDP. Τα τηλέφωνα, οι κάμερες και τα APs δεν
χρειάζονται σύνδεση — αυτοδηλώνονται προς τον switch.

Κάθε γείτονας ταιριάζει με το inventory με τρία κλειδιά, με σαφή ισχύ:
 1. MAC (chassis ID) — ``device_mac``: βεβαιότητα
 2. management IP — ``devices.mgmt_ip``: ισχυρή
 3. system name — ``devices.name``: μόνο υπόδειξη
Αν δεν ταιριάξει τίποτα, γίνεται **πρόταση** (proposal) και περιμένει έγκριση·
ποτέ δεν δημιουργείται συσκευή χωρίς να το ξέρει ο χρήστης.
"""
from __future__ import annotations

import re

from .macwatch import normalize_mac, normalize_port

# ------------------------------------------------------------------- parsers
# Aruba (ProCurve/ArubaOS-Switch): «show lldp info remote-device»
#   Local Port  ChassisId  PortId  PortDescr  SystemName
#   1/1/3       00:0b:82:...  00:0b:82:...  GXP1625  GXP1625
def parse_aruba(text: str) -> list[dict]:
    """Πίνακας με Local Port, ChassisId, PortId, PortDescr, SystemName.

    Κάποιες εκδόσεις βάζουν ``|`` διαχωριστικά· τα αγνοούμε, οπότε το ίδιο
    parsing δουλεύει και με και χωρίς αυτά.
    """
    rows = []
    for raw in str(text or "").splitlines():
        line = raw.replace("|", " ").strip()
        if not line or line.startswith(("Local Port", "---", "Status", "LLDP")):
            continue
        tokens = line.split()
        if len(tokens) < 4 or not re.match(r"^\d+/\d+(?:/\d+)?$", tokens[0]):
            continue
        port, chassis, port_id = tokens[0], tokens[1], tokens[2]
        sysname = tokens[-1]
        descr = " ".join(tokens[3:-1])
        rows.append({
            "port": normalize_port(port),
            "mac": normalize_mac(chassis) if re.search(r"[0-9a-f]{2}[:-]", chassis, re.I) else "",
            "chassis": chassis,
            "remote_port": normalize_port(port_id),
            "sysname": sysname,
            "model": descr,
            "mgmt_ip": "",
            "vendor_hint": "",
            "protocol": "lldp",
        })
    return rows


# Cisco: «show lldp neighbors detail» — μπλοκ με Device ID / Port id / IP
CISCO_KEYS = {
    "device id": "sysname",
    "port id": "remote_port",
    "system name": "sysname",
    "system description": "model",
    "management address": "mgmt_ip",
    "ip address": "mgmt_ip",
    "local intf": "port",
    "local interface": "port",
    "chassis id": "chassis",
}


def parse_cisco(text: str) -> list[dict]:
    rows: list[dict] = []
    current: dict = {}

    def flush():
        if not current:
            return
        chassis = current.get("chassis", "")
        mac = normalize_mac(chassis) if re.search(r"[0-9a-f]{2}[:-]", chassis, re.I) else ""
        rows.append({
            "port": normalize_port(current.get("port", "")),
            "mac": mac,
            "chassis": chassis,
            "remote_port": normalize_port(current.get("remote_port", "")),
            "sysname": current.get("sysname", ""),
            "model": current.get("model", ""),
            "mgmt_ip": _first_ip(current.get("mgmt_ip", "")),
            "vendor_hint": "",
            "protocol": "lldp",
        })
        current.clear()

    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if re.match(r"^-+$", stripped):
            continue
        if ":" not in stripped:
            if "neighbor" in stripped.lower():
                flush()
            continue
        key, _, value = stripped.partition(":")
        field = CISCO_KEYS.get(key.strip().lower())
        if field:
            current.setdefault(field, value.strip())
    flush()
    return rows


# MikroTik: «/ip neighbor print detail» ή columnar «print»
MT_KEYS = {
    "address": "mgmt_ip",
    "mac-address": "mac",
    "identity": "sysname",
    "interface": "remote_port",
    "board": "model",
}


def parse_mikrotik(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        data = {k: v for k, v in re.findall(r"(\S+)=(\"[^\"]*\"|\S+)", line)}
        if not data:
            continue
        if "mac-address" not in data and "address" not in data:
            continue
        rows.append({
            "port": normalize_port(data.get("interface", "")),   # θύρα του ΤΟΠΙΚΟΥ switch
            "mac": normalize_mac(data.get("mac-address", "")),
            "chassis": data.get("mac-address", ""),
            "remote_port": "",
            "sysname": data.get("identity", "").strip('"'),
            "model": data.get("board", "").strip('"'),
            "mgmt_ip": _first_ip(data.get("address", "")),
            "vendor_hint": "",
            "protocol": "lldp",
        })
    return rows


def _first_ip(text: str) -> str:
    match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", str(text or ""))
    return match.group(1) if match else ""


PARSERS = {"aruba": parse_aruba, "cisco": parse_cisco, "mikrotik": parse_mikrotik}
COMMANDS = {
    "aruba": "show lldp info remote-device",
    "cisco": "show lldp neighbors detail",
    "mikrotik": "/ip neighbor print detail without-paging",
}


def parse(text: str, vendor: str) -> list[dict]:
    parser = PARSERS.get((vendor or "").lower())
    if parser:
        rows = parser(text)
        if rows:
            return rows
    for candidate in PARSERS.values():
        rows = candidate(text)
        if rows:
            return rows
    return []


def run(target, vendor: str, runner=None, timeout: int = 25) -> str:
    command = COMMANDS.get((vendor or "").lower())
    if not command:
        raise ValueError(f"unsupported_vendor: {vendor}")
    if runner is None:
        from . import workspace
        runner = workspace._ssh_runner(target, timeout)
    return str(runner(command) or "")


# ------------------------------------------------------------------ matching
def match(neighbors: list[dict], devices: list[dict], macs: dict[str, int],
          customer_devices: set[int] | None = None) -> dict:
    """Δέσε κάθε γείτονα με υπάρχον device — ή σήμανέ τον ως πρόταση.

    ``devices``: [{id, name, mgmt_ip, vendor, model, kind}]
    ``macs``:    {mac: device_id}   (πίνακας device_mac)
    """
    by_ip = {str(d.get("mgmt_ip") or "").strip(): d for d in devices if d.get("mgmt_ip")}
    by_name = {str(d.get("name") or "").strip().lower(): d for d in devices if d.get("name")}
    allowed = customer_devices if customer_devices is not None else {d["id"] for d in devices}

    resolved: list[dict] = []
    for row in neighbors:
        entry = dict(row)
        device = None
        confidence = "none"
        matched_by = ""

        if row.get("mac") and row["mac"] in macs and macs[row["mac"]] in allowed:
            device = next((d for d in devices if d["id"] == macs[row["mac"]]), None)
            if device:
                confidence, matched_by = "confirmed", "mac"
        if device is None and row.get("mgmt_ip") and row["mgmt_ip"] in by_ip:
            device = by_ip[row["mgmt_ip"]]
            if device["id"] in allowed:
                confidence, matched_by = "strong", "mgmt_ip"
            else:
                device = None
        if device is None and row.get("sysname"):
            candidate = by_name.get(row["sysname"].strip().lower())
            if candidate and candidate["id"] in allowed:
                device = candidate
                confidence, matched_by = "hint", "name"

        entry.update({
            "device_id": device["id"] if device else None,
            "device_name": device["name"] if device else "",
            "confidence": confidence,
            "matched_by": matched_by,
            "state": "matched" if device else "proposed",
        })
        resolved.append(entry)

    known = sum(1 for r in resolved if r["state"] == "matched")
    return {
        "neighbors": resolved,
        "matched": known,
        "proposed": len(resolved) - known,
        "note": ("Το LLDP διαβάζεται από τον switch· οι γείτονες δεν χρειάζονται "
                 "σύνδεση. Οι προτάσεις δεν γίνονται συσκευές χωρίς έγκριση."),
    }
