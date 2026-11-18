"""Mock network/serial devices used to exercise TELNET and COM sessions.

  python scripts/fake_device.py --telnet 2323
  python scripts/fake_device.py --serial --slave-file /tmp/sshdeck-pty

The device answers a tiny command set so a session can be verified end to end.
"""
from __future__ import annotations

import argparse
import os
import pty
import select
import socket
import sys
import threading
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sshdeck.transports import TelnetParser  # noqa: E402

BANNER = b"\r\nSSHDeck Mock Device v1.0\r\n"
PROMPT = b"device> "
REPLIES = {
    b"help": b"commands: status, version, reboot, help, exit",
    b"status": b"ok uptime=2d04h temp=41C relays=4/4",
    b"version": b"firmware 1.0.3 (mock build 2026-11-18)",
    b"reboot": b"rebooting...",
    b"exit": b"bye",
}


class Device:
    def __init__(self, writer, log_path: str | None = None):
        self.writer = writer
        self.buffer = b""
        self.log_path = log_path
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def note(self, line: str) -> None:
        if self.log:
            self.log.write(f"{time.strftime('%H:%M:%S')} {line}\n")
            self.log.flush()

    def greet(self) -> None:
        self.writer(BANNER + PROMPT)
        self.note("banner sent")

    def feed(self, data: bytes) -> None:
        if not data:
            return
        self.buffer += data
        while True:
            for sep in (b"\r\n", b"\r", b"\n"):
                if sep in self.buffer:
                    line, self.buffer = self.buffer.split(sep, 1)
                    break
            else:
                return
            if not line.strip():
                self.writer(PROMPT)
                continue
            self.note(f"rx {line!r}")
            reply = REPLIES.get(line.strip().lower(), b"unknown command: " + line.strip())
            self.writer(reply + b"\r\n" + PROMPT)


def serve_telnet(port: int, log_path: str | None) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    print(f"telnet mock listening on 127.0.0.1:{port}", flush=True)
    parser = TelnetParser()
    while True:
        conn, addr = srv.accept()
        print(f"telnet client {addr}", flush=True)

        def send(payload: bytes, sock=conn) -> None:
            sock.sendall(payload)

        device = Device(send, log_path)
        try:
            device.greet()
        except OSError:  # client vanished before the banner: keep serving
            conn.close()
            continue
        conn.settimeout(0.5)
        try:
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                clean, reply = parser.feed(chunk)
                if reply:
                    conn.sendall(reply)
                device.feed(clean)
        except OSError:
            pass
        finally:
            conn.close()


def serve_serial(slave_file: str, log_path: str | None) -> None:
    master, slave = pty.openpty()
    # The pty starts cooked with ECHO on, so every byte written to the master is
    # echoed straight back and the device answers its own banner forever. Raw
    # mode on the tty (not just the fds) stops the echo.
    tty.setraw(slave)
    slave_name = os.ttyname(slave)
    with open(slave_file, "w", encoding="utf-8") as fh:
        fh.write(slave_name)
    print(f"serial mock on {slave_name}", flush=True)
    os.set_blocking(master, False)

    def send(payload: bytes) -> None:
        os.write(master, payload)

    device = Device(send, log_path)
    # Writes to the master are discarded while no client holds the slave open, so
    # a banner sent at startup never reaches the operator. Greet on the first
    # input instead: the port is provably attached by then.
    greeted = False
    deadline = time.time() + 1800
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.4)
        if not ready:
            continue
        try:
            data = os.read(master, 4096)
        except OSError:
            break
        if not greeted:
            device.greet()
            greeted = True
        device.feed(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telnet", type=int)
    ap.add_argument("--serial", action="store_true")
    ap.add_argument("--slave-file", default="/tmp/sshdeck-pty")
    ap.add_argument("--log", default=None, help="append a transcript to this file")
    args = ap.parse_args()
    if args.telnet:
        serve_telnet(args.telnet, args.log)
    elif args.serial:
        serve_serial(args.slave_file, args.log)
    else:
        ap.error("choose --telnet PORT or --serial")
    return 0


if __name__ == "__main__":
    threading.current_thread().name = "mock-device"
    raise SystemExit(main())
