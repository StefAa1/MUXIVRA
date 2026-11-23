"""LLDP/CDP: parsers + ταίριασμα με το inventory (χωρίς πραγματική συσκευή)."""
from sshdeck import lldp

ARUBA = """LLDP Remote Devices Information

Local Port | ChassisId                 PortId                    PortDescr        SystemName
---------  + ------------------------- ------------------------- ---------------- --------------
1/1/3      | 00:0b:82:11:22:33         00:0b:82:11:22:33         IP Phone 1625    GXP1625
1/1/4      | 44:19:b6:aa:bb:cc         80                        IP Camera        IPC-HDW
1/1/48     | 6c:f3:7f:00:11:22         48                        Aruba Switch     SW-CORE
"""

CISCO = """------------------------------------------------
LLDP neighbor entries:
------------------------------------------------
Device ID: GXP1625
  Chassis id: 00:0b:82:11:22:33
  Port id: 00:0b:82:11:22:33
  Local Intf: GigabitEthernet1/0/5
  System Name: GXP1625
  System Description: Grandstream GXP1625
  Management Address: 10.10.5.31

Device ID: IPC-HDW
  Chassis id: 44:19:b6:aa:bb:cc
  Port id: 80
  Local Intf: GigabitEthernet1/0/9
  System Name: IPC-HDW
  Management Address: 10.10.5.40
"""

MIKROTIK = """ 0 interface=ether5 address=10.10.5.31 mac-address=00:0B:82:11:22:33 identity="GXP1625" board="GXP1625" """


def test_aruba_lldp_rows():
    rows = lldp.parse(ARUBA, "aruba")
    assert len(rows) == 3
    assert rows[0]["port"] == "1/1/3"
    assert rows[0]["mac"] == "00:0b:82:11:22:33"
    assert rows[0]["sysname"] == "GXP1625"
    assert rows[2]["port"] == "1/1/48"


def test_cisco_lldp_detail():
    rows = lldp.parse(CISCO, "cisco")
    assert len(rows) == 2
    assert rows[0]["port"] == "gi1/0/5"
    assert rows[0]["mgmt_ip"] == "10.10.5.31"
    assert rows[1]["mac"] == "44:19:b6:aa:bb:cc"


def test_mikrotik_neighbor():
    rows = lldp.parse(MIKROTIK, "mikrotik")
    assert rows[0]["mgmt_ip"] == "10.10.5.31"
    assert rows[0]["sysname"] == "GXP1625"


def test_match_by_mac_ip_and_name():
    devices = [
        {"id": 1, "name": "Phone 101", "mgmt_ip": "10.10.5.31", "vendor": "Grandstream", "model": "", "kind": "phone"},
        {"id": 2, "name": "Camera West", "mgmt_ip": "10.10.5.40", "vendor": "Hikvision", "model": "", "kind": "camera"},
    ]
    macs = {"00:0b:82:11:22:33": 1}
    res = lldp.match(lldp.parse(ARUBA, "aruba"), devices, macs)
    by_port = {n["port"]: n for n in res["neighbors"]}
    assert by_port["1/1/3"]["state"] == "matched"
    assert by_port["1/1/3"]["matched_by"] == "mac"
    assert by_port["1/1/4"]["device_id"] is None          # καμία IP/MAC → πρόταση
    assert by_port["1/1/4"]["state"] == "proposed"
    assert res["matched"] == 1 and res["proposed"] == 2


def test_match_by_ip_when_mac_unknown():
    devices = [{"id": 2, "name": "Camera West", "mgmt_ip": "10.10.5.40", "vendor": "Hikvision",
                "model": "", "kind": "camera"}]
    res = lldp.match(lldp.parse(CISCO, "cisco"), devices, {})
    cam = [n for n in res["neighbors"] if n["port"] == "gi1/0/9"][0]
    assert cam["matched_by"] == "mgmt_ip" and cam["confidence"] == "strong"


def test_other_customer_device_never_matches():
    devices = [{"id": 2, "name": "Camera West", "mgmt_ip": "10.10.5.40", "vendor": "", "model": "", "kind": "camera"}]
    res = lldp.match(lldp.parse(CISCO, "cisco"), devices, {}, customer_devices=set())
    assert all(n["state"] == "proposed" for n in res["neighbors"])
