"""Create/update a host on the running instance through the API (demo/verification helper).

  .venv/bin/python scripts/demo_host.py --label mock-serial --protocol serial \
      --serial-port "$(cat runtime/pty-name)"

Prints the session cookie so a browser run can reuse it.
"""
import argparse
import json
import pathlib

import httpx

ROOT = pathlib.Path("/root/sshdeck")
HEADERS = {"x-requested-with": "sshdeck"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--protocol", default="ssh", choices=["ssh", "telnet", "serial"])
    ap.add_argument("--hostname", default="")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--username", default="")
    ap.add_argument("--serial-port", default="")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--group", default="devices")
    ap.add_argument("--tags", default="demo")
    args = ap.parse_args()

    password = (ROOT / "runtime/master.pass").read_text().strip()
    client = httpx.Client(base_url="http://127.0.0.1:8722", timeout=25, headers=HEADERS)
    client.post("/api/login", json={"password": password}).raise_for_status()
    token = client.cookies.get("sshdeck_session")

    payload = {
        "label": args.label,
        "protocol": args.protocol,
        "hostname": args.hostname,
        "port": args.port,
        "username": args.username,
        "group_name": args.group,
        "tags": args.tags,
        "serial_port": args.serial_port,
        "baud": args.baud,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "color": "#7c9cff" if args.protocol == "serial" else "#ffc75f",
        "notes": "demo host",
    }
    existing = {h["label"]: h for h in client.get("/api/hosts").json()}
    if args.label in existing:
        res = client.put(f"/api/hosts/{existing[args.label]['id']}", json=payload)
    else:
        res = client.post("/api/hosts", json=payload)
    res.raise_for_status()
    print(json.dumps({"token": token, "host": res.json()["label"], "id": res.json()["id"],
                      "protocol": res.json()["protocol"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
