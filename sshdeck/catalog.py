"""Hardware catalog: vendor → model → the ports the device really has.

Adding a device by model is the difference between "a switch" and a switch with
24 labelled RJ45 ports and 4 SFP+ uplinks. The catalog stores only what the
documentation needs: kind, rack units and port groups that produce real port
names per vendor convention (Gi0/1, ether1, 1, Port 1).

Nothing here talks to a device. The catalogue is local data; an observation can
later confirm or contradict it, and reconciliation never deletes ports because a
collection failed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PortGroup:
    prefix: str
    count: int
    kind: str = "rj45"
    start: int = 1
    sep: str = ""
    label: str = ""

    def names(self) -> list[str]:
        out = []
        for index in range(self.start, self.start + self.count):
            out.append(f"{self.prefix}{self.sep}{index}")
        return out


@dataclass(frozen=True)
class Model:
    key: str
    vendor: str
    name: str
    kind: str
    units: int = 1
    groups: tuple[PortGroup, ...] = field(default_factory=tuple)
    note: str = ""

    @property
    def port_count(self) -> int:
        return sum(group.count for group in self.groups)

    def ports(self) -> list[dict]:
        ports = []
        for group in self.groups:
            for name in group.names():
                ports.append({"name": name[:40], "kind": group.kind})
        return ports

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "vendor": self.vendor,
            "name": self.name,
            "kind": self.kind,
            "units": self.units,
            "ports": self.port_count,
            "groups": [
                {"prefix": g.prefix, "count": g.count, "kind": g.kind, "label": g.label}
                for g in self.groups
            ],
            "note": self.note,
        }


VENDORS = {
    "cisco": "Cisco",
    "mikrotik": "MikroTik",
    "aruba": "Aruba",
    "ubiquiti": "Ubiquiti",
}

MODELS: tuple[Model, ...] = (
    # ------------------------------------------------------------------ Cisco
    Model(
        "cisco-c9200-24p", "cisco", "Catalyst 9200-24P", "switch", 1,
        (PortGroup("Gi0/", 24, "rj45", 1), PortGroup("Gi0/", 4, "sfp", 25)),
        "24×1G PoE+ · 4×1G SFP uplinks",
    ),
    Model(
        "cisco-c9200-48p", "cisco", "Catalyst 9200-48P", "switch", 1,
        (PortGroup("Gi0/", 48, "rj45", 1), PortGroup("Gi0/", 4, "sfp", 49)),
        "48×1G PoE+ · 4×1G SFP uplinks",
    ),
    Model(
        "cisco-c1000-8t", "cisco", "Catalyst 1000-8T", "switch", 1,
        (PortGroup("Gi1/0/", 8, "rj45", 1),),
        "8×1G, fanless",
    ),
    Model(
        "cisco-c2960x-48", "cisco", "Catalyst 2960-X 48TS", "switch", 1,
        (PortGroup("Gi1/0/", 48, "rj45", 1), PortGroup("Te1/0/", 4, "sfp", 1)),
        "48×1G · 4×10G SFP+",
    ),
    Model(
        "cisco-isr4331", "cisco", "ISR 4331", "router", 1,
        (PortGroup("Gi0/0/", 2, "rj45", 0), PortGroup("Gi0/0/", 2, "sfp", 2)),
        "2×1G RJ45 · 2×1G SFP (WAN/LAN)",
    ),
    Model(
        "cisco-asa5506", "cisco", "ASA 5506-X", "firewall", 1,
        (PortGroup("Gi1/", 8, "rj45", 1),),
        "8×1G firewall ports",
    ),
    # --------------------------------------------------------------- MikroTik
    Model(
        "mikrotik-crs328-24p", "mikrotik", "CRS328-24P-4S+RM", "switch", 1,
        (PortGroup("ether", 24, "rj45", 1), PortGroup("sfp-sfpplus", 4, "sfp", 1)),
        "24×1G PoE-out · 4×10G SFP+",
    ),
    Model(
        "mikrotik-crs326-24g", "mikrotik", "CRS326-24G-2S+RM", "switch", 1,
        (PortGroup("ether", 24, "rj45", 1), PortGroup("sfp-sfpplus", 2, "sfp", 1)),
        "24×1G · 2×10G SFP+",
    ),
    Model(
        "mikrotik-hex-s", "mikrotik", "hEX S (RB760iGS)", "router", 1,
        (PortGroup("ether", 5, "rj45", 1), PortGroup("sfp", 1, "sfp", 1)),
        "5×1G · 1×SFP · PoE-in/out",
    ),
    Model(
        "mikrotik-rb4011", "mikrotik", "RB4011iGS+RM", "router", 1,
        (PortGroup("ether", 10, "rj45", 1), PortGroup("sfp-sfpplus", 1, "sfp", 1)),
        "10×1G · 1×10G SFP+",
    ),
    Model(
        "mikrotik-ccr2004", "mikrotik", "CCR2004-1G-12S+2XS", "router", 1,
        (PortGroup("ether", 1, "rj45", 1), PortGroup("sfp-sfpplus", 12, "sfp", 1)),
        "1×1G · 12×10G SFP+ · 2×25G",
    ),
    Model(
        "mikrotik-cap-ac", "mikrotik", "cAP ac", "ap", 1,
        (PortGroup("ether", 1, "rj45", 1),),
        "ceiling AP · 1×1G PoE-in",
    ),
    # ------------------------------------------------------------------ Aruba
    Model(
        "aruba-2930f-24g", "aruba", "2930F 24G 4SFP+", "switch", 1,
        (PortGroup("", 24, "rj45", 1), PortGroup("", 4, "sfp", 25)),
        "24×1G · 4×10G SFP+",
    ),
    Model(
        "aruba-2930f-48g", "aruba", "2930F 48G PoE+ 4SFP+", "switch", 1,
        (PortGroup("", 48, "rj45", 1), PortGroup("", 4, "sfp", 49)),
        "48×1G PoE+ · 4×10G SFP+",
    ),
    Model(
        "aruba-2530-8g", "aruba", "2530-8G", "switch", 1,
        (PortGroup("", 8, "rj45", 1),),
        "8×1G, fanless",
    ),
    Model(
        "aruba-ap515", "aruba", "AP-515", "ap", 1,
        (PortGroup("eth", 1, "rj45", 0),),
        "Wi-Fi 6 AP · 1×2.5G",
    ),
    Model(
        "aruba-ap315", "aruba", "AP-315", "ap", 1,
        (PortGroup("eth", 1, "rj45", 0),),
        "Wi-Fi 5 AP · 1×1G",
    ),
    # --------------------------------------------------------------- Ubiquiti
    Model(
        "ubiquiti-usw-24-poe", "ubiquiti", "UniFi Switch 24 PoE", "switch", 1,
        (PortGroup("Port ", 24, "rj45", 1), PortGroup("SFP+ ", 2, "sfp", 1)),
        "24×1G PoE · 2×10G SFP+",
    ),
    Model(
        "ubiquiti-usw-48", "ubiquiti", "UniFi Switch 48", "switch", 1,
        (PortGroup("Port ", 48, "rj45", 1), PortGroup("SFP+ ", 4, "sfp", 1)),
        "48×1G · 4×10G SFP+",
    ),
    Model(
        "ubiquiti-edgeswitch-24", "ubiquiti", "EdgeSwitch 24 250W", "switch", 1,
        (PortGroup("Port ", 24, "rj45", 1), PortGroup("SFP ", 2, "sfp", 1)),
        "24×1G PoE · 2×1G SFP",
    ),
    Model(
        "ubiquiti-udm-pro", "ubiquiti", "UniFi Dream Machine Pro", "router", 1,
        (PortGroup("Port ", 8, "rj45", 1), PortGroup("SFP+ ", 2, "sfp", 1)),
        "8×1G · 2×10G SFP+ · firewall/controller",
    ),
    Model(
        "ubiquiti-u6-pro", "ubiquiti", "UniFi U6-Pro", "ap", 1,
        (PortGroup("eth", 1, "rj45", 0),),
        "Wi-Fi 6 AP · 1×1G PoE",
    ),
    Model(
        "ubiquiti-uap-ac-lite", "ubiquiti", "UniFi AP AC Lite", "ap", 1,
        (PortGroup("eth", 1, "rj45", 0),),
        "Wi-Fi 5 AP · 1×1G PoE",
    ),
    # ------------------------------------------------- MikroTik (home / SMB line)
    Model(
        "mikrotik-hap-ax3", "mikrotik", "hAP ax³ (C53UiG+5HPaxD2HPaxD)", "router", 1,
        (PortGroup("ether", 4, "rj45", 2), PortGroup("ether", 1, "rj45", 1), PortGroup("sfp", 1, "sfp", 1)),
        "5×1G (1×2.5G PoE-in on ether1) · 1×2.5G SFP · Wi-Fi 6",
    ),
    Model(
        "mikrotik-hap-ax2", "mikrotik", "hAP ax² (C52iG-5HaxD2HaxD)", "router", 1,
        (PortGroup("ether", 5, "rj45", 1),),
        "5×1G · Wi-Fi 6, PoE-in on ether1",
    ),
    Model(
        "mikrotik-hap-ac2", "mikrotik", "hAP ac² (RBD52G-5HacD2HnD)", "router", 1,
        (PortGroup("ether", 5, "rj45", 1),),
        "5×1G · Wi-Fi 5, PoE-in on ether1",
    ),
    Model(
        "mikrotik-rb5009", "mikrotik", "RB5009UG+S+IN", "router", 1,
        (PortGroup("ether", 7, "rj45", 1), PortGroup("sfp-sfpplus", 1, "sfp", 1)),
        "7×1G · 1×10G SFP+ · PoE-in",
    ),
    Model(
        "mikrotik-crs112-8p", "mikrotik", "CRS112-8P-4S-IN", "switch", 1,
        (PortGroup("ether", 8, "rj45", 1), PortGroup("sfp", 4, "sfp", 1)),
        "8×1G PoE-out · 4×1G SFP",
    ),
    # ------------------------------------------------------- Cisco (small/edge)
    Model(
        "cisco-cbs350-24t", "cisco", "Catalyst CBS350-24T-4G", "switch", 1,
        (PortGroup("Gi1/0/", 24, "rj45", 1), PortGroup("Gi1/0/", 4, "sfp", 25)),
        "24×1G · 4×1G SFP (SMB line)",
    ),
    Model(
        "cisco-c9300-24p", "cisco", "Catalyst 9300-24P", "switch", 1,
        (PortGroup("Gi1/0/", 24, "rj45", 1), PortGroup("Te1/1/", 4, "sfp", 1)),
        "24×1G PoE+ · 4×10G SFP+ (stackable)",
    ),
    # --------------------------------------------------------- Aruba Instant On
    Model(
        "aruba-1930-24g", "aruba", "Instant On 1930 24G 4SFP/SFP+", "switch", 1,
        (PortGroup("", 24, "rj45", 1), PortGroup("", 4, "sfp", 25)),
        "24×1G · 4×10G SFP+ (SMB line)",
    ),
    Model(
        "aruba-1930-8g", "aruba", "Instant On 1930 8G 2SFP", "switch", 1,
        (PortGroup("", 8, "rj45", 1), PortGroup("", 2, "sfp", 9)),
        "8×1G · 2×1G SFP, fanless",
    ),
    # ------------------------------------------------------------- Ubiquiti Pro
    Model(
        "ubiquiti-usw-pro-24-poe", "ubiquiti", "UniFi Switch Pro 24 PoE", "switch", 1,
        (PortGroup("Port ", 24, "rj45", 1), PortGroup("SFP+ ", 2, "sfp", 1)),
        "24×1G PoE+ (16×PoE) · 2×10G SFP+",
    ),
    Model(
        "ubiquiti-udm-se", "ubiquiti", "UniFi Dream Machine SE", "router", 1,
        (PortGroup("Port ", 8, "rj45", 1), PortGroup("SFP+ ", 2, "sfp", 1)),
        "8×1G (2×PoE) · 1×2.5G · 2×10G SFP+",
    ),
    Model(
        "ubiquiti-u6-lr", "ubiquiti", "UniFi U6-LR", "ap", 1,
        (PortGroup("eth", 1, "rj45", 0),),
        "Wi-Fi 6 long-range AP · 1×1G PoE+",
    ),
    Model(
        "ubiquiti-usw-lite-8-poe", "ubiquiti", "UniFi Switch Lite 8 PoE", "switch", 1,
        (PortGroup("Port ", 8, "rj45", 1),),
        "8×1G (4×PoE) · fanless",
    ),
)

BY_KEY = {model.key: model for model in MODELS}


class CatalogError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def vendors() -> list[dict]:
    out = []
    for key, label in VENDORS.items():
        models = [m.as_dict() for m in MODELS if m.vendor == key]
        out.append({
            "key": key,
            "name": label,
            "models": models,
            "port_total": sum(m["ports"] for m in models),
        })
    return out


def get(key: str) -> Model:
    model = BY_KEY.get((key or "").strip())
    if not model:
        raise CatalogError("unknown_model", f"Unknown model: {key}")
    return model


def models_for(vendor: str) -> list[dict]:
    vendor = (vendor or "").strip().lower()
    return [m.as_dict() for m in MODELS if not vendor or m.vendor == vendor]


def create_ports(conn, device_id: int, model_key: str) -> int:
    """Give the device the model's real ports (idempotent: existing names stay)."""
    model = get(model_key)
    existing = {
        row["name"] for row in conn.execute(
            "SELECT name FROM ports WHERE device_id = ?", (device_id,)
        )
    }
    created = 0
    for port in model.ports():
        if port["name"] in existing:
            continue
        conn.execute(
            "INSERT INTO ports(device_id, name, kind) VALUES (?, ?, ?)",
            (device_id, port["name"], port["kind"]),
        )
        created += 1
    return created
