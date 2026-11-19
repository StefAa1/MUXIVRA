"""Single source of truth for device and port kinds.

The API models, the import/export validator, the vendor catalog and the
reconciliation worker all read from here. A kind that the API accepts can
therefore never be rejected by a backup restore — the class of bug that made
`ap`/`firewall` devices and `ethernet` ports un-restorable.
"""
from __future__ import annotations

DEVICE_KINDS = ("switch", "router", "firewall", "ap", "patch-panel", "server", "pdu", "other")
PORT_KINDS = ("rj45", "sfp", "fiber", "power", "other")

DEVICE_KIND_SET = set(DEVICE_KINDS)
PORT_KIND_SET = set(PORT_KINDS)

# Earlier builds wrote port kinds that are not part of the vocabulary. They are
# normalised on migration so old databases keep working without losing links.
LEGACY_PORT_KINDS = {
    "ethernet": "rj45",
    "copper": "rj45",
    "sfpplus": "sfp",
    "sfp+": "sfp",
    "optical": "fiber",
    "logical": "other",
}

LEGACY_DEVICE_KINDS = {
    "access-point": "ap",
    "wireless": "ap",
    "gateway": "router",
    "fw": "firewall",
}


def normalize_device_kind(value: str) -> str:
    kind = (value or "").strip().lower()
    kind = LEGACY_DEVICE_KINDS.get(kind, kind)
    return kind if kind in DEVICE_KIND_SET else "other"


def normalize_port_kind(value: str) -> str:
    kind = (value or "").strip().lower()
    kind = LEGACY_PORT_KINDS.get(kind, kind)
    return kind if kind in PORT_KIND_SET else "other"
