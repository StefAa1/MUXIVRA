#!/usr/bin/env python3
"""Έλεγχος του απομονωμένου παραθύρου web management με πραγματικό Qt WebEngine.

Τρέχει σε Xvfb (χωρίς οθόνη), σερβίρει μια τοπική fixture σελίδα, ανοίγει το
παράθυρο της συσκευής και επιβεβαιώνει ότι φόρτωσε — χωρίς browser και χωρίς
πρόσβαση στην εφαρμογή.

    python scripts/e2e_web_surface.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def serve(directory: str, port: int) -> HTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=directory)
    server = HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    from sshdeck import webview_surface as surface

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "index.html").write_text(
            "<!doctype html><title>Switch admin</title><h1>fixture</h1>", encoding="utf-8"
        )
        server = serve(tmp, 8941)
        time.sleep(0.4)
        os.environ["SSHDECK_DATA"] = str(Path(tmp, "data"))
        cfg = surface.surface_config("http://127.0.0.1:8941/", 7, "Switch fixture")
        env = dict(os.environ, QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu")
        command = [
            "xvfb-run", "-a", "-s", "-screen 0 1280x900x24",
            sys.executable, "-m", "sshdeck.webview_surface",
            "--json", json.dumps(cfg), "--selftest",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
        finally:
            server.shutdown()
        output = (result.stdout or "") + (result.stderr or "")
        ok = "SELFTEST ok" in output and "Switch admin" in output
        print(output.strip().splitlines()[-1] if output.strip() else "(no output)")
        print("RESULT:", "PASS" if ok else "FAIL")
        print("profile:", cfg["profile"], "| bridge:", cfg["bridge"], "| app cookies:", cfg["app_cookies"])
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
