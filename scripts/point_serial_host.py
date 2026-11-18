"""Point the mock-serial host at the current PTY and print the state."""
import json
import pathlib

import httpx

pw = pathlib.Path("/root/sshdeck/runtime/master.pass").read_text().strip()
pty = pathlib.Path("/root/sshdeck/runtime/pty-name").read_text().strip()
client = httpx.Client(
    base_url="http://127.0.0.1:8722", timeout=20, headers={"x-requested-with": "sshdeck"}
)
client.post("/api/login", json={"password": pw}).raise_for_status()
hosts = {h["label"]: h for h in client.get("/api/hosts").json()}
host = hosts["mock-serial"]
spec = {
    "label": "mock-serial",
    "protocol": "serial",
    "serial_port": pty,
    "baud": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "hostname": "",
    "port": 22,
    "username": "",
    "group_name": "devices",
    "tags": "mock",
    "color": "#7c9cff",
    "notes": "Mock serial device on a PTY",
}
res = client.put(f"/api/hosts/{host['id']}", json=spec)
res.raise_for_status()
print(json.dumps({"id": host["id"], "serial_port": res.json()["serial_port"],
                  "ports": client.get("/api/serial/ports").json()}))
