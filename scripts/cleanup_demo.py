"""Remove the temporary mock devices from the live inventory and hand the panel back."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path("/root/sshdeck")
sys.path.insert(0, str(ROOT))

from sshdeck import config, db  # noqa: E402

MOCK_LABELS = ("mock-telnet", "mock-serial")

db.init()
with db.db() as conn:
    removed = []
    for label in MOCK_LABELS:
        row = conn.execute("SELECT id FROM hosts WHERE label = ?", (label,)).fetchone()
        if row:
            conn.execute("DELETE FROM hosts WHERE id = ?", (row["id"],))
            removed.append(label)
    hosts = [r["label"] for r in conn.execute("SELECT label FROM hosts ORDER BY label")]
    creds = [r["name"] for r in conn.execute("SELECT name FROM credentials ORDER BY name")]
print(json.dumps({"removed": removed, "hosts_left": hosts, "credentials": creds}, ensure_ascii=False))

subprocess.run(
    [str(ROOT / ".venv/bin/python"), "-m", "sshdeck", "reset-account"],
    cwd=str(ROOT), check=True,
)
print(json.dumps({"sessions_left": sqlite3.connect(config.DB_PATH).execute("SELECT COUNT(*) FROM sessions").fetchone()[0]}))
