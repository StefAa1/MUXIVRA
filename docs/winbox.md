# WinBox

Το `sshdeck/winbox.py` ανοίγει το **επίσημο WinBox** της MikroTik ως ξεχωριστό παράθυρο για μια
συσκευή RouterOS. Δεν μιλάει το protocol του WinBox και **δεν** ενσωματώνει το παράθυρό του στο
MUXIVRA: δίνει στη MikroTik τη διεύθυνση και (προαιρετικά) το username, και το WinBox ζητάει μόνο
του το password.

Όλες οι δηλώσεις για δυνατότητες του WinBox παρακάτω διαβάστηκαν **2026-11-18** από τις επίσημες
σελίδες του πίνακα «Πηγές». Το `help.mikrotik.com` είναι πλέον παγωμένο (η σελίδα το δηλώνει ρητά)
και η ζωντανή τεκμηρίωση είναι το `manual.mikrotik.com`· και τα δύο αναφέρονται όπου χρειάζεται.

## Πηγές

| # | Τι τεκμηριώνει | URL (ανακτήθηκε 2026-11-18) |
|---|---|---|
| 1 | Το WinBox είναι βοηθητικό πρόγραμμα διαχείρισης RouterOS με GUI· το **v4** είναι native εφαρμογή για Windows, Linux και macOS «no Wine or emulation required»· το **v3** είναι native Win32/Win64 binary που τρέχει σε Linux/macOS μέσω Wine | <https://manual.mikrotik.com/docs/management-tools/winbox/> · <https://manual.mikrotik.com/docs/management-tools/winbox-legacy/> |
| 2 | Σύνδεση στην υπηρεσία `winbox` σε **TCP 8291**, αλλαγή θύρας με `/ip/service/set winbox port=…`, μορφή `192.168.88.1:9999`· IPv6 literals σε αγκύλες (`[db8::1]`, `[db8::1]:8299`) | <https://manual.mikrotik.com/docs/management-tools/winbox/> (Login section, Table 1) · <https://help.mikrotik.com/docs/spaces/ROS/pages/328129/WinBox> (IPv6 connectivity) |
| 3 | **Command line**: `winbox.exe [<connect-to> [<login> [<password>]] <session\|workspace>]`, με παράδειγμα `winbox.exe 10.5.101.1 admin "" "<own>"`· ξεχωριστή μορφή `--romon` | <https://help.mikrotik.com/docs/spaces/ROS/pages/328129/WinBox> (ενότητα «Command Line») |
| 4 | Διανομή: **v4.5** (2026-11-03) για macOS universal, Linux 64-bit, Windows 64-bit, Windows arm64 | <https://mikrotik.com/download/winbox> |
| 5 | Εγκατάσταση Windows: ZIP που περιέχει `WinBox.exe`, χωρίς εγκατάσταση («no installation required»)· το ZIP πρέπει να εξαχθεί σε δικό του υποφάκελο για να δουλεύει το self-update | <https://manual.mikrotik.com/docs/management-tools/winbox/> (Download and Installation) |
| 6 | Ασφάλεια συνεδρίας: EV certificate, ECSRP key exchange, αμφίδρομη επαλήθευση password (χωρίς MITM), AES128-CBC-SHA | <https://manual.mikrotik.com/docs/management-tools/winbox/> (Summary) |
| 7 | Αποθηκευμένες συσκευές σε `.cdb`, με δράσεις Open/Move/Import/Export και **Set file password** για κρυπτογράφηση | <https://manual.mikrotik.com/docs/management-tools/winbox/> (Saved device list) |
| 8 | Οι MAC συνεδρίες «use network broadcasts and are not 100% reliable» — προτιμάμε IP | <https://manual.mikrotik.com/docs/management-tools/winbox/> (warning, Login section) |
| 9 | Στο v3 δεν γίνονται όλα από το WinBox (π.χ. αλλαγή MAC διεπαφής) | <https://manual.mikrotik.com/docs/management-tools/winbox-legacy/> |

## Τι υποστηρίζεται

| Δυνατότητα | Κατάσταση |
|---|---|
| Εντοπισμός εγκατεστημένου WinBox | `find_winbox()` — `MUXIVRA_WINBOX`, `%LOCALAPPDATA%\MikroTik\WinBox*`, `%PROGRAMFILES%\MikroTik\WinBox\winbox64.exe`, `…\winbox.exe`, `PATH` |
| Εκκίνηση σε εξωτερικό παράθυρο | `launch(address, username=None, port=None, executable=None)` → `{"pid", "executable", "command"}` |
| Επιλογή θύρας | `port=` → `host:port` ή `[v6]:port`, όπως τεκμηριώνει η πηγή 2 |
| Κατάσταση για UI/ρυθμίσεις | `describe()` → `{"available", "executable", "version_hint"}`, χωρίς exception |
| Έλεγχος argv πριν την εκκίνηση | `build_args(...)` — καθαρή συνάρτηση, χωρίς filesystem/διεργασία |
| Ενσωμάτωση του WinBox UI στο MUXIVRA | **Όχι** — `WINDOW_MODE = "external-window"` |
| Αποστολή password/token στο WinBox | **Όχι** — καμία θέση password στο argv, κανένα προσωρινό αρχείο |

## Γιατί δεν γίνεται embed

Η επίσημη τεκμηρίωση —
<https://manual.mikrotik.com/docs/management-tools/winbox/> (πηγές 1, 5) και
<https://help.mikrotik.com/docs/spaces/ROS/pages/328129/WinBox> (πηγή 3) — περιγράφει το WinBox ως
**αυτόνομη εφαρμογή** με δικό της login interface και δικά της παράθυρα (MDI μέσα στο κύριο
παράθυρό του), με δικό του `.cdb` και δικό του self-update. Δεν τεκμηριώνει πουθενά API
ενσωμάτωσης, plugin, IPC/RPC επιφάνεια ή δυνατότητα φιλοξενίας του παραθύλου (window handle / OLE /
ActiveX) μέσα σε άλλη εφαρμογή.

Αυτό είναι **συμπέρασμα από την απουσία τεκμηρίωσης**, όχι δήλωση της MikroTik ότι απαγορεύεται. Το
να «κλέψουμε» το παράθυρο ενός ξένου, κλειστού binary (π.χ. re-parenting του HWND) θα ήταν
αντίστροφη μηχανική, θα εξαρτιόταν από την έκδοση και θα έσπαγε σε κάθε release — γι' αυτό το
MUXIVRA δεν το κάνει. Άνοιγμα σε ξεχωριστό παράθυρο είναι η μόνη υποστηριζόμενη συμπεριφορά.

## Γιατί δεν περνάμε password στο command line

Η επίσημη σύνταξη (πηγή 3) έχει **θέση password** ως τρίτο positional όρισμα:

```
winbox.exe [<connect-to> [<login> [<password>]] <session|workspace>]
winbox.exe 10.5.101.1 admin "<password>"   # 😱 τι ΔΕΝ κάνουμε
```

Αν τη χρησιμοποιήσουμε, το μυστικό μένει στο argv της διεργασίας για όλη τη διάρκεια ζωής της και
είναι αναγνώσιμο από άλλα τοπικά εργαλεία (π.χ. Task Manager / WMI `Win32_Process.CommandLine`,
process listings) και από οποιοδήποτε audit/log κρατά argv. Το ίδιο ισχύει για προσωρινό `.cmd`/script
με το password μέσα. Γι' αυτό:

- το `launch()` δέχεται **μόνο** `address`, `username`, `port` (και προαιρετικά `executable` path) — δεν
  υπάρχει παράμετρος password.
- το argv σταματά στη θέση του login: `[exe, "192.168.88.1:8299", "admin"]`.
- το WinBox ζητάει το password στο δικό του login dialog (πηγή 1, Login section).
- το vault του MUXIVRA **δεν** εξάγει credentials προς το WinBox. Αν ο χρήστης θέλει αποθήκευση,
  μπορεί να προσθέσει τη συσκευή στο `Saved` του WinBox, με προαιρετικό **Set file password** στο
  `.cdb` (πηγή 7) — αυτό είναι επιλογή του χρήστη μέσα στο WinBox, όχι κάτι που κάνει το MUXIVRA.

Επειδή δεν περνάμε password, το WinBox θα το ζητήσει — αναμενόμενη συμπεριφορά, όχι σφάλμα.

## API

```python
from sshdeck import winbox

winbox.find_winbox()            # Path | None (Windows only)
winbox.describe()               # {"available": bool, "executable": str|None, "version_hint": str|None}
winbox.build_args(exe, "192.168.88.1", username="admin", port=8299)
winbox.launch("192.168.88.1", username="admin", port=8299)
# -> {"pid": 12345,
#     "executable": r"C:\Program Files\MikroTik\WinBox\winbox64.exe",
#     "command": [..., "192.168.88.1:8299", "admin"]}
```

### Σειρά εντοπισμού (`find_winbox`)

1. `MUXIVRA_WINBOX` (πλήρες path — καλύπτει portable ZIP οπουδήποτε, πηγή 5).
2. `%LOCALAPPDATA%\MikroTik\WinBox*` (υποφάκελοι, με προτίμηση `winbox64.exe` έναντι `winbox.exe`).
   Προσοχή: αυτό **δεν** είναι τεκμηριωμένος installer path της MikroTik — το WinBox είναι portable
   ZIP χωρίς εγκατάσταση (πηγή 5) — το ψάχνουμε γιατί εκεί καταλήγουν συχνά extracted builds.
3. `%PROGRAMFILES%\MikroTik\WinBox\winbox64.exe`, μετά `…\winbox.exe`.
4. `PATH` (και τα δύο ονόματα).

Σε Linux/macOS επιστρέφει πάντα `None` — βλ. «Όρια».

### Σφάλματα

| Τύπος | `code` | Πότε |
|---|---|---|
| `WinBoxUnavailable` (υποκλάση `RuntimeError`) | `winbox_not_found` | δεν βρέθηκε εκτελέσιμο, ή το δοσμένο `executable=` δεν υπάρχει |
| `WinBoxUnavailable` | `winbox_launch_failed` | το εκτελέσιμο υπάρχει αλλά το OS αρνήθηκε να το ξεκινήσει (`OSError`) |
| `ValueError` | — | κενή/μη έγκυρη διεύθυνση, διεύθυνση που αρχίζει με `-`, χαρακτήρες ελέγχου, θύρα εκτός 1–65535, θύρα ήδη μέσα στη διεύθυνση |

Το `WinBoxUnavailable` έχει `code`, `message`, `str(exc)` και `as_dict()` για απευθείας JSON.

Έλεγχοι εισόδου: η διεύθυνση και το username δεν μπορούν να αρχίζουν με `-` (θα διαβάζονταν ως
switch, π.χ. `--romon`) και δεν δέχονται χαρακτήρες ελέγχου. Διπλή θύρα (`1.2.3.4:9999` + `port=`)
απορρίπτεται αντί να μαντέψουμε ποια ισχύει.

## Όρια όταν λείπει το εκτελέσιμο

- `launch()` → `WinBoxUnavailable("winbox_not_found")` με μήνυμα που λέει τι λείπει και πού να το
  κατεβάσεις· **δεν** πετάει άλλο σφάλμα και δεν αφήνει μισοξεκινημένη κατάσταση.
- `describe()` → `{"available": False, "executable": None, "version_hint": None}` χωρίς exception,
  ώστε σελίδα ρυθμίσεων να μη σκάει. Το `available: True` σημαίνει «βρέθηκε εκτελέσιμο», **όχι** «η
  σύνδεση θα πετύχει»: χρειάζεται προσβάσιμη υπηρεσία `winbox` (8291/TCP) και firewall, και το
  password το ζητά το WinBox.
- **Linux/macOS**: το module επιστρέφει σκόπιμα `None`. Αυτό είναι περιορισμός αυτής της έκδοσης
  (Windows-only policy), **όχι** ισχυρισμός ότι δεν υπάρχει WinBox για Linux/macOS — η πηγή 4 δείχνει
  καθαρά builds v4.5 για macOS universal και Linux 64-bit. Όποιος θέλει εκκίνηση εκεί περνά
  `executable=` ρητά ή επεκτείνει την discovery.
- `version_hint` προέρχεται **μόνο** από το path (π.χ. φάκελος `WinBox 3.41` → `"3.41"`). Δεν
  διαβάζουμε το PE version resource του `.exe` και η τεκμηριωμένη γραμμή εντολών του WinBox δεν έχει
  switch έκδοσης, άρα `None` σημαίνει «άγνωστο», όχι «παλιό».

## Εγκατάσταση (Windows)

1. Κατέβασε από <https://mikrotik.com/download/winbox> (v4.5: Windows 64-bit ή arm64). Το
   `winbox64.exe` είναι το 64-bit build της σειράς v3 και αναφέρεται στην επίσημη σελίδα WinBox· γι'
   αυτό το ψάχνουμε κι αυτό.
2. Εξήγαγε το ZIP σε **δικό του, καθαρό υποφάκελο** — απαιτείται για το self-update (πηγή 5) — π.χ.
   `C:\Program Files\MikroTik\WinBox\` ή `%LOCALAPPDATA%\MikroTik\WinBox\`.
3. Δεν χρειάζεται installer· τρέχει το `WinBox.exe` (πηγή 5).
4. Αν το έβαλες αλλού: `set MUXIVRA_WINBOX=C:\Tools\WinBox\WinBox.exe` (ή βάλε τον φάκελο στο `PATH`).

Επιβεβαίωση από Python:

```python
from sshdeck import winbox
winbox.describe()   # {'available': True, 'executable': '...\\WinBox.exe', 'version_hint': None}
```

## Τι δεν κάνει

- Δεν μιλάει το WinBox protocol ούτε κάνει neighbor/RoMON discovery — αυτά γίνονται μέσα στο WinBox.
- Δεν περνάει credentials και δεν αποθηκεύει τίποτα για λογαριασμό του WinBox.
- Δεν ελέγχει αν η υπηρεσία `winbox` είναι ανοιχτή στη συσκευή (firewall/θύρα): το βλέπει ο χρήστης
  ως αποτυχία σύνδεσης μέσα στο WinBox.
- Δεν αντικαθιστά το SSH/WebFig: ό,τι δεν γίνεται από το WinBox (πηγή 9) παραμένει δουλειά του
  terminal.
