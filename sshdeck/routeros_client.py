"""RouterOS client: hand a MikroTik device to MikroTik's own GUI.

MUXIVRA does not speak the RouterOS client wire protocol and does not embed the RouterOS client
window.  It starts the official executable as a *separate* desktop window and
passes only what MikroTik documents as positional command-line arguments:

    routeros_client.exe [<connect-to> [<login> [<password>]] <session|workspace>]

The password slot is never filled: credentials stay out of the process command
line (readable by other local tooling) and out of any temporary file.  RouterOS client
asks for the password itself, in its own login dialog.

Every behavioural claim below was read on 2026-11-18 from:

* https://help.mikrotik.com/docs/spaces/ROS/pages/328129/RouterOS client  ("Command Line")
* https://manual.mikrotik.com/docs/management-tools/routeros-client/
* https://manual.mikrotik.com/docs/management-tools/routeros-client-legacy/
* https://mikrotik.com/download/routeros-client

See ``docs/routeros-client.md`` for the sourcing table and the known limits.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

__all__ = [
    "WINDOW_MODE",
    "ENV_VAR",
    "RouterOS clientUnavailable",
    "build_args",
    "describe",
    "find_routeros_client",
    "launch",
]

#: RouterOS client is a normal top-level application window; MUXIVRA cannot host it.
WINDOW_MODE = "external-window"

#: Explicit override, highest discovery priority (the official build is portable
#: and may live anywhere -- see docs/routeros-client.md).
ENV_VAR = "MUXIVRA_ROUTEROS_CLIENT"

#: Executable names MikroTik ships.  ``routeros_client64.exe`` is the 64-bit RouterOS client v3
#: build, ``RouterOS client.exe`` the RouterOS client v4 archive payload; Windows matches names
#: case-insensitively, so comparisons use ``str.lower()``.
EXE_NAMES = ("routeros_client64.exe", "routeros_client.exe")

HELP_URL = "https://help.mikrotik.com/docs/spaces/ROS/pages/328129/RouterOS client"
DOWNLOAD_URL = "https://mikrotik.com/download/routeros-client"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
#: An address that already carries a port: ``host:8291`` or ``[v6]:8291``.
#: Kept explicit so a bare IPv6 literal such as ``db8::1`` is not mistaken for one.
_BRACKETED_WITH_PORT = re.compile(r"^\[[^\]]+\]:\d{1,5}$")
_HOST_WITH_PORT = re.compile(r"^[^:\[\]]+:\d{1,5}$")
_VERSION_IN_NAME = re.compile(r"(\d+\.\d+(?:\.\d+)?)")

_MESSAGES = {
    "client_not_found": "RouterOS client executable not found.",
    "client_launch_failed": "RouterOS client was found but could not be started.",
}


class RouterOS clientUnavailable(RuntimeError):
    """RouterOS client cannot be launched here.

    ``code`` is stable for callers (``client_not_found`` when no executable is
    available, ``client_launch_failed`` when the executable exists but the OS
    refused to start it); ``message`` is meant for logs and the UI.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------- arguments
def _target(address: str, port: int | None) -> str:
    """Return the ``<connect-to>`` token, adding a documented ``host:port``.

    MikroTik documents that a non-default port is appended to the address with a
    colon (``192.168.88.1:9999``) and that IPv6 literals must be bracketed
    (``[db8::1]``), so the same rule is applied here.
    """
    if not isinstance(address, str):
        raise ValueError("address must be a string")
    addr = address.strip()
    if not addr:
        raise ValueError("address must not be empty")
    if _CONTROL_CHARS.search(addr):
        raise ValueError("address must not contain control characters")
    if addr.startswith("-"):
        # Would otherwise be read by RouterOS client as a switch such as --romon.
        raise ValueError("address must not start with '-'")
    if port is None:
        return addr
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if _BRACKETED_WITH_PORT.match(addr) or _HOST_WITH_PORT.match(addr):
        raise ValueError("address already contains a port; pass it once")
    if addr.count(":") >= 2 and not (addr.startswith("[") and addr.endswith("]")):
        return f"[{addr}]:{port}"
    return f"{addr}:{port}"


def _user(username: str | None) -> str | None:
    if username is None:
        return None
    if not isinstance(username, str):
        raise ValueError("username must be a string")
    user = username.strip()
    if not user:
        raise ValueError("username must not be empty")
    if _CONTROL_CHARS.search(user):
        raise ValueError("username must not contain control characters")
    if user.startswith("-"):
        raise ValueError("username must not start with '-'")
    return user


def _argv(executable: str, target: str, user: str | None) -> list[str]:
    # Documented order is connect-to, login[, password[, session]]; we stop after
    # login so the password slot is never occupied.
    argv = [executable, target]
    if user is not None:
        argv.append(user)
    return argv


def build_args(
    executable: str | os.PathLike[str],
    address: str,
    username: str | None = None,
    port: int | None = None,
) -> list[str]:
    """Build the RouterOS client argv for ``address``/``username``/``port``.

    Pure function: no filesystem access and no process start, so callers (and
    tests) can inspect exactly what would be executed.  The result never
    contains a password, a token or a temporary file path.
    """
    exe = os.fspath(executable)
    if not isinstance(exe, str) or not exe.strip():
        raise ValueError("executable must be a non-empty path")
    return _argv(exe, _target(address, port), _user(username))


# ---------------------------------------------------------------- discovery
def _exe_in(directory: Path) -> Path | None:
    """First MikroTik executable inside ``directory`` (64-bit build preferred)."""
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return None
    for wanted in EXE_NAMES:
        for entry in entries:
            if entry.name.lower() == wanted and entry.is_file():
                return entry
    return None


def _candidate_paths(env: Mapping[str, str]) -> Iterable[Path]:
    """Yield discovery candidates in priority order, without touching the disk twice.

    Split out from :func:`find_routeros_client` so the Windows layout can be exercised on
    a POSIX host with a synthetic ``env``.
    """
    local = env.get("LOCALAPPDATA")
    if local:
        base = Path(local, "MikroTik")
        try:
            folders = sorted(base.glob("RouterOS client*"))
        except OSError:
            folders = []
        for folder in folders:
            if folder.is_dir():
                found = _exe_in(folder)
                if found is not None:
                    yield found

    program_files = env.get("PROGRAMFILES")
    if program_files:
        for name in EXE_NAMES:
            candidate = Path(program_files, "MikroTik", "RouterOS client", name)
            if candidate.is_file():
                yield candidate

    search_path = env.get("PATH")
    if search_path:
        for name in EXE_NAMES:
            hit = shutil.which(name, path=search_path)
            if hit:
                yield Path(hit)


def find_routeros_client() -> Path | None:
    """Locate a routeros_client executable, or ``None``.

    Order: ``MUXIVRA_ROUTEROS_CLIENT``, ``%LOCALAPPDATA%\\MikroTik\\RouterOS client*``,
    ``%PROGRAMFILES%\\MikroTik\\RouterOS client\\routeros_client64.exe``, ``...\\routeros_client.exe``, then
    ``PATH``.  Windows only -- on Linux and macOS this always returns ``None``
    (see the limits section of ``docs/routeros-client.md``).
    """
    if os.name != "nt":
        return None

    override = (os.environ.get(ENV_VAR) or "").strip().strip('"')
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate

    for candidate in _candidate_paths(os.environ):
        return candidate
    return None


# ---------------------------------------------------------------- launching
def _not_found_message(executable: Path | None) -> str:
    if executable is not None:
        return (
            f"RouterOS client executable {executable} does not exist. Set {ENV_VAR} to the "
            f"full path of RouterOS client.exe ({DOWNLOAD_URL})."
        )
    if os.name != "nt":
        return (
            "RouterOS client launching is implemented for Windows desktops only, so no "
            f"executable is searched on {platform.system() or os.name}. Install "
            f"RouterOS client manually or point {ENV_VAR} at it ({DOWNLOAD_URL})."
        )
    return (
        "RouterOS client executable not found. Install RouterOS client from "
        f"{DOWNLOAD_URL} or set {ENV_VAR} to the full path of RouterOS client.exe."
    )


def launch(
    address: str,
    username: str | None = None,
    port: int | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> dict:
    """Start RouterOS client as a separate window on ``address``.

    Returns ``{"pid", "executable", "command"}``; ``command`` is the exact argv
    handed to the OS and contains no secret.  ``executable`` skips discovery
    (used by the API when the user picked a path).

    Raises:
        RouterOS clientUnavailable: nothing to launch (``client_not_found``) or the OS
            refused to start it (``client_launch_failed``).
        ValueError: invalid address, username or port.
    """
    # Validate the request before touching the filesystem: a malformed address
    # must fail the same way whether or not RouterOS client is installed.
    target = _target(address, port)
    user = _user(username)

    exe = Path(os.fspath(executable)).expanduser() if executable is not None else find_routeros_client()
    if exe is None or not exe.is_file():
        raise RouterOS clientUnavailable("client_not_found", _not_found_message(exe))

    argv = _argv(str(exe), target, user)
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, never shell=True
            argv,
            close_fds=True,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RouterOS clientUnavailable(
            "client_launch_failed", f"Could not start {exe}: {exc}"
        ) from exc

    return {"pid": process.pid, "executable": str(exe), "command": argv}


# ---------------------------------------------------------------- capability
def _version_hint(executable: Path | None) -> str | None:
    """Best-effort version label taken from the install path, or ``None``.

    RouterOS client documents no version switch on its command line and MUXIVRA does not
    parse the Windows PE version resource, so this is a hint only:
    a folder named e.g. ``RouterOS client 3.41`` yields ``"3.41"``.
    """
    if executable is None:
        return None
    for text in (executable.parent.name, executable.stem):
        match = _VERSION_IN_NAME.search(text)
        if match:
            return match.group(1)
    return None


def describe() -> dict:
    """Report availability without raising; safe for a settings page.

    ``available`` means an executable was found, not that a session can connect:
    the router still needs the ``routeros_client`` service reachable and the password is
    asked by RouterOS client itself.
    """
    try:
        executable = find_routeros_client()
    except Exception:  # pragma: no cover - defensive: a probe must never break the UI
        executable = None
    return {
        "available": executable is not None,
        "executable": str(executable) if executable is not None else None,
        "version_hint": _version_hint(executable),
    }
