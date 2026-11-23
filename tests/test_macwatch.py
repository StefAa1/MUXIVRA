"""MAC-table parsers: fixtures από πραγματικά CLI formats, χωρίς συσκευή."""
from sshdeck import macwatch


MIKROTIK = """Flags: D - DYNAMIC; I - INVALID
Columns: MAC-ADDRESS, VID, ON-INTERFACE, BRIDGE

 0  D  00:0B:82:11:22:33  10  ether3    bridge1
 1  D  44:19:B6:AA:BB:CC  10  ether4    bridge1
 2  D  00:0B:82:11:22:44  10  ether3    bridge1
"""

CISCO = """          Mac Address Table
-------------------------------------------
Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
  10    0011.2233.4455    DYNAMIC     Gi1/0/5
  10    0011.2233.4466    DYNAMIC     Gi1/0/5
  10    44bc.9a11.2233    DYNAMIC     Gi1/0/9
"""

ARUBA = """ Status and Counters - Port Address Table

  MAC Address       Port      VLAN
  ----------------  --------  ----
  000b-8211-2233    1/1/3     10    Dynamic
  4419-b6aa-bbcc    1/1/4     10    Dynamic
"""


def test_mikrotik_ports_and_vendors():
    rows = macwatch.parse(MIKROTIK, "mikrotik")
    assert len(rows) == 3
    assert rows[0]["mac"] == "00:0b:82:11:22:33"
    assert rows[0]["port"] == "ether3"
    assert rows[0]["vlan"] == "10"      # η columnar μορφή δείχνει VID

    res = macwatch.analyze(rows, ["ether3", "ether4"])
    by_port = {p["port"]: p for p in res["ports"]}
    assert by_port["ether3"]["count"] == 2
    assert by_port["ether3"]["multi"] is True
    assert by_port["ether3"]["hint"] == "phone"          # Grandstream
    assert by_port["ether4"]["devices"][0]["vendor"] == "Hikvision"
    assert by_port["ether4"]["devices"][0]["kind"] == "camera"


def test_cisco_short_port_names_normalize():
    rows = macwatch.parse(CISCO, "cisco")
    assert [r["port"] for r in rows] == ["gi1/0/5", "gi1/0/5", "gi1/0/9"]
    res = macwatch.analyze(rows, ["Gi1/0/5", "Gi1/0/9"])
    assert res["ports_seen"] == 2


def test_aruba_table_and_unknown_oui():
    rows = macwatch.parse(ARUBA, "aruba")
    assert rows[0]["port"] == "1/1/3"
    res = macwatch.analyze(rows, [])
    assert res["total_macs"] == 2
    assert res["ports"][0]["devices"][0]["confidence"] == "probable"


def test_user_known_mac_beats_oui():
    rows = macwatch.parse(MIKROTIK, "mikrotik")
    res = macwatch.analyze(rows, ["ether3"], known_macs={"00:0B:82:11:22:33": "Reception phone"})
    entry = [d for p in res["ports"] for d in p["devices"]
             if d["mac"] == "00:0b:82:11:22:33"][0]
    assert entry["vendor"] == "Reception phone"
    assert entry["confidence"] == "confirmed"


def test_unknown_vendor_is_not_invented():
    res = macwatch.analyze([{"mac": "02:00:00:00:00:01", "port": "ether9", "vlan": "",
                             "bridge": "", "dynamic": True}], [])
    assert res["ports"][0]["devices"][0]["confidence"] == "none"
    assert res["ports"][0]["hint"] == "unknown"


def test_run_uses_read_only_command_and_parses_through_api_shape(monkeypatch):
    from sshdeck import macwatch

    seen = {}

    def fake_runner(cmd):
        seen["cmd"] = cmd
        return MIKROTIK

    raw = macwatch.run(object(), "mikrotik", runner=fake_runner)
    assert seen["cmd"] == macwatch.COMMANDS["mikrotik"]
    assert "host print" in seen["cmd"] and "set" not in seen["cmd"]
    res = macwatch.analyze(macwatch.parse(raw, "mikrotik"), ["ether3"], known_macs={})
    assert res["ports"][0]["port"] == "ether3"


def test_unsupported_vendor_is_rejected():
    import pytest

    from sshdeck import macwatch

    with pytest.raises(ValueError):
        macwatch.run(object(), "huawei", runner=lambda c: "")
