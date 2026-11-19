"""SQLite storage: schema, migrations and small query helpers."""
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA_VERSION = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    username   TEXT NOT NULL,
    pw_salt    BLOB NOT NULL,
    pw_hash    BLOB NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS credentials (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('password', 'key')),
    username       TEXT NOT NULL DEFAULT '',
    secret_enc     TEXT NOT NULL,
    passphrase_enc TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hosts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    label             TEXT NOT NULL,
    hostname          TEXT NOT NULL,
    port              INTEGER NOT NULL DEFAULT 22,
    username          TEXT NOT NULL,
    credential_id     INTEGER REFERENCES credentials(id) ON DELETE SET NULL,
    group_name        TEXT NOT NULL DEFAULT 'General',
    tags              TEXT NOT NULL DEFAULT '',
    color             TEXT NOT NULL DEFAULT '#3ddc97',
    notes             TEXT NOT NULL DEFAULT '',
    protocol          TEXT NOT NULL DEFAULT 'ssh',
    serial_port       TEXT NOT NULL DEFAULT '',
    baud              INTEGER NOT NULL DEFAULT 115200,
    bytesize          INTEGER NOT NULL DEFAULT 8,
    parity            TEXT NOT NULL DEFAULT 'N',
    stopbits          REAL NOT NULL DEFAULT 1,
    last_connected_at REAL,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS snippets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    command    TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id   INTEGER,
    kind      TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);

CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    code       TEXT NOT NULL DEFAULT '',
    color      TEXT NOT NULL DEFAULT '#3ddc97',
    notes      TEXT NOT NULL DEFAULT '',
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sites (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS racks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    units   INTEGER NOT NULL DEFAULT 42
);

CREATE TABLE IF NOT EXISTS devices (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rack_id   INTEGER NOT NULL REFERENCES racks(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'switch',
    units     INTEGER NOT NULL DEFAULT 1,
    position  INTEGER NOT NULL DEFAULT 1,
    note      TEXT NOT NULL DEFAULT '',
    host_id   INTEGER REFERENCES hosts(id) ON DELETE SET NULL,
    vendor    TEXT NOT NULL DEFAULT '',
    model     TEXT NOT NULL DEFAULT '',
    mgmt_ip   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ports (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'rj45',
    UNIQUE(device_id, name)
);

CREATE TABLE IF NOT EXISTS links (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    port_a   INTEGER NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
    port_b   INTEGER NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
    cable    TEXT NOT NULL DEFAULT '',
    length_m REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_profiles (
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    host_id   INTEGER NOT NULL REFERENCES hosts(id) ON DELETE CASCADE,
    PRIMARY KEY (device_id, host_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_links_port_a ON links(port_a);
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_port_b ON links(port_b);

-- a port may appear at most once across *both* columns: the two unique
-- indexes above do not catch a port that is port_a in one row and port_b in
-- another, so the rule is enforced with triggers as well
CREATE TRIGGER IF NOT EXISTS links_one_endpoint_ins
BEFORE INSERT ON links
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM links
     WHERE port_a IN (NEW.port_a, NEW.port_b)
        OR port_b IN (NEW.port_a, NEW.port_b)
  ) THEN RAISE(ABORT, 'port_already_linked') END;
END;

CREATE TRIGGER IF NOT EXISTS links_one_endpoint_upd
BEFORE UPDATE ON links
BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM links
     WHERE id <> NEW.id
       AND (port_a IN (NEW.port_a, NEW.port_b) OR port_b IN (NEW.port_a, NEW.port_b))
  ) THEN RAISE(ABORT, 'port_already_linked') END;
END;
"""


def now() -> float:
    return time.time()


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


HOST_COLUMNS = {
    "protocol": "TEXT NOT NULL DEFAULT 'ssh'",
    "serial_port": "TEXT NOT NULL DEFAULT ''",
    "baud": "INTEGER NOT NULL DEFAULT 115200",
    "bytesize": "INTEGER NOT NULL DEFAULT 8",
    "parity": "TEXT NOT NULL DEFAULT 'N'",
    "stopbits": "REAL NOT NULL DEFAULT 1",
    "flow": "TEXT NOT NULL DEFAULT 'none'",
    "uuid": "TEXT NOT NULL DEFAULT ''",
}


DEVICE_COLUMNS = {
    "host_id": "INTEGER REFERENCES hosts(id) ON DELETE SET NULL",
    "vendor": "TEXT NOT NULL DEFAULT ''",
    "model": "TEXT NOT NULL DEFAULT ''",
    "mgmt_ip": "TEXT NOT NULL DEFAULT ''",
}


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if not existing:
        return  # table not created yet; SCHEMA handles it
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL,
    host_id     INTEGER,
    vendor      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'ssh',
    raw_enc     BLOB,
    sha256      TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_config_device ON config_versions(device_id, created_at DESC);

CREATE TABLE IF NOT EXISTS planned_links (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_device  INTEGER NOT NULL,
    to_device    INTEGER NOT NULL,
    from_port    TEXT NOT NULL DEFAULT '',
    to_port      TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS device_observed (
    device_id   INTEGER PRIMARY KEY,
    host_id     INTEGER,
    vendor      TEXT NOT NULL DEFAULT '',
    data_json   TEXT NOT NULL DEFAULT '{}',
    collected_at REAL NOT NULL
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema without losing data."""
    _add_missing_columns(conn, "hosts", HOST_COLUMNS)
    _add_missing_columns(conn, "sites", {"customer_id": "INTEGER REFERENCES customers(id)"})
    _add_missing_columns(conn, "hosts", {"customer_id": "INTEGER REFERENCES customers(id)"})
    _add_missing_columns(conn, "credentials", {"customer_id": "INTEGER REFERENCES customers(id)"})
    from . import customers as _customers

    _customers.rename_unclassified(conn)
    _customers.backfill_ownership(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sites_customer ON sites(customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hosts_customer ON hosts(customer_id)")
    # fill stable identities for pre-0.4.3 rows (UUIDs, never labels)
    import uuid as _uuid

    for row in conn.execute("SELECT id FROM hosts WHERE uuid IS NULL OR uuid = ''").fetchall():
        conn.execute("UPDATE hosts SET uuid = ? WHERE id = ?", (_uuid.uuid4().hex, row["id"]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_hosts_uuid ON hosts(uuid) WHERE uuid <> ''")
    _add_missing_columns(conn, "devices", DEVICE_COLUMNS)
    # v8: planned vs documented equipment, configuration history, observed state
    _add_missing_columns(conn, "devices", {"origin": "TEXT DEFAULT 'documented'"})
    conn.executescript(CONFIG_SCHEMA)
    conn.execute(
        "INSERT INTO settings(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _schema_on_disk() -> int:
    if not config.DB_PATH.exists():
        return -1
    try:
        conn = connect()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
            return int(row["value"]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def snapshot(destination) -> str:
    """Consistent copy through the SQLite backup API (WAL-safe, unlike copy2)."""
    from pathlib import Path as _Path

    destination = _Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = connect()
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return str(destination)


def backup_before_migration() -> str | None:
    """Copy the DB before an upgrade so a rollback of the binaries still opens it."""
    import shutil

    current = _schema_on_disk()
    if current < 0 or current >= SCHEMA_VERSION:
        return None
    target = config.DATA_DIR / "backups"
    target.mkdir(parents=True, exist_ok=True)
    destination = target / f"sshdeck-v{current}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    return snapshot(destination)


def init() -> None:
    config.ensure_dirs()
    backup_before_migration()
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        migrate(conn)


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def log_event(host_id, kind: str, detail: str = "", conn: sqlite3.Connection | None = None) -> None:
    """Record an event. Pass the caller's connection when inside an open write
    transaction — opening a second connection there deadlocks on the write lock."""
    sql = "INSERT INTO events(host_id, kind, detail, created_at) VALUES (?, ?, ?, ?)"
    params = (host_id, kind, detail[:500], now())
    if conn is not None:
        conn.execute(sql, params)
        return
    with db() as own:
        own.execute(sql, params)
