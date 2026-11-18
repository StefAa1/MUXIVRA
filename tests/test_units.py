import io
import os
import stat

import paramiko
import pytest

from sshdeck import config, security, sshconfig, sshconn


def test_ssh_config_parser_handles_wildcards_and_ports():
    text = """
Host a
  HostName 1.2.3.4
  User root
  Port 2022
Host b c
  HostName 5.6.7.8
Host *
  User nobody
"""
    hosts = sshconfig.parse(text)
    assert [h.alias for h in hosts] == ["a", "b", "c"]
    assert hosts[0].port == 2022
    assert hosts[1].hostname == "5.6.7.8"


def test_private_key_roundtrip_is_accepted():
    key = paramiko.Ed25519Key.generate() if hasattr(paramiko.Ed25519Key, "generate") else None
    if key is None:
        key = paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    key.write_private_key(buf)
    target = sshconn.Target(None, "t", "127.0.0.1", 22, "root", "key", buf.getvalue())
    loaded = sshconn._pkey(target)
    assert loaded.get_fingerprint() == key.get_fingerprint()


def test_broken_private_key_reports_clean_error():
    target = sshconn.Target(None, "t", "127.0.0.1", 22, "root", "key", "-----BEGIN OPENSSH PRIVATE KEY-----\nnope\n")
    with pytest.raises(sshconn.SshError) as excinfo:
        sshconn._pkey(target)
    assert excinfo.value.code == "bad_private_key"


def test_known_hosts_is_created_private():
    path = sshconn._ensure_known_hosts()
    assert os.path.exists(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_known_fingerprint_reads_paramiko_hostkeys():
    path = sshconn._ensure_known_hosts()
    key = paramiko.RSAKey.generate(2048)
    keys = paramiko.HostKeys(path)
    keys.add("example.test", key.get_name(), key)
    keys.save(path)
    assert sshconn.known_fingerprint("example.test") == security.fingerprint_of(key.asbytes())
    assert sshconn.forget_host_keys("example.test") == 1
    assert sshconn.known_fingerprint("example.test") is None


def test_fingerprint_format():
    fp = security.fingerprint_of(b"some-key-bytes")
    assert fp.startswith("SHA256:")
    assert fp != security.fingerprint_of(b"other-key-bytes")


def test_vault_rejects_tampered_blob():
    sealed = security.seal("hello")
    with pytest.raises(security.VaultError):
        security.open_sealed(sealed[:-4] + "AAAA")


def test_mask_hides_secret():
    assert security.mask("supersecretvalue") == "••••alue"
    assert security.mask("short") == "••••"
