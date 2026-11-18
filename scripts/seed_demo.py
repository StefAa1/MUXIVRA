"""Seed the running SSHDeck instance with a real local target for verification.

Uses only the public HTTP API (same calls the browser makes)."""
import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8722"
ROOT = Path(__file__).resolve().parent.parent
HEADERS = {"x-requested-with": "sshdeck"}

password = (ROOT / "runtime/master.pass").read_text().strip()
deploy_pass = (ROOT / "runtime/deploy.pass").read_text().strip()
deploy_key = (ROOT / "testdata/deploy_key").read_text()

client = httpx.Client(base_url=BASE, timeout=30)

status = client.get("/api/status").json()
if status["initialized"]:
    res = client.post("/api/login", json={"password": password})
else:
    res = client.post("/api/setup", json={"username": "admin", "password": password})
res.raise_for_status()

token = client.cookies.get("sshdeck_session")
assert token, "no session cookie issued"

key_cred = client.post(
    "/api/credentials",
    headers=HEADERS,
    json={"name": "deploy-key", "kind": "key", "username": "deploy", "secret": deploy_key},
)
key_cred.raise_for_status()
key_id = key_cred.json()["id"]

pass_cred = client.post(
    "/api/credentials",
    headers=HEADERS,
    json={"name": "deploy-password", "kind": "password", "username": "deploy", "secret": deploy_pass},
)
pass_cred.raise_for_status()
pass_id = pass_cred.json()["id"]

hosts = [
    {"label": "hermes-agent", "hostname": "127.0.0.1", "port": 22, "username": "deploy",
     "credential_id": key_id, "group_name": "homelab", "tags": "local,key", "color": "#3ddc97",
     "notes": "Local test target (key auth)"},
    {"label": "hermes-agent/pw", "hostname": "127.0.0.1", "port": 22, "username": "deploy",
     "credential_id": pass_id, "group_name": "homelab", "tags": "local,password", "color": "#7c9cff",
     "notes": "Same host, password auth path"},
]
host_ids = []
for payload in hosts:
    res = client.post("/api/hosts", headers=HEADERS, json=payload)
    res.raise_for_status()
    host_ids.append(res.json()["id"])

for label, command in [
    ("Disk usage", "df -h /"),
    ("Memory", "free -m"),
    ("Top CPU", "ps aux --sort=-%cpu | head -5"),
    ("Authorized keys", "cat ~/.ssh/authorized_keys | cut -c1-40"),
]:
    client.post("/api/snippets", headers=HEADERS, json={"label": label, "command": command}).raise_for_status()

imported = client.post(
    "/api/import/ssh-config",
    headers=HEADERS,
    json={"path": "/root/.ssh/config", "credential_id": key_id, "group_name": "imported"},
)
print(json.dumps({
    "token": token,
    "host_ids": host_ids,
    "key_cred": key_id,
    "pass_cred": pass_id,
    "import": imported.json() if imported.status_code == 200 else imported.text,
}, indent=2))

sys.exit(0)
