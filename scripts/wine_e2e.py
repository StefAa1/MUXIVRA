"""End-to-end check against the portable Windows bundle running under Wine.

Sets up an account, stores the deploy key, opens a terminal websocket to the
local sshd through the packaged Python and asserts command output comes back.
"""
import asyncio
import base64
import json
import pathlib
import sys

import httpx

BASE = "http://127.0.0.1:8899"
ROOT = pathlib.Path("/root/sshdeck")
HEADERS = {"x-requested-with": "sshdeck"}
PASSWORD = "wine-verify-password-1"


def setup() -> str:
    client = httpx.Client(base_url=BASE, timeout=25)
    status = client.get("/api/status").json()
    if status["initialized"]:
        res = client.post("/api/login", json={"password": PASSWORD})
        if res.status_code != 200:
            raise SystemExit(f"login failed: {res.status_code} {res.text}")
    else:
        res = client.post("/api/setup", json={"username": "admin", "password": PASSWORD})
        res.raise_for_status()
    token = client.cookies.get("sshdeck_session")
    if not token:
        raise SystemExit("no session cookie")

    creds = client.get("/api/credentials", headers=HEADERS).json()
    key_cred = next((c for c in creds if c["name"] == "win-deploy-key"), None)
    if not key_cred:
        key_cred = client.post(
            "/api/credentials",
            headers=HEADERS,
            json={
                "name": "win-deploy-key",
                "kind": "key",
                "username": "deploy",
                "secret": (ROOT / "testdata/deploy_key").read_text(),
            },
        ).json()

    hosts = client.get("/api/hosts", headers=HEADERS).json()
    if not any(h["label"] == "win-ssh" for h in hosts):
        client.post(
            "/api/hosts",
            headers=HEADERS,
            json={
                "label": "win-ssh",
                "protocol": "ssh",
                "hostname": "127.0.0.1",
                "port": 22,
                "username": "deploy",
                "credential_id": key_cred["id"],
                "group_name": "wine",
                "tags": "wine",
            },
        ).raise_for_status()
    host = next(h for h in client.get("/api/hosts", headers=HEADERS).json() if h["label"] == "win-ssh")
    return token, host["id"]


async def terminal_roundtrip(token: str, host_id: int) -> str:
    import websockets

    url = f"ws://127.0.0.1:8899/api/ws/terminal/{host_id}?cols=100&rows=30"
    kwargs = {"additional_headers": {"Cookie": f"sshdeck_session={token}"}}
    try:
        conn = websockets.connect(url, **kwargs)
    except TypeError:  # websockets < 14
        conn = websockets.connect(url, extra_headers={"Cookie": f"sshdeck_session={token}"})

    output = ""
    async with conn as ws:
        await ws.send(json.dumps({"type": "input", "data": "echo WINE-SSH-$(id -un)\r"}))
        deadline = asyncio.get_running_loop().time() + 25
        while asyncio.get_running_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=6)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == "error":
                raise SystemExit(f"server error: {msg}")
            if msg.get("type") == "data":
                output += base64.b64decode(msg["b64"]).decode("utf-8", "replace")
            if "WINE-SSH-deploy" in output:
                break
    return output


def main() -> int:
    token, host_id = setup()
    print(f"session ok, host id {host_id}")
    output = asyncio.run(terminal_roundtrip(token, host_id))
    print("--- terminal output (tail) ---")
    print(output[-400:])
    ok = "WINE-SSH-deploy" in output
    print("[wine-e2e]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
