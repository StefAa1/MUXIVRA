"""Native desktop shell: local engine + OS webview window (no browser, no URL).

The HTTP server is an implementation detail: it binds a random loopback port and
the window loads a one-time token URL that is exchanged for a session cookie and
then stripped from the address. Nothing is reachable from outside the machine.
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request

TOKEN_TTL_SECONDS = 180
_PENDING: dict[str, float] = {}
_LOCK = threading.Lock()


# ------------------------------------------------------------------ tokens
def new_token() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for key, expiry in list(_PENDING.items()):
            if expiry < now:
                _PENDING.pop(key, None)
        _PENDING[token] = now + TOKEN_TTL_SECONDS
    return token


def consume_token(token: str) -> bool:
    """Single use: a token is valid once, within its short lifetime."""
    now = time.time()
    with _LOCK:
        expiry = _PENDING.pop(token, None)
    return expiry is not None and expiry >= now


# ------------------------------------------------------------------ server
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(port: int, log_level: str = "warning"):
    import uvicorn

    from .main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level=log_level))
    thread = threading.Thread(target=server.run, name="sshdeck-engine", daemon=True)
    thread.start()
    return server


def wait_ready(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


class DesktopBridge:
    """Native window services for the trusted app page only.

    The clipboard lives here because the Qt webview blocks the async Clipboard
    API on a plain http origin; JS falls back to this bridge instead.
    """

    def clip_set(self, text):                      # noqa: D401 - JS bridge
        text = "" if text is None else str(text)
        try:
            import pyperclip                              # type: ignore
            pyperclip.copy(text)
            return True
        except Exception:
            pass
        if os.name == "nt":
            try:
                subprocess.run(["cmd", "/c", "clip"], input=text.encode("utf-16-le"),
                               check=False, creationflags=0x08000000)
                return True
            except Exception:
                return False
        return False

    def clip_get(self):                            # noqa: D401 - JS bridge
        try:
            import pyperclip                              # type: ignore
            return pyperclip.paste() or ""
        except Exception:
            return ""


_BRIDGE = None


def _desktop_bridge():
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = DesktopBridge()
    return _BRIDGE


def acquire_instance_lock():
    """One engine per data directory: a second double-click must not share SQLite."""
    from . import config

    config.migrate_legacy_data()   # adopt an old <bundle>/data BEFORE creating the new dir
    config.ensure_dirs()
    handle = open(config.DATA_DIR / "instance.lock", "a+")
    try:
        if os.name == "nt":  # pragma: no cover - Windows only
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    # γράψε το PID: updater/installer/uninstaller κλείνουν ΜΟΝΟ αυτή τη διεργασία
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return handle


def selftest(port: int = 0) -> int:
    """Headless check of the desktop handshake (no window)."""
    port = port or free_port()
    server = start_server(port)
    try:
        if not wait_ready(port):
            print("FAIL engine did not become ready")
            return 1
        token = new_token()
        body = ('{"token": "%s"}' % token).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/desktop-login",
            data=body,
            headers={"content-type": "application/json", "x-requested-with": "sshdeck"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            cookie = response.headers.get("set-cookie", "")
            ok = response.status == 200 and "sshdeck_session=" in cookie
        reuse = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/desktop-login",
            data=body,
            headers={"content-type": "application/json", "x-requested-with": "sshdeck"},
            method="POST",
        )
        try:
            urllib.request.urlopen(reuse, timeout=5)
            replay = "accepted (BAD)"
        except urllib.error.HTTPError as exc:
            replay = f"rejected {exc.code}"
        print(f"engine ready: yes\ncookie issued: {'yes' if ok else 'no'}\ntoken reuse: {replay}")
        return 0 if ok and replay.startswith("rejected") else 1
    finally:
        server.should_exit = True


# ------------------------------------------------------------------ window
def _log(message: str) -> None:
    from . import config

    try:
        config.ensure_dirs()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(config.LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [desktop] {message}\n")
    except OSError:
        pass


def _open_edge_app(url: str, width: int = 1400, height: int = 860) -> bool:
    """Chromeless Edge window: a fallback that still looks like an app, not a tab."""
    import subprocess

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for exe in candidates:
        if os.path.exists(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", f"--window-size={width},{height}"])
                _log(f"opened Edge app window from {exe}")
                return True
            except OSError as exc:
                _log(f"edge app mode failed: {exc}")
    return False


def run(
    port: int | None = None,
    width: int = 1320,
    height: int = 860,
    debug: bool = False,
    gui: str | None = None,
) -> int:
    try:
        import webview
    except ImportError:
        _log("pywebview is not installed")
        print("pywebview is not installed: pip install 'pywebview[qt]'")
        return 2

    lock = acquire_instance_lock()
    if lock is None:
        print("MUXIVRA is already running — close the existing window first.")
        return 0

    port = port or free_port()
    server = start_server(port)
    if not wait_ready(port):
        server.should_exit = True
        _log("engine failed to start")
        print("engine failed to start")
        return 1

    # Qt ships its own Chromium and needs no .NET; it is the only backend that
    # works out of the box on a clean Windows install.
    order = [gui] if gui else ["qt", "gtk", "edgechromium", "cef"]
    _log(f"engine on 127.0.0.1:{port}; backend order {order}")
    url = ""
    try:
        bridge = _desktop_bridge()
        for backend in order:
            url = f"http://127.0.0.1:{port}/?desktop_token={new_token()}"
            try:
                webview.create_window(
                    "MUXIVRA",
                    url,
                    width=width,
                    height=height,
                    min_size=(960, 620),
                    background_color="#080a0e",
                    confirm_close=False,
                    js_api=bridge,
                )
                webview.start(gui=backend, debug=debug, private_mode=False)
                _log(f"backend {backend} exited normally")
                return 0
            except Exception as exc:  # try the next backend
                _log(f"backend {backend} failed: {type(exc).__name__}: {exc}")
                continue

        # Last resort: keep the engine alive and hand the URL to a chromeless window,
        # then to the default browser.
        import webbrowser

        if not _open_edge_app(url, width, height):
            _log("no webview backend and no Edge — opening the system browser")
            webbrowser.open(url)
        while True:
            time.sleep(1)
    finally:
        server.should_exit = True
        time.sleep(0.3)
