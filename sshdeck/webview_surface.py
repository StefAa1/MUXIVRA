"""Device web management in an isolated native surface.

The device's own admin page is vendor content. It must never share anything with
the MUXIVRA application: no app bridge, no app cookies, no vault, no local API.
That is why this runs as a separate process with its own Qt WebEngine profile
(storage and cache per customer), and why the in-app page only ever receives a
URL — the vendor page is not embedded in the application document at all.

If Qt is unavailable the caller is told the truth: the page opens in the system
browser, outside the isolation this module provides.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


class SurfaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_url(url: str) -> str:
    """Only a real device management URL. No credentials in the URL, ever."""
    text = (url or "").strip()
    if not text:
        raise SurfaceError("url_required", "Χρειάζεται διεύθυνση της συσκευής.")
    parsed = urlparse(text)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SurfaceError("bad_scheme", f"Επιτρέπονται μόνο http/https, όχι {parsed.scheme or 'κενό'}.")
    if not parsed.hostname:
        raise SurfaceError("bad_url", "Η διεύθυνση δεν έχει host.")
    if parsed.username or parsed.password:
        raise SurfaceError("credentials_in_url", "Μην βάζεις διαπιστευτήρια στη διεύθυνση.")
    if "@" in (parsed.netloc or ""):
        raise SurfaceError("credentials_in_url", "Μην βάζεις διαπιστευτήρια στη διεύθυνση.")
    return text


def default_url(device_row) -> str:
    """What to open when the operator gives no URL: the documented management IP."""
    keys = device_row.keys()
    for column in ("mgmt_ip", "hostname"):
        if column in keys and (device_row[column] or "").strip():
            host = device_row[column].strip()
            break
    else:
        host = ""
    if not host:
        raise SurfaceError("no_address", "Η συσκευή δεν έχει management IP ή hostname.")
    if host.startswith("http://") or host.startswith("https://"):
        return validate_url(host)
    scheme = "https" if _looks_https(host) else "http"
    return f"{scheme}://{host}/"


def _looks_https(host: str) -> bool:
    # vendor panels usually terminate TLS on 443; an explicit URL always wins
    return False


def profile_dir(customer_id: int | None) -> Path:
    """One browser profile per customer: same IP, two customers, two sessions."""
    from . import config

    base = Path(config.DATA_DIR) / "web-profiles"
    scope = f"customer-{int(customer_id)}" if customer_id else "customer-unknown"
    path = base / scope
    path.mkdir(parents=True, exist_ok=True)
    return path


def surface_config(url: str, customer_id: int | None, device_label: str,
                   trust_sha256: str | None = None) -> dict:
    """Everything the native surface is allowed to do — inspectable before launch."""
    clean = validate_url(url)
    return {
        "url": clean,
        "profile": str(profile_dir(customer_id)),
        "title": f"{device_label or 'Συσκευή'} — web management",
        "bridge": False,              # no QWebChannel: vendor page cannot call the app
        "app_cookies": False,         # separate profile, separate storage
        "javascript_enabled": True,   # vendor panels need it; it is their document
        "trust_sha256": trust_sha256 or "",
        "schemes": list(ALLOWED_SCHEMES),
    }


def qt_available() -> bool:
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def launch(url: str, customer_id: int | None, device_label: str,
           trust_sha256: str | None = None) -> dict:
    """Open the device page in its own isolated window (non-blocking)."""
    cfg = surface_config(url, customer_id, device_label, trust_sha256)
    if not qt_available():
        return {
            "mode": "external",
            "url": cfg["url"],
            "warning": "Το Qt WebEngine δεν είναι διαθέσιμο: η σελίδα θα ανοίξει στον "
                       "περιηγητή του συστήματος, χωρίς την απομόνωση της εφαρμογής.",
        }
    log_path = Path(cfg["profile"]).parent / f"surface-{secrets.token_hex(4)}.log"
    process = subprocess.Popen(
        [sys.executable, "-m", "sshdeck.webview_surface", "--json", json.dumps(cfg)],
        stdout=subprocess.DEVNULL, stderr=open(log_path, "w", encoding="utf-8"),
        close_fds=True,
    )
    return {"mode": "window", "url": cfg["url"], "pid": process.pid,
            "profile": cfg["profile"], "log": str(log_path)}


# ------------------------------------------------------------------ Qt surface
def _run_surface(cfg: dict, selftest: bool = False) -> int:   # pragma: no cover - GUI
    """Native window with its own storage: the vendor page never touches the app."""
    import sys as _sys

    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import (
        QApplication, QLabel, QMainWindow, QToolBar, QWidget,
    )

    app = QApplication.instance() or QApplication(_sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle(cfg["title"])
    window.resize(1180, 840)

    profile = QWebEngineProfile("muxivra-device", app)
    profile.setPersistentStoragePath(cfg["profile"])
    profile.setCachePath(str(Path(cfg["profile"]) / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)

    view = QWebEngineView()
    page = QWebEnginePage(profile, view)
    view.setPage(page)

    toolbar = QToolBar("Περιήγηση")
    toolbar.setMovable(False)
    window.addToolBar(toolbar)
    back = toolbar.addAction("←")
    forward = toolbar.addAction("→")
    reload_action = toolbar.addAction("⟳")
    toolbar.addSeparator()
    address = QLabel(cfg["url"])
    address.setStyleSheet("padding: 0 10px; color: #8a94a6;")
    toolbar.addWidget(address)
    toolbar.addSeparator()
    scope = QLabel("απομονωμένο περιβάλλον · χωρίς πρόσβαση στην εφαρμογή")
    scope.setStyleSheet("padding: 0 10px; color: #c9a227; font-size: 11px;")
    toolbar.addWidget(scope)

    back.triggered.connect(view.back)
    forward.triggered.connect(view.forward)
    reload_action.triggered.connect(view.reload)
    view.urlChanged.connect(lambda url: address.setText(url.toString()))
    view.titleChanged.connect(
        lambda title: window.setWindowTitle(f"{cfg['title']} — {title}" if title else cfg["title"])
    )

    trust = (cfg.get("trust_sha256") or "").strip().lower().replace(":", "")
    refused: list[str] = []

    class SurfacePage(QWebEnginePage):
        def certificateError(self, error):        # noqa: N802 - Qt naming
            try:
                fingerprint = bytes(error.certificate().digest()).hex()
            except Exception:                     # noqa: BLE001
                refused.append("unknown")
                return False
            if trust and fingerprint == trust:
                print(f"[surface] certificate accepted for {error.url().toString()} "
                      f"sha256={fingerprint}", flush=True)
                return True
            refused.append(fingerprint)
            print(f"[surface] certificate REFUSED for {error.url().toString()} "
                  f"sha256={fingerprint} — βάλε το fingerprint στη συσκευή για ρητή εμπιστοσύνη.",
                  flush=True)
            return False

    isolated = SurfacePage(profile, view)
    view.setPage(isolated)
    window.setCentralWidget(view)
    view.load(QUrl(cfg["url"]))

    if selftest:
        result = {"code": 1}

        def finish(ok: bool) -> None:
            result["code"] = 0 if ok else 1
            print(f"SELFTEST {'ok' if ok else 'fail'} url={cfg['url']} "
                  f"title={view.title()!r} profile={cfg['profile']}", flush=True)
            app.quit()

        view.loadFinished.connect(lambda ok: QTimer.singleShot(400, lambda: finish(ok)))

        def timeout() -> None:
            print(f"SELFTEST fail timeout profile={cfg['profile']}", flush=True)
            app.quit()

        QTimer.singleShot(20000, timeout)
        window.show()
        return app.exec()

    window.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" in argv:
        cfg = json.loads(argv[argv.index("--json") + 1])
        return _run_surface(cfg, selftest="--selftest" in argv)
    # check mode: print what would be launched, without opening anything
    url = argv[argv.index("--url") + 1] if "--url" in argv else ""
    customer = int(argv[argv.index("--customer") + 1]) if "--customer" in argv else None
    print(json.dumps(surface_config(url, customer, "check"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":                     # pragma: no cover
    raise SystemExit(main())
