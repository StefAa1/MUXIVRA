"""Probe the Windows DPAPI path without importing cryptography.

Runs under the bundled Windows Python (Wine or real Windows):
    runtime/python/python.exe tools/dpapi_probe.py
Stubs cryptography so sshdeck.security imports, then exercises the real
CryptProtectData / CryptUnprotectData ctypes code.
"""
import sys
import types


def install_stub():
    package = types.ModuleType("cryptography")
    fernet = types.ModuleType("cryptography.fernet")

    class Fernet:
        def __init__(self, key):
            self.key = key

    class InvalidToken(Exception):
        pass

    fernet.Fernet = Fernet
    fernet.InvalidToken = InvalidToken
    package.fernet = fernet
    sys.modules["cryptography"] = package
    sys.modules["cryptography.fernet"] = fernet


def main() -> int:
    install_stub()
    from sshdeck import security

    if not security._dpapi_available():
        print("FAIL dpapi not available (os.name=%r)" % __import__("os").name)
        return 1

    secret = b"inss-vault-key-0123456789"
    blob = security.dpapi_protect(secret)
    if not blob:
        print("FAIL protect returned empty")
        return 1
    back = security.dpapi_unprotect(blob)
    ok = back == secret
    print(f"protect: {len(blob)} bytes, prefix={blob[:8].hex()}")
    print(f"unprotect matches: {ok}")
    print(f"plaintext leaked in blob: {secret in blob}")
    return 0 if ok and secret not in blob else 1


if __name__ == "__main__":
    sys.exit(main())
