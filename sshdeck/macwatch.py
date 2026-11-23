"""MAC-table discovery: τι είναι συνδεδεμένο σε κάθε φυσική θύρα.

Διαβάζει το forwarding table του switch (ίδιο CLI που δίνει ο τεχνικός),
χαρτογραφεί κάθε MAC σε θύρα και προτείνει τύπο συσκευής από το OUI.

Αρχές:
  * read-only — δεν γράφει τίποτα στη συσκευή και δεν δημιουργεί καλώδια·
  * κάθε πρόταση φέρει ``confidence`` και ``source``, ώστε το UI να δείχνει
    «πιθανό» και ο χρήστης να επιβεβαιώνει·
  * ό,τι δεν αναγνωρίζεται μένει ``unknown`` αντί να μαντευτεί.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# --------------------------------------------------------------- vendor hints
# Μικρό, συντηρήσιμο σύνολο: μόνο OUIs που χρησιμοποιούνται εδώ σε switches,
# τηλέφωνα, κάμερες και APs. Επεκτείνεται από το data/oui.json (χωρίς rebuild).
DEFAULT_OUI: dict[str, dict[str, str]] = {
    "00:0B:82": {"vendor": "Grandstream", "kind": "phone"},
    "00:15:65": {"vendor": "Yealink", "kind": "phone"},
    "80:5E:C0": {"vendor": "Yealink", "kind": "phone"},
    "00:04:13": {"vendor": "Snom", "kind": "phone"},
    "00:04:F2": {"vendor": "Polycom", "kind": "phone"},
    "0C:38:3E": {"vendor": "Fanvil", "kind": "phone"},
    "44:19:B6": {"vendor": "Hikvision", "kind": "camera"},
    "C0:56:E3": {"vendor": "Hikvision", "kind": "camera"},
    "BC:AD:28": {"vendor": "Hikvision", "kind": "camera"},
    "28:57:BE": {"vendor": "Hikvision", "kind": "camera"},
    "3C:EF:8C": {"vendor": "Dahua", "kind": "camera"},
    "4C:11:BF": {"vendor": "Dahua", "kind": "camera"},
    "E0:50:8B": {"vendor": "Dahua", "kind": "camera"},
    "00:40:8C": {"vendor": "Axis", "kind": "camera"},
    "AC:CC:8E": {"vendor": "Axis", "kind": "camera"},
    "B8:A4:4F": {"vendor": "Axis", "kind": "camera"},
    "24:5A:4C": {"vendor": "Ubiquiti", "kind": "ap"},
    "78:8A:20": {"vendor": "Ubiquiti", "kind": "ap"},
    "F0:9F:C2": {"vendor": "Ubiquiti", "kind": "ap"},
    "04:18:D6": {"vendor": "Ubiquiti", "kind": "ap"},
    "68:D7:9A": {"vendor": "Ubiquiti", "kind": "ap"},
    "00:0B:86": {"vendor": "Aruba", "kind": "switch"},
    "6C:F3:7F": {"vendor": "Aruba", "kind": "switch"},
    "94:BF:95": {"vendor": "Aruba", "kind": "switch"},
    "20:4C:03": {"vendor": "Aruba", "kind": "switch"},
    "4C:5E:0C": {"vendor": "MikroTik", "kind": "switch"},
    "CC:2D:E0": {"vendor": "MikroTik", "kind": "switch"},
    "6C:3B:6B": {"vendor": "MikroTik", "kind": "switch"},
    "DC:2C:6E": {"vendor": "MikroTik", "kind": "switch"},
    "2C:C8:1B": {"vendor": "MikroTik", "kind": "switch"},
    "48:8F:5A": {"vendor": "MikroTik", "kind": "switch"},
    "00:00:0C": {"vendor": "Cisco", "kind": "switch"},
    "00:1B:54": {"vendor": "Cisco", "kind": "switch"},
    "00:1F:29": {"vendor": "HP", "kind": "server"},
    "3C:D9:2B": {"vendor": "HP", "kind": "printer"},
    "00:14:22": {"vendor": "Dell", "kind": "server"},
    "00:1B:21": {"vendor": "Intel", "kind": "pc"},
}

MAC_RE = re.compile(
    r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}"        # aa:bb:cc:dd:ee:ff / aa-bb-...
    r"|(?:[0-9a-f]{4}[.-]){2}[0-9a-f]{4}",      # aabb.ccdd.eeff / aabb-ccdd-eeff
    re.IGNORECASE)


def normalize_mac(text: str) -> str:
    """Ένα format παντού: ``aa:bb:cc:dd:ee:ff`` (lowercase)."""
    hexes = re.sub(r"[^0-9a-f]", "", str(text or "").lower())
    if len(hexes) != 12:
        return ""
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2))


def normalize_port(name: str) -> str:
    """Φέρνει ονόματα θυρών σε κοινή μορφή, ώστε να ταιριάζουν με τις θύρες
    του inventory: ``Gi1/0/5`` → ``gi1/0/5``, ``ethernet 1/1/3`` → ``1/1/3``."""
    text = str(name or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    aliases = [
        ("gigabitethernet", "gi"), ("fastethernet", "fa"), ("tengigabitethernet", "te"),
        ("ethernet", ""), ("ether", "ether"), ("port-channel", "po"),
    ]
    for long, short in aliases:
        if text.startswith(long):
            text = short + text[len(long):]
            break
    text = text.replace("ethernet", "")
    return text


def oui_of(mac: str) -> str:
    mac = normalize_mac(mac)
    return mac[:8].upper() if mac else ""


def load_oui(path: Path | str | None = None) -> dict[str, dict[str, str]]:
    table = {k.upper(): dict(v) for k, v in DEFAULT_OUI.items()}
    if path:
        try:
            extra = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return table
        if isinstance(extra, dict):
            for prefix, info in extra.items():
                if isinstance(info, dict):
                    table[str(prefix).upper().replace("-", ":")] = {
                        "vendor": str(info.get("vendor") or "unknown"),
                        "kind": str(info.get("kind") or "other"),
                    }
    return table


def guess(mac: str, table: dict[str, dict[str, str]]) -> dict[str, str]:
    prefix = oui_of(mac)
    hit = table.get(prefix)
    if not hit:
        return {"vendor": "", "kind": "unknown", "confidence": "none", "source": "oui"}
    return {"vendor": hit["vendor"], "kind": hit["kind"],
            "confidence": "probable", "source": "oui"}


# ------------------------------------------------------------------- parsers
def parse_mikrotik(text: str) -> list[dict[str, str]]:
    """RouterOS: τόσο ``print detail`` (key=value) όσο και η columnar μορφή."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        mac = MAC_RE.search(line)
        if not mac:
            continue
        iface = re.search(r"(?:on-)?interface=([^\s]+)", line)
        bridge = re.search(r"bridge=([^\s]+)", line)
        vlan = re.search(r"vlan(?:-id)?=(\d+)", line)
        flags = re.search(r"flags=([A-Z,]*)", line)
        dynamic = bool(flags and "D" in flags.group(1))
        port = ""
        if iface:
            port = iface.group(1)
        else:
            # columnar: MAC  VID  ON-INTERFACE  BRIDGE
            tail = [t for t in line[mac.end():].split() if t]
            if tail and tail[0].isdigit():
                vlan = vlan or re.match(r"(\d+)", tail[0])
                tail = tail[1:]
            if tail:
                port = tail[0]
            if len(tail) > 1 and not bridge:
                bridge = re.match(r"([^\s]+)", tail[1])
        rows.append({
            "mac": normalize_mac(mac.group(0)),
            "port": normalize_port(port),
            "vlan": vlan.group(1) if vlan else "",
            "bridge": bridge.group(1) if bridge and hasattr(bridge, "group") else "",
            "dynamic": dynamic,
        })
    return rows


def parse_aruba(text: str) -> list[dict[str, str]]:
    """Aruba/ProCurve ``show mac-address`` ή ``show mac-address-table``."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        mac = MAC_RE.search(line)
        if not mac:
            continue
        rest = line[mac.end():].strip()
        parts = rest.split()
        if not parts:
            continue
        # μορφές: "<mac> <port> ..." ή "<vlan> <mac> <type> <port>"
        port = ""
        port_re = re.compile(r"^(?:\d+/){1,2}\d+$|^[A-Za-z]{1,4}\d+(?:/\d+)*$|^\d+$")
        for token in parts:
            if port_re.match(token) and not re.fullmatch(r"(?i)(dynamic|static|learned|self)", token):
                # το VLAN έρχεται πριν το port· προτίμησε τη μορφή x/y[/z]
                if "/" in token or re.match(r"^[A-Za-z]", token):
                    port = token
                    break
        if not port:
            for token in parts:
                if port_re.match(token):
                    port = token
                    break
        vlan = ""
        head = line[:mac.start()].strip().split()
        if head and head[0].isdigit():
            vlan = head[0]
        rows.append({
            "mac": normalize_mac(mac.group(0)),
            "port": normalize_port(port),
            "vlan": vlan,
            "bridge": "",
            "dynamic": "dynamic" in line.lower(),
        })
    return rows


def parse_cisco(text: str) -> list[dict[str, str]]:
    """``show mac address-table``: vlan, mac (xxxx.xxxx.xxxx), type, port."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        mac = MAC_RE.search(line)
        if not mac:
            continue
        head = line[:mac.start()].split()
        tail = line[mac.end():].split()
        if not tail:
            continue
        port = tail[-1] if len(tail) >= 2 else tail[0]
        kind = next((t for t in tail if t.lower() in {"dynamic", "static", "secure"}), "")
        rows.append({
            "mac": normalize_mac(mac.group(0)),
            "port": normalize_port(port),
            "vlan": head[0] if head and head[0].isdigit() else "",
            "bridge": "",
            "dynamic": kind.lower() == "dynamic",
        })
    return rows


PARSERS = {
    "mikrotik": parse_mikrotik,
    "aruba": parse_aruba,
    "cisco": parse_cisco,
}

COMMANDS = {
    "mikrotik": "/interface bridge host print detail without-paging",
    "aruba": "show mac-address",
    "cisco": "show mac address-table",
}


def run(target, vendor: str, runner=None, timeout: int = 25) -> str:
    """Εκτελεί ΜΟΝΟ την read εντολή του forwarding table (ποτέ write)."""
    command = COMMANDS.get((vendor or "").lower())
    if not command:
        raise ValueError(f"unsupported_vendor: {vendor}")
    if runner is None:
        from . import workspace
        runner = workspace._ssh_runner(target, timeout)
    return str(runner(command) or "")


def parse(text: str, vendor: str) -> list[dict[str, str]]:
    parser = PARSERS.get((vendor or "").lower())
    if not parser:
        # δοκίμασε όλους τους parsers· κρατά τον πρώτο που βγάζει γραμμές
        for candidate in PARSERS.values():
            rows = candidate(text)
            if rows:
                return rows
        return []
    return parser(text)


# ------------------------------------------------------------------- analysis
def analyze(rows: list[dict[str, str]], device_ports: list[str],
            oui: dict[str, dict[str, str]] | None = None,
            known_macs: dict[str, str] | None = None) -> dict:
    """Ποιος είναι πίσω από κάθε θύρα, με σαφή confidence.

    ``known_macs``: MAC → όνομα συσκευής που δήλωσε ο χρήστης· κερδίζει πάντα
    από το OUI, γιατί είναι δική του γνώση.
    """
    table = oui or load_oui()
    known = {normalize_mac(k): v for k, v in (known_macs or {}).items()}
    ports = {normalize_port(p) for p in device_ports or []}

    by_port: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("mac"):
            continue
        mac = row["mac"]
        info = guess(mac, table)
        if mac in known:
            info = {"vendor": known[mac], "kind": info["kind"],
                    "confidence": "confirmed", "source": "user"}
        entry = {
            "mac": mac,
            **info,
            "vlan": row.get("vlan", ""),
            "learned": bool(row.get("dynamic")),
            "port_known": (normalize_port(row.get("port", "")) in ports) if ports else None,
        }
        by_port.setdefault(normalize_port(row.get("port", "")), []).append(entry)

    seen: list[dict] = []
    for port in sorted(by_port):
        entries = sorted(by_port[port], key=lambda e: e["mac"])
        kinds = sorted({e["kind"] for e in entries if e["kind"] != "unknown"})
        seen.append({
            "port": port,
            "devices": entries,
            "count": len(entries),
            "hint": kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "unknown"),
            "multi": len(entries) > 1,      # τυπικό σε uplink/trunk ή πίσω από hub
        })
    return {
        "total_macs": len(rows),
        "ports_seen": len(seen),
        "ports": seen,
        "note": ("Ένα MAC ανά θύρα σημαίνει άκρο· πολλά MAC στην ίδια θύρα "
                 "σημαίνει uplink/trunk ή switch πίσω από τη θύρα."),
    }
