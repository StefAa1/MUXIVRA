"""Telnet and serial (COM) transports sharing the SSH shell session contract."""
from __future__ import annotations

import socket
import threading
import time

from . import config

# Telnet command bytes (RFC 854 / 1073 / 855)
IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240
OPT_ECHO = 1
OPT_SGA = 3
OPT_TTYPE = 24
OPT_NAWS = 31


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class TelnetParser:
    """Strip IAC sequences from the stream and answer option negotiation."""

    def __init__(self, cols: int = 80, rows: int = 24):
        self.cols = cols
        self.rows = rows
        self.naws_enabled = False
        self._state = "data"
        self._cmd: int | None = None
        self._sb = bytearray()

    def _naws(self) -> bytes:
        c, r = max(1, self.cols), max(1, self.rows)
        return bytes([IAC, SB, OPT_NAWS, (c >> 8) & 0xFF, c & 0xFF, (r >> 8) & 0xFF, r & 0xFF, IAC, SE])

    def _ttype_reply(self) -> bytes:
        """TERMINAL-TYPE subnegotiation: answer SEND(1) with IS(0) + our TERM."""
        term = b"xterm-256color"
        return bytes([IAC, SB, OPT_TTYPE, 0]) + term + bytes([IAC, SE])

    def _subnegotiate(self) -> bytes:
        """Handle SB payloads we care about (currently TERMINAL-TYPE)."""
        if len(self._sb) >= 2 and self._sb[0] == OPT_TTYPE:
            if self._sb[1] == 1:  # SEND
                return self._ttype_reply()
        return b""

    def _negotiate(self, cmd: int, opt: int) -> bytes:
        if cmd == DO:
            if opt == OPT_NAWS:
                self.naws_enabled = True
                return bytes([IAC, WILL, OPT_NAWS]) + self._naws()
            if opt in (OPT_SGA, OPT_TTYPE):
                return bytes([IAC, WILL, opt])
            return bytes([IAC, WONT, opt])
        if cmd == WILL:
            if opt in (OPT_ECHO, OPT_SGA):
                return bytes([IAC, DO, opt])
            return bytes([IAC, DONT, opt])
        return b""

    def feed(self, data: bytes) -> tuple[bytes, bytes]:
        out = bytearray()
        reply = bytearray()
        for byte in data:
            if self._state == "data":
                if byte == IAC:
                    self._state = "iac"
                else:
                    out.append(byte)
            elif self._state == "iac":
                if byte == IAC:  # escaped 0xFF in the data stream
                    out.append(0xFF)
                    self._state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self._cmd = byte
                    self._state = "opt"
                elif byte == SB:
                    self._state = "sb"
                else:  # NOP, GA, AYT, ...
                    self._state = "data"
            elif self._state == "opt":
                reply.extend(self._negotiate(self._cmd or 0, byte))
                self._state = "data"
            elif self._state == "sb":
                if byte == IAC:
                    self._state = "sb_iac"
                else:
                    self._sb.append(byte)
            elif self._state == "sb_iac":
                if byte == SE:
                    reply.extend(self._subnegotiate())
                    self._sb.clear()
                    self._state = "data"
                elif byte == IAC:
                    self._sb.append(0xFF)
                    self._state = "sb"
                else:
                    self._sb.append(byte)
                    self._state = "sb"
        return bytes(out), bytes(reply)

    def resize(self, cols: int, rows: int) -> bytes:
        self.cols, self.rows = cols, rows
        return self._naws() if self.naws_enabled else b""


class SessionBase:
    protocol = "base"

    def __init__(self, target, cols: int = 120, rows: int = 32):
        self.target = target
        self.cols = cols
        self.rows = rows
        self.on_data = None  # callable(bytes)
        self.on_close = None  # callable()
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                chunk = self._read_chunk()
            except Exception:
                break
            if chunk is None:
                break
            if chunk and self.on_data:
                self.on_data(chunk)
        self.close()
        if self.on_close:
            self.on_close()

    def _read_chunk(self) -> bytes | None:
        raise NotImplementedError

    def write(self, text: str) -> None:
        if self._closed.is_set():
            return
        try:
            self._write(text.encode("utf-8"))
        except Exception:
            self.close()

    def _write(self, data: bytes) -> None:
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._shutdown()
        except Exception:
            pass

    def _shutdown(self) -> None:
        raise NotImplementedError

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def describe(self) -> str:
        return self.target.label


class TelnetSession(SessionBase):
    protocol = "telnet"

    def __init__(self, target, cols: int = 120, rows: int = 32):
        super().__init__(target, cols, rows)
        host = target.hostname
        port = int(target.port or 23)
        try:
            self.sock = socket.create_connection((host, port), timeout=config.CONNECT_TIMEOUT)
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError("timeout", f"Timeout στη σύνδεση με {host}:{port}") from exc
        except OSError as exc:
            raise TransportError("network", f"Δεν έγινε σύνδεση με {host}:{port}: {exc}") from exc
        self.sock.settimeout(0.3)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self.parser = TelnetParser(cols, rows)
        self._send_lock = threading.Lock()

    def _read_chunk(self) -> bytes | None:
        try:
            raw = self.sock.recv(65536)
        except socket.timeout:
            return b""
        except OSError:
            return None
        if not raw:
            return None
        data, reply = self.parser.feed(raw)
        if reply:
            try:
                self.sock.sendall(reply)
            except OSError:
                return None
        return data

    def _write(self, data: bytes) -> None:
        # Enter arrives as CR; telnet hosts expect CRLF line endings.
        text = data.decode("utf-8", "replace")
        # normalise every line ending to CRLF: pasted text arrives as CRLF or LF,
        # Enter arrives as a lone CR — all three must produce exactly one CRLF
        normalised = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        with self._send_lock:
            self.sock.sendall(normalised.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        super().resize(cols, rows)
        payload = self.parser.resize(cols, rows)
        if payload and not self._closed.is_set():
            try:
                with self._send_lock:
                    self.sock.sendall(payload)
            except OSError:
                pass

    def _shutdown(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


BYTESIZE = {5: 5, 6: 6, 7: 7, 8: 8}
PARITY = {"N": "N", "E": "E", "O": "O", "M": "M", "S": "S"}
STOPBITS = {1: 1, 1.5: 1.5, 2: 2}


class SerialSession(SessionBase):
    protocol = "serial"

    def __init__(self, target, cols: int = 120, rows: int = 32):
        super().__init__(target, cols, rows)
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise TransportError("pyserial_missing", "Το pyserial δεν είναι εγκατεστημένο.") from exc
        port = (target.serial_port or "").strip()
        flow = str(getattr(target, "flow", "none") or "none").lower()
        if flow not in {"none", "rtscts", "xonxoff", "dsrdtr"}:
            flow = "none"
        if not port:
            raise TransportError("serial_port_missing", "Δεν έχει οριστεί σειριακή θύρα.")
        kwargs = dict(
            port=port,
            baudrate=int(target.baud or 115200),
            bytesize=BYTESIZE.get(int(target.bytesize or 8), 8),
            parity=PARITY.get((target.parity or "N").upper(), "N"),
            stopbits=STOPBITS.get(float(target.stopbits or 1), 1),
            rtscts=flow in {"rtscts", "dsrdtr"},
            xonxoff=flow == "xonxoff",
            dsrdtr=flow == "dsrdtr",
            timeout=0.2,
            write_timeout=2,
        )
        try:
            self.ser = serial.Serial(**kwargs)
        except Exception as exc:
            raise TransportError("serial_open_failed", f"Άνοιγμα {port} απέτυχε: {exc}") from exc

    def _read_chunk(self) -> bytes | None:
        try:
            waiting = self.ser.in_waiting
            data = self.ser.read(waiting or 1)
        except Exception:
            return None
        return data if data else b""

    def _write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def _shutdown(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover
        return []
    out = []
    for info in list_ports.comports():
        out.append(
            {
                "device": info.device,
                "description": info.description or "",
                "hwid": info.hwid or "",
            }
        )
    out.sort(key=lambda p: p["device"])
    return out


def open_session(target, cols: int = 120, rows: int = 32) -> SessionBase:
    """Build the session object for a host, whatever its protocol."""
    proto = getattr(target, "protocol", "ssh") or "ssh"
    if proto == "telnet":
        return TelnetSession(target, cols, rows)
    if proto == "serial":
        return SerialSession(target, cols, rows)
    from . import sshconn

    session = sshconn.ShellSession(target, cols, rows)
    setattr(session, "protocol", "ssh")
    setattr(session, "describe", lambda: f"{target.username}@{target.hostname}")
    return session


def tcp_probe(host: str, port: int, timeout: float = 6.0) -> dict:
    started = time.time()
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    sock.close()
    return {"reachable": True, "latency_ms": int((time.time() - started) * 1000)}
