# MUXIVRA

Self-hosted SSH / Telnet / serial workspace στο πρότυπο του Termius: inventory, encrypted
vault, multi-tab terminal, SFTP, snippets — και οι τρεις τύποι σύνδεσης στο ίδιο περιβάλλον.
FastAPI + xterm.js + paramiko/pyserial, χωρίς cloud και χωρίς λογαριασμό τρίτου.

```
┌ rail ─┬─ sidebar ──┬──────────── tabs ────────────┐
│Servers│ search     │ web-01 ✕  switch-01 ✕      + │
│Keys   │ ▾ homelab  │ ┌──────────────────────────┐ │
│Snipp. │  ● ssh     │ │  xterm.js  ⇄ websocket   │ │
│Files  │  ● telnet  │ │  ⇄ paramiko / socket /   │ │
│       │  ● COM3    │ │    pyserial              │ │
└───────┴────────────┴─┴──────────────────────────┴─┘
```

## Τι κάνει

- **Inventory** με groups, tags, notes, χρώμα· search· quick-connect palette (`Ctrl/⌘+K`).
- **SSH**: interactive shell με πολλά tabs, resize, UTF-8· password ή private key (+passphrase).
- **Telnet** (RFC 854): διαπραγμάτευση IAC (WILL/DO/WONT/DONT, NAWS για resize), CRLF line
  endings, prompt-based συσκευές.
- **Serial / COM**: baud, bits, parity, stop bits· λίστα θυρών από το μηχάνημα που τρέχει ο
  server (COM1… στα Windows, `/dev/ttyUSB*` στο Linux) — για switches, PLC, relay boards.
- **Vault**: passwords και private keys κρυπτογραφημένα με Fernet (AES-128-CBC + HMAC-SHA256)
  σε SQLite· το κλειδί είναι ξεχωριστό αρχείο `0600` και **στα Windows** τυλίγεται με
  **DPAPI** (`data/vault.key.dpapi`) — αντίγραφο σε άλλο μηχάνημα δεν το αποκρυπτογραφεί.
- **SFTP** (μόνο SSH): περιήγηση, upload, download, mkdir, rename, delete.
- **Snippets**: αποθηκευμένες εντολές που στέλνονται στο ενεργό session με ένα κλικ.
- **Import**: `~/.ssh/config` και **Keeper Secrets Manager** (βλ. παρακάτω).
- **Racks & καλώδια** (NETDOC-style, δικό μας module): χώροι → racks → συσκευές με U θέση →
  ports (bulk 24 τη φορά) → συνδέσεις με καλώδιο/μήκος. Ένα port μπορεί να είναι σε **μία μόνο**
  σύνδεση, οπότε οι διπλές καταγραφές απορρίπτονται με σαφές μήνυμα· εξαγωγή `links.csv`.
  Για ομάδες που χρησιμοποιούν και το [netdoc.app](https://netdoc.app), υπάρχει κουμπί
  που ανοίγει την υπηρεσία — δεν ανταλλάσσονται δεδομένα αυτόματα (δεν υπάρχει δημόσιο API).
- **Ενημερώσεις από GitHub**: Ρυθμίσεις → repo `owner/name` → έλεγχος release, λήψη του `.zip`
  με επαλήθευση `sha256`, και δημιουργία `apply-update.cmd` που κάνει backup, αντικατάσταση
  και επανεκκίνηση (το αρχείο τρέχει *αφού* κλείσει η εφαρμογή).
- **Host keys (TOFU)**: το πρώτο SSH key καταγράφεται· αν αλλάξει, η σύνδεση **απορρίπτεται**.

## Εκκίνηση

```bash
cd /root/sshdeck
./run.sh                     # http://127.0.0.1:8722
```

Πρώτη φορά: άνοιξε το URL και όρισε master password (≥8 χαρ.). Αυτό είναι το μόνο
credential του panel· τα SSH secrets μπαίνουν μετά, μέσα από το UI (Keychain).

Μόνιμη υπηρεσία (systemd, μόνο loopback):

```bash
install -m 644 deploy/sshdeck.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now sshdeck
systemctl status sshdeck --no-pager
```

Πρόσβαση από άλλο μηχάνημα χωρίς να εκτεθεί το panel:

```bash
ssh -N -L 8722:127.0.0.1:8722 root@<server>   # μετά http://localhost:8722
```

Για δημόσια έκθεση: βάλε reverse proxy με TLS (nginx/caddy) **και** `--host 0.0.0.0`
μόνο αφού έχει οριστεί master password· το cookie γίνεται `Secure` αυτόματα πίσω από TLS.

## Desktop mode (native window, χωρίς browser)

```bash
.venv/bin/python -m sshdeck desktop            # native παράθυρο
.venv/bin/python -m sshdeck desktop --selftest # headless έλεγχος του handshake
```

- Ο engine (FastAPI) ξεκινά σε **τυχαία θύρα loopback**· το παράθυρο φορτώνει ένα
  one-time token URL που εξαργυρώνεται σε session cookie και σβήνεται από τη διεύθυνση.
- Στο desktop mode δεν υπάρχει login: όριο ασφαλείας είναι η συνεδρία του λειτουργικού.
  Ο HTTP server ζει μόνο όσο είναι ανοιχτό το παράθυρο.
- Linux backend: `pip install 'pywebview[qt]'` (ή GTK)· Windows: Edge WebView2, που
  υπάρχει ήδη στα 10/11.
- Η web λειτουργία (`sshdeck serve`) μένει διαθέσιμη για απομακρυσμένη πρόσβαση με
  master password.

## Windows portable (χωρίς εγκατάσταση)

```bash
.venv/bin/python scripts/build_windows_portable.py
# -> dist/MUXIVRA-portable-win64.zip  (~20 MB)
```

Το zip περιέχει CPython 3.12 embeddable + όλα τα win_amd64 wheels + την εφαρμογή:

```
MUXIVRA-portable-win64/
  MUXIVRA.cmd            <- διπλό κλικ: native παράθυρο (χωρίς console/browser)
  MUXIVRA-debug.cmd      <- ίδιο, με console + devtools για διάγνωση
  MUXIVRA-web.cmd        <- web mode στο http://127.0.0.1:8722
  app/sshdeck, app/web   <- κώδικας
  runtime/python         <- python.exe + pythonw.exe (embeddable, vcruntime included)
  runtime/libs           <- fastapi, paramiko, cryptography, pyserial, pywebview…
  data/                  <- δημιουργείται στο πρώτο run
```

Δεν χρειάζεται admin, Python ή VC++ redist. Τα δεδομένα μένουν στο `data\` δίπλα στο exe·
Τα δεδομένα **δεν** μένουν πλέον μέσα στον φάκελο της εφαρμογής: πάνε σε
`%LOCALAPPDATA%\MUXIVRA\data` (Windows) ή `~/.local/share/muxivra/data` (Linux), ώστε μια
ενημέρωση να αντικαθιστά μόνο τα binaries. Παλιός φάκελος `<bundle>\data` υιοθετείται στην
πρώτη εκκίνηση· αν το νέο σημείο έχει ήδη βάση/vault, **δεν** αντικαθίσταται — τα παλιά
αντιγράφονται σε `legacy-import-<timestamp>` και γράφεται `migration.json`.

Για πραγματικό single-file `.exe` χρειάζεται Windows toolchain (PyInstaller):

```bat
pyinstaller --onefile --name MUXIVRA --add-data "web;web" --hidden-import uvicorn ^
  --hidden-import paramiko --hidden-import serial -c sshdeck\cli.py
```

Το portable zip είναι λειτουργικά ισοδύναμο χωρίς αυτό το βήμα.

## Keeper Secrets Manager (KSM)

Read-only import: τα SSH records του Keeper γίνονται hosts + vault credentials.

```bash
pip install keeper-secrets-manager-core          # προαιρετικό extra
.venv/bin/python -m sshdeck keeper-import --token-file runtime/ksm-token.txt --dry-run
.venv/bin/python -m sshdeck keeper-import --token-file runtime/ksm-token.txt --group Keeper
# ή από εξαγμένο JSON (χωρίς SDK/token):
.venv/bin/python -m sshdeck keeper-import --from-json testdata/keeper-sample.json
```

- Κάθε device θέλει **δικό του One-Time Access Token**· το πρώτο `get_secrets()` το δεσμεύει
  και γράφει `data/ksm-config.json` (0600). Μετά δουλεύει χωρίς token.
- Mapping: `host/hostname`, `port`, `login/username`, `password`, `key/private_key`, `passphrase`,
  `notes` — και από `fields` και από `custom_fields` (dict ή λίστα `{type,value}`).
- Τίποτα δεν γράφεται πίσω στο Keeper· revoke του KSM application γίνεται από το Keeper console.
- Το KSM είναι entitlement του Keeper πλάνου — επιβεβαιώσέ το στο Keeper admin console.

## CLI

```bash
.venv/bin/python -m sshdeck status                  # κατάσταση, counts, paths
.venv/bin/python -m sshdeck serve --open-browser     # server + άνοιγμα browser
.venv/bin/python -m sshdeck set-password            # αλλαγή master password (revoke sessions)
.venv/bin/python -m sshdeck issue-session --ttl 600 # token για headless/operator χρήση
.venv/bin/python -m sshdeck reset-account           # ξεχνά τον λογαριασμό, κρατά inventory+vault
.venv/bin/python -m sshdeck keeper-import --help    # Keeper import (token ή JSON)
```

## Δεδομένα & backup

| Path | Περιεχόμενο |
|---|---|
| `data/sshdeck.db` | hosts (ssh/telnet/serial), credentials (encrypted), sessions, events |
| `vault.key` (Linux) / `vault.key.dpapi` (Windows, δεμένο με DPAPI) | κλειδί Fernet — **χωρίς αυτό τα secrets δεν ανακτώνται** |
| `data/known_hosts` | TOFU host keys (`0600`) |
| `data/ksm-config.json` | bound Keeper device config (`0600`, αν χρησιμοποιείται KSM) |

Backup: αντίγραφο του DATA_DIR με σταματημένη την υπηρεσία, ή JSON backup της τεκμηρίωσης
(χώροι/racks/συσκευές/ports/συνδέσεις) από το UI — το JSON **δεν** περιέχει secrets.
Το `vault.key` είναι το
root of trust: φύλαξέ το μαζί με τη βάση, ποτέ μόνο του σε κοινόχρηστο σημείο.

## Ασφάλεια

- Master password: `scrypt(n=2^14, r=8, p=1)`, sessions σε DB με hash του token, cookie
  `HttpOnly` + `SameSite=Lax` (+`Secure` σε https), TTL 12h.
- Κάθε mutation απαιτεί header `x-requested-with: sshdeck` (CSRF barrier)· τα GET είναι read-only.
- Login throttle: 8 αποτυχίες / 5 λεπτά ανά IP → `429`.
- CSP `default-src 'self'`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`,
  `no-store` στα API responses.
- Secrets δεν επιστρέφονται ποτέ από το API· μόνο `••••` + 4 τελευταίοι χαρακτήρες.
- Host key change → `host_key_changed`, η σύνδεση δεν ολοκληρώνεται.
- Default bind `127.0.0.1`: δεν εκτίθεται τίποτα στο δίκτυο αν δεν το ζητήσεις.

## Tests

```bash
.venv/bin/pytest                       # 56 tests: API, vault, SSH/Telnet/Serial, Keeper, desktop, CLI
.venv/bin/python scripts/live_check.py # 8 live checks σε πραγματικό sshd + mock telnet/σειριακή
```

Το Windows bundle επαληθεύεται κάτω από Wine 8 (server + SSH session μέσω του packaged
python). Το Wine χρειάζεται shim `bcryptprimitives.dll` (`testdata/wine_shim.c`) το οποίο
**δεν** μπαίνει στο zip — τα Windows 10/11 έχουν το κανονικό DLL.

## Όρια (δεν υπάρχουν ακόμα)

Port forwarding / tunnels, jump hosts (ProxyJump), team sync & sharing, session recording,
mobile/desktop clients, agent forwarding, Keeper write-back, SFTP resume για μεγάλα αρχεία
(>200MB μπλοκάρεται), σειριακά macros/DTR/RTS. Το remote host key γράφεται μόνο μέσω TOFU —
δεν υπάρχει UI για manual pinning πριν την πρώτη σύνδεση.

## Δομή

```
sshdeck/  __init__.py config.py db.py security.py api.py main.py cli.py desktop.py
          sshconn.py (ssh/sftp/TOFU)  transports.py (telnet/serial)  keeper.py  sshconfig.py
web/      index.html styles.css app.js + vendor/xterm.js
tests/    test_api.py test_units.py test_transports.py test_keeper.py conftest.py
scripts/  build_windows_portable.py live_check.py fake_device.py wine_e2e.py
          seed_demo.py point_serial_host.py
deploy/   sshdeck.service
```
