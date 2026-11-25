"""Password hashing, session tokens and the encrypted secret vault."""
import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

from cryptography.fernet import Fernet, InvalidToken

from . import config, db

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32


class VaultError(RuntimeError):
    pass


# ---------------------------------------------------------------- passwords
def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if not password or len(password) < 8:
        raise ValueError("password_too_short")
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN
    )
    return salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN
    )
    return hmac.compare_digest(digest, expected)


def user_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE id = 1").fetchone() is not None


def create_user(conn: sqlite3.Connection, username: str, password: str) -> None:
    salt, digest = hash_password(password)
    conn.execute(
        "INSERT INTO users(id, username, pw_salt, pw_hash, created_at) VALUES (1, ?, ?, ?, ?)",
        (username.strip() or "admin", salt, digest, db.now()),
    )


def check_login(conn: sqlite3.Connection, password: str) -> bool:
    row = conn.execute("SELECT pw_salt, pw_hash FROM users WHERE id = 1").fetchone()
    if not row:
        return False
    return verify_password(password, row["pw_salt"], row["pw_hash"])


def change_password(conn: sqlite3.Connection, password: str) -> None:
    salt, digest = hash_password(password)
    conn.execute("UPDATE users SET pw_salt = ?, pw_hash = ? WHERE id = 1", (salt, digest))


# ----------------------------------------------------------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn: sqlite3.Connection, user_agent: str = "", ttl: int | None = None) -> str:
    token = secrets.token_urlsafe(32)
    created = db.now()
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent) "
        "VALUES (?, 1, ?, ?, ?)",
        (_token_hash(token), created, created + (ttl or config.SESSION_TTL_SECONDS), user_agent[:300]),
    )
    return token


def validate_session(conn: sqlite3.Connection, token: str | None) -> bool:
    if not token:
        return False
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
    ).fetchone()
    if not row:
        return False
    if row["expires_at"] < db.now():
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        return False
    return True


def drop_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))


# -------------------------------------------------------------------- vault
_cached_fernet: Fernet | None = None

# Windows DPAPI: binds the vault key to the logged-in Windows user, so copying
# data\ to another machine does not reveal the key.
FORCE_DPAPI = False
_DPAPI_HOOKS: dict = {}
CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _dpapi_available() -> bool:
    return FORCE_DPAPI or os.name == "nt"


def _data_blob(data: bytes):
    """Build a real DATA_BLOB (cbData + pointer), keeping the buffer alive."""
    import ctypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer  # prevent garbage collection while Windows reads it
    return blob, DataBlob


def _dpapi_call(name: str, data: bytes) -> bytes:
    import ctypes

    src, DataBlob = _data_blob(data)
    out = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = getattr(crypt32, name)
    function.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(DataBlob),
    ]
    function.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = function(ctypes.byref(src), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        error = ctypes.get_last_error()
        raise VaultError(f"{name}_failed:{error}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes) -> bytes | None:
    """Encrypt with CryptProtectData (current user). None when unavailable."""
    if not _dpapi_available():
        return None
    if _DPAPI_HOOKS.get("protect"):
        return _DPAPI_HOOKS["protect"](data)
    return _dpapi_call("CryptProtectData", data)


def dpapi_unprotect(blob: bytes) -> bytes:
    if _DPAPI_HOOKS.get("unprotect"):
        return _DPAPI_HOOKS["unprotect"](blob)
    if not _dpapi_available():
        raise VaultError("dpapi_unavailable")
    return _dpapi_call("CryptUnprotectData", blob)


def _wrapped_key_path():
    from pathlib import Path

    return Path(str(config.VAULT_KEY_PATH) + ".dpapi")


def _validate_key(key: bytes) -> bytes:
    try:
        Fernet(key.strip())
    except Exception as exc:
        raise VaultError("vault_key_invalid") from exc
    return key.strip()


def _validate_saved_secrets(key: bytes | None) -> None:
    if not config.DB_PATH.exists():
        return
    cipher = Fernet(key) if key is not None else None
    with db.db() as conn:
        rows = conn.execute(
            """SELECT secret_enc FROM credentials WHERE length(secret_enc) > 0
            UNION ALL SELECT passphrase_enc FROM credentials WHERE length(passphrase_enc) > 0
            UNION ALL SELECT raw_enc FROM config_versions WHERE length(raw_enc) > 0
            UNION ALL SELECT value FROM settings
              WHERE length(value) > 0 AND (key = 'update_token_enc' OR key GLOB 'snmp_community:*')"""
        )
        for (blob,) in rows:
            if cipher is None:
                raise VaultError("vault_key_missing")
            try:
                cipher.decrypt(blob.encode("ascii") if isinstance(blob, str) else blob)
            except (InvalidToken, TypeError, ValueError) as exc:
                raise VaultError("vault_decrypt_failed") from exc


def _load_key() -> bytes:
    config.ensure_dirs()
    plain = config.VAULT_KEY_PATH
    wrapped = _wrapped_key_path()

    # A failed restore leaves existing ciphertext and key paths untouched.
    if not any(path.exists() and path.stat().st_size for path in (plain, wrapped)):
        _validate_saved_secrets(None)

    with config.file_lock(plain.with_name(".vault.lock")):
        blob = wrapped.read_bytes() if wrapped.exists() else b""
        if blob:
            try:
                key = _validate_key(dpapi_unprotect(blob))
            except VaultError as exc:
                raise VaultError("vault_key_unreadable") from exc
            _validate_saved_secrets(key)
            return key

        raw = plain.read_bytes() if plain.exists() else b""
        key = _validate_key(raw) if raw else None
        _validate_saved_secrets(key)
        key = key or Fernet.generate_key()

        if _dpapi_available():
            wrapped_blob = dpapi_protect(key)
            if not wrapped_blob:
                raise VaultError("dpapi_protect_failed")
            config.write_private_file(wrapped, wrapped_blob)
            plain.unlink(missing_ok=True)
        elif not raw:
            config.write_private_file(plain, key)
        return key


def verify_vault() -> None:
    """Check persisted ciphertext with its stored key, initializing an empty vault."""
    _load_key()


def vault_key_status() -> dict:
    """What protects the vault key on this machine (shown in Settings)."""
    wrapped = _wrapped_key_path()
    return {
        "protected": wrapped.exists() and _dpapi_available(),
        "mechanism": "windows-dpapi" if wrapped.exists() else "file-0600",
        "path": str(wrapped if wrapped.exists() else config.VAULT_KEY_PATH),
    }


_last_secret_use = 0.0
_locked = False  # explicit lock state: while True no secret operation is allowed
_VAULT_LOCK = threading.RLock()
AUTO_LOCK_SECONDS = int(os.environ.get("SSHDECK_AUTO_LOCK_SECONDS", "0"))  # 0 = off


def fernet() -> Fernet:
    """Return the vault cipher. While locked, every secret operation is refused."""
    global _cached_fernet, _last_secret_use
    with _VAULT_LOCK:
        enforce_auto_lock()
        if _locked:
            raise VaultError("vault_locked")
        if _cached_fernet is None:
            _cached_fernet = Fernet(_load_key())
        _last_secret_use = time.time()
        return _cached_fernet


def lock(reason: str = "manual") -> None:
    """Lock the vault: drop the key and refuse secret operations until unlock()."""
    global _cached_fernet, _locked
    with _VAULT_LOCK:
        _cached_fernet = None
        _locked = True


def unlock() -> None:
    """Explicit unlock: re-read the key (DPAPI/0600) and reset the idle clock."""
    global _cached_fernet, _last_secret_use, _locked
    with _VAULT_LOCK:
        lock()
        _cached_fernet = Fernet(_load_key())
        _locked = False
        _last_secret_use = time.time()


def is_locked() -> bool:
    return _locked


def auto_lock_expired() -> bool:
    """True when the vault has been idle longer than AUTO_LOCK_SECONDS."""
    if _locked or AUTO_LOCK_SECONDS <= 0 or not _last_secret_use:
        return False
    return (time.time() - _last_secret_use) > AUTO_LOCK_SECONDS


def enforce_auto_lock() -> bool:
    with _VAULT_LOCK:
        if auto_lock_expired():
            lock("idle")
            return True
        return False


def vault_state() -> dict:
    return {
        "locked": is_locked(),
        "auto_lock_seconds": AUTO_LOCK_SECONDS,
        "idle_seconds": int(time.time() - _last_secret_use) if _last_secret_use else None,
        "key": vault_key_status(),
    }


def seal(secret: str) -> str:
    return fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def open_sealed(blob: str) -> str:
    try:
        return fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise VaultError("vault_decrypt_failed") from exc


# ------------------------------------------------------------------ helpers
def mask(secret: str) -> str:
    """Display form for a secret: never the whole value."""
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 8 else ""
    return f"••••{tail}"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def fingerprint_of(key_blob: bytes) -> str:
    digest = hashlib.sha256(key_blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


__all__ = [
    "hash_password",
    "verify_password",
    "user_exists",
    "create_user",
    "check_login",
    "change_password",
    "create_session",
    "validate_session",
    "drop_session",
    "purge_expired",
    "seal",
    "open_sealed",
    "mask",
    "b64",
    "fingerprint_of",
    "VaultError",
    "time",
]
