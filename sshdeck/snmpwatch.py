"""SNMP discovery: LLDP + MAC table χωρίς SSH login.

Χρήση όταν ο switch δεν δίνει SSH (ή ο πελάτης δεν το επιτρέπει): ένα
**read-only** community string αρκεί για να διαβαστούν

  * LLDP-MIB  → ποιος γείτονας είναι σε κάθε θύρα (όνομα, chassis, port id)
  * BRIDGE-MIB / Q-BRIDGE-MIB → ποια MAC μαθαίνονται σε κάθε θύρα

Τίποτα δεν γράφεται στη συσκευή. Το community ζει κρυπτογραφημένο στο vault
και δεν επιστρέφεται ποτέ στο UI/logs.
"""
from __future__ import annotations

import asyncio

from .macwatch import normalize_mac, normalize_port

# ------------------------------------------------------------------- OIDs
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"          # ifName.<ifIndex>
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"            # ifDescr.<ifIndex>

LLDP_CHASSIS = "1.0.8802.1.1.2.1.4.1.1.5"   # .<timeMark>.<localPortNum>.<index>
LLDP_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
LLDP_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
LLDP_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"

BRIDGE_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"  # .<6 bytes MAC> → bridge port
BRIDGE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
QBridge_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"   # .<vlan>.<6 bytes MAC>

WALK_OIDS = [IF_NAME, IF_DESCR, LLDP_CHASSIS, LLDP_PORT_ID, LLDP_PORT_DESC,
             LLDP_SYS_NAME, BRIDGE_FDB_PORT, BRIDGE_PORT_IFINDEX, QBridge_FDB_PORT]


class SnmpError(RuntimeError):
    """Λάθος/αδυναμία SNMP — το μήνυμα δεν περιέχει ποτέ το community."""


# ------------------------------------------------------------------- walking
async def _walk_async(host: str, community: str, oid: str, port: int = 161,
                      timeout: float = 2.0, retries: int = 1) -> dict[str, str]:
    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine,
            UdpTransportTarget, walk_cmd,
        )
    except ImportError as exc:                     # pragma: no cover - bundled wheel
        raise SnmpError("snmp_library_missing") from exc

    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create((host, port), timeout=timeout, retries=retries)
    except Exception as exc:                       # noqa: BLE001
        raise SnmpError(f"snmp_unreachable: {host}") from exc

    found: dict[str, str] = {}
    try:
        async for err_ind, err_stat, _err_idx, var_binds in walk_cmd(
                engine, CommunityData(community, mpModel=1), target, ContextData(),
                ObjectType(ObjectIdentity(oid)), lexicographicMode=False):
            if err_ind:
                raise SnmpError(f"snmp_error: {err_ind}")
            if err_stat:
                raise SnmpError(f"snmp_error: {err_stat.prettyPrint()}")
            for name, value in var_binds:
                found[str(name)] = str(value)
    except SnmpError:
        raise
    except Exception as exc:                       # noqa: BLE001 - timeout/auth
        raise SnmpError(f"snmp_failed: {type(exc).__name__}") from exc
    finally:
        try:
            engine.close_dispatcher()
        except Exception:                          # noqa: BLE001 - best effort
            pass
    return found


def walk(host: str, community: str, oid: str, **kwargs) -> dict[str, str]:
    return asyncio.run(_walk_async(host, community, oid, **kwargs))


def fetch(host: str, community: str, walker=None, **kwargs) -> dict[str, list[dict]]:
    """Διαβάζει LLDP + MAC tables και επιστρέφει έτοιμες γραμμές."""
    walker = walker or walk
    raw = {oid: walker(host, community, oid, **kwargs) for oid in WALK_OIDS}
    return {
        "lldp": build_lldp(raw),
        "macs": build_macs(raw),
    }


# --------------------------------------------------------------- row builders
def _suffix(oid: str, base: str) -> list[str]:
    return [p for p in oid[len(base):].lstrip(".").split(".") if p]


def _ifname_map(raw: dict[str, dict[str, str]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for oid, value in (raw.get(IF_NAME) or {}).items():
        parts = _suffix(oid, IF_NAME)
        if parts:
            names[parts[0]] = value
    for oid, value in (raw.get(IF_DESCR) or {}).items():
        parts = _suffix(oid, IF_DESCR)
        if parts and parts[0] not in names:
            names[parts[0]] = value
    return names


def build_lldp(raw: dict[str, dict[str, str]]) -> list[dict]:
    """Κάθε localPortNum → ifName, με τα πεδία του γείτονα."""
    names = _ifname_map(raw)
    rows: dict[tuple[str, str], dict] = {}
    for oid, value in (raw.get(LLDP_CHASSIS) or {}).items():
        parts = _suffix(oid, LLDP_CHASSIS)
        if len(parts) != 3:
            continue
        _time_mark, local_port, index = parts
        rows[(local_port, index)] = {
            "port": normalize_port(names.get(local_port, local_port)),
            "chassis": value,
            "mac": normalize_mac(value) if len(value.replace(":", "")) == 12 else "",
            "remote_port": "",
            "sysname": "",
            "model": "",
            "mgmt_ip": "",
            "vendor_hint": "",
            "protocol": "lldp-snmp",
        }
    for oid, value in (raw.get(LLDP_PORT_ID) or {}).items():
        parts = _suffix(oid, LLDP_PORT_ID)
        if len(parts) == 3 and (parts[1], parts[2]) in rows:
            rows[(parts[1], parts[2])]["remote_port"] = normalize_port(value)
    for oid, value in (raw.get(LLDP_SYS_NAME) or {}).items():
        parts = _suffix(oid, LLDP_SYS_NAME)
        if len(parts) == 3 and (parts[1], parts[2]) in rows:
            rows[(parts[1], parts[2])]["sysname"] = value
    for oid, value in (raw.get(LLDP_PORT_DESC) or {}).items():
        parts = _suffix(oid, LLDP_PORT_DESC)
        if len(parts) == 3 and (parts[1], parts[2]) in rows:
            rows[(parts[1], parts[2])]["model"] = value
    return [row for row in rows.values() if row["port"]]


def _oid_mac(parts: list[str]) -> str:
    if len(parts) < 6:
        return ""
    return normalize_mac("".join(f"{int(p):02x}" for p in parts[-6:]))


def build_macs(raw: dict[str, dict[str, str]]) -> list[dict]:
    """MAC → θύρα, μέσω bridge port → ifIndex → ifName."""
    names = _ifname_map(raw)
    port_to_if: dict[str, str] = {}
    for oid, value in (raw.get(BRIDGE_PORT_IFINDEX) or {}).items():
        parts = _suffix(oid, BRIDGE_PORT_IFINDEX)
        if parts:
            port_to_if[parts[0]] = str(value).strip()

    rows: dict[str, dict] = {}
    for oid, value in (raw.get(BRIDGE_FDB_PORT) or {}).items():
        mac = _oid_mac(_suffix(oid, BRIDGE_FDB_PORT))
        if not mac:
            continue
        if_index = port_to_if.get(str(value).strip(), "")
        rows[mac] = {
            "mac": mac,
            "port": normalize_port(names.get(if_index, if_index)),
            "vlan": "",
            "bridge": "",
            "dynamic": True,
        }
    for oid, value in (raw.get(QBridge_FDB_PORT) or {}).items():
        parts = _suffix(oid, QBridge_FDB_PORT)
        if len(parts) < 7:
            continue
        vlan = parts[0]
        mac = _oid_mac(parts[1:])
        if not mac:
            continue
        if_index = port_to_if.get(str(value).strip(), "")
        rows[mac] = {
            "mac": mac,
            "port": normalize_port(names.get(if_index, if_index)),
            "vlan": vlan,
            "bridge": "",
            "dynamic": True,
        }
    return [row for row in rows.values() if row["port"]]
