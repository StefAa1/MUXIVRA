"""Admin CLI: sshdeck serve|init|set-password|issue-session|status."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time

from . import config, db, security


def cmd_init(_args) -> int:
    db.init()
    print(f"initialized data dir: {config.DATA_DIR}")
    print(f"db: {config.DB_PATH}")
    return 0


def cmd_status(_args) -> int:
    db.init()
    with db.db() as conn:
        initialized = security.user_exists(conn)
        hosts = conn.execute("SELECT COUNT(*) c FROM hosts").fetchone()["c"]
        creds = conn.execute("SELECT COUNT(*) c FROM credentials").fetchone()["c"]
        sessions = conn.execute("SELECT COUNT(*) c FROM sessions WHERE expires_at > ?", (db.now(),)).fetchone()["c"]
        username = None
        if initialized:
            username = conn.execute("SELECT username FROM users WHERE id = 1").fetchone()["username"]
    print(json.dumps(
        {
            "initialized": initialized,
            "username": username,
            "hosts": hosts,
            "credentials": creds,
            "active_sessions": sessions,
            "data_dir": str(config.DATA_DIR),
            "vault_key": str(config.VAULT_KEY_PATH),
        },
        indent=2,
    ))
    return 0


def cmd_set_password(args) -> int:
    db.init()
    if args.password_file:
        password = open(args.password_file, encoding="utf-8").read().strip()
    else:
        password = getpass.getpass("new master password: ")
        confirm = getpass.getpass("repeat: ")
        if password != confirm:
            print("passwords differ", file=sys.stderr)
            return 2
    with db.db() as conn:
        if security.user_exists(conn):
            security.change_password(conn, password)
            conn.execute("DELETE FROM sessions")
            print("master password updated; all sessions revoked")
        else:
            security.create_user(conn, args.username, password)
            print(f"master password set for user {args.username!r}")
    return 0


def cmd_issue_session(args) -> int:
    """Headless operator access: mint a session token for the local caller."""
    db.init()
    with db.db() as conn:
        if not security.user_exists(conn):
            print("run set-password first", file=sys.stderr)
            return 2
        token = security.create_session(conn, "cli", ttl=args.ttl)
    if args.json:
        print(json.dumps({"token": token, "ttl": args.ttl, "cookie": config.COOKIE_NAME}))
    else:
        print(token)
    return 0


def cmd_reset_account(args) -> int:
    """Drop the local account and all sessions, keeping inventory and vault data."""
    db.init()
    with db.db() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
    print("account reset: no master password set, all sessions revoked")
    return 0


def cmd_keeper_import(args) -> int:
    """Import SSH-ish records from Keeper Secrets Manager (or an exported JSON)."""
    from . import keeper

    db.init()
    records: list[dict] = []
    if args.from_json:
        try:
            payload = json.loads(open(args.from_json, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read {args.from_json}: {exc}", file=sys.stderr)
            return 2
        if isinstance(payload, dict):
            payload = payload.get("records") or payload.get("secrets") or [payload]
        records = [r for r in payload if isinstance(r, dict)]
    else:
        token = ""
        if args.token_file:
            try:
                token = open(args.token_file, encoding="utf-8").read().strip()
            except OSError as exc:
                print(f"cannot read token file: {exc}", file=sys.stderr)
                return 2
        try:
            records = keeper.fetch_records(token, args.config)
        except keeper.KeeperError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 2

    with db.db() as conn:
        summary = keeper.import_records(conn, records, group=args.group, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_desktop(args) -> int:
    from . import desktop

    db.init()
    if args.selftest:
        return desktop.selftest(args.port)
    try:
        return desktop.run(
            port=args.port, width=args.width, height=args.height, debug=args.debug, gui=args.gui
        )
    except Exception as exc:  # never die with a raw traceback in a windowless process
        desktop._log(f"fatal: {type(exc).__name__}: {exc}")
        print(f"MUXIVRA failed to start: {exc}")
        return 1


def cmd_serve(args) -> int:
    import threading
    import webbrowser

    import uvicorn

    config.ensure_dirs()
    db.init()
    if not args.no_reminder:
        with db.db() as conn:
            if not security.user_exists(conn):
                print(
                    f"! no master password yet — open http://{args.host}:{args.port} and create one",
                    file=sys.stderr,
                )
    if args.open_browser:
        host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        url = f"http://{host}:{args.port}/"

        def _open() -> None:
            time.sleep(1.8)
            try:
                webbrowser.open(url)
            except Exception:
                print(f"open {url} manually", file=sys.stderr)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "sshdeck.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sshdeck", description="MUXIVRA — self-hosted SSH workspace")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the web server")
    p.add_argument("--host", default=config.BIND_HOST)
    p.add_argument("--port", type=int, default=config.BIND_PORT)
    p.add_argument("--log-level", default="info")
    p.add_argument("--no-reminder", action="store_true")
    p.add_argument("--open-browser", action="store_true", help="open the UI in the default browser")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("init", help="create data dir and database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="print state summary")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("set-password", help="set or change the master password")
    p.add_argument("--username", default="admin")
    p.add_argument("--password-file", help="read the password from a 0600 file instead of prompting")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("issue-session", help="mint a session token for local operator access")
    p.add_argument("--ttl", type=int, default=600)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_issue_session)

    p = sub.add_parser("reset-account", help="remove the local account and revoke all sessions")
    p.set_defaults(func=cmd_reset_account)

    p = sub.add_parser("desktop", help="run as a native desktop app (no browser)")
    p.add_argument("--port", type=int, default=0, help="engine port (0 = random free port)")
    p.add_argument("--width", type=int, default=1320)
    p.add_argument("--height", type=int, default=860)
    p.add_argument("--debug", action="store_true", help="open devtools in the window")
    p.add_argument("--gui", default=None, help="force a webview backend (qt, edgechromium, gtk, cef)")
    p.add_argument("--selftest", action="store_true", help="headless check of the engine + login handshake")
    p.set_defaults(func=cmd_desktop)

    p = sub.add_parser("keeper-import", help="import SSH hosts/keys from Keeper Secrets Manager")
    p.add_argument("--token-file", help="file containing the KSM One-Time Access Token")
    p.add_argument("--config", default=str(config.DATA_DIR / "ksm-config.json"),
                   help="bound KSM config file (created on first use)")
    p.add_argument("--from-json", help="import records from an exported JSON file instead of Keeper")
    p.add_argument("--group", default="Keeper", help="MUXIVRA group for imported hosts")
    p.add_argument("--dry-run", action="store_true", help="show the mapping without writing")
    p.set_defaults(func=cmd_keeper_import)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
