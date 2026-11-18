"""Parse ~/.ssh/config style text into host drafts."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SshConfigHost:
    alias: str
    hostname: str = ""
    user: str = ""
    port: int = 22
    identity_file: str = ""
    extra: dict = field(default_factory=dict)


def parse(text: str) -> list[SshConfigHost]:
    hosts: list[SshConfigHost] = []
    current: list[SshConfigHost] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip().strip('"')
        if key == "host":
            current = []
            for alias in value.split():
                if any(ch in alias for ch in "*?!"):
                    current = []
                    break
                host = SshConfigHost(alias=alias)
                hosts.append(host)
                current.append(host)
            continue
        if not current:
            continue
        for host in current:
            if key == "hostname":
                host.hostname = value
            elif key == "user":
                host.user = value
            elif key == "port":
                try:
                    host.port = int(value)
                except ValueError:
                    pass
            elif key == "identityfile":
                host.identity_file = value.replace("~", "/root", 1) if value.startswith("~") else value
            else:
                host.extra[key] = value
    return hosts
