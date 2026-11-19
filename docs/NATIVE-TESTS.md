# Native Windows test checklist (MUXIVRA)

Εκτελέσιμο με το χέρι σε πραγματικά Windows 10/11. Κάθε βήμα γράφει τι πρέπει να δεις.
Σημείωσε OK/FAIL και στείλε το `data\sshdeck.log` αν κάτι αποκλίνει.

## 0. Ποιο build τρέχεις
1. Άνοιξε **Ρυθμίσεις**: πρέπει να δείχνει `v<version> · build <commit> · portable` και τη διαδρομή `data:`.
2. Άνοιξε `data\sshdeck.log`: πρώτη γραμμή `MUXIVRA <version> (build <commit>, portable) | data=… | staging=…`.
3. OK όταν το `BUILD.txt` στον φάκελο της εφαρμογής συμφωνεί με τα παραπάνω.

## 1. Πρώτη εκκίνηση και vault
1. Διπλό κλικ `MUXIVRA.exe` → ανοίγει native παράθυρο, χωρίς browser.
2. Όρισε master password (≥8 χαρ.) → μπαίνει στο UI.
3. Στο `%LOCALAPPDATA%\MUXIVRA\data` πρέπει να υπάρχουν `sshdeck.db` και **`vault.key.dpapi`** (όχι `vault.key`).
4. Ρυθμίσεις → Vault: `Κατάσταση: ξεκλείδωτο`, `windows-dpapi`.
5. Πάτησε **Κλείδωμα** → `κλειδωμένο`; **Ξεκλείδωμα** → `ξεκλείδωτο`.
6. Auto-lock: βάλε 1 λεπτό, περίμενε >1 λεπτό χωρίς ενέργεια, άνοιξε credential → πρέπει να ζητά ξεκλείδωμα (409/μήνυμα), όχι να δουλεύει σιωπηλά.

## 2. Credentials
1. Keys → Νέο credential (password) με ειδικούς χαρακτήρες (`inss$1234$`) → αποθηκεύεται χωρίς σφάλμα.
2. Άνοιξε το `sshdeck.db` με SQLite viewer και κάνε αναζήτηση του plaintext: **δεν** πρέπει να βρεθεί.
3. Δεύτερο credential τύπου private key + passphrase → αποθηκεύεται.

## 3. SSH
1. Νέα σύνδεση σε host με **άγνωστο** host key → πρέπει να εμφανιστεί fingerprint και να ζητηθεί επιβεβαίωση, όχι σιωπηλή αποδοχή.
2. Μετά την επιβεβαίωση: shell ανοίγει, πληκτρολόγηση δουλεύει, resize αλλάζει το `$COLUMNS`.
3. Άλλαξε το host key στον server (ή δοκίμασε διπλό NAT) → η σύνδεση **απορρίπτεται** με `host_key_changed`.
4. Λάθος password → καθαρό μήνυμα `auth_failed`.

## 4. Telnet / COM / RouterOS client
1. Telnet σε συσκευή: banner + prompt, η ένδειξη «μη κρυπτογραφημένο» ορατή.
2. COM: Devices → refresh λίστας, σύνδεση με 115200 8N1· βγάλε το USB → σφάλμα αποσύνδεσης, όχι πάγωμα. Σύνδεση σε απασχολημένη θύρα → `busy`.
3. RouterOS client: σε συσκευή με `mgmt_ip` πάτησε «RouterOS client» → ανοίγει το επίσημο παράθυρο RouterOS client στη διεύθυνση. Χωρίς εγκατεστημένο RouterOS client → μήνυμα `client_not_found`. Το password **δεν** περνά ποτέ σε argv (έλεγχος στο docs/routeros-client.md).

## 5. Racks / καλώδια
1. Νέος χώρος → rack 42U → συσκευή σε U20 → ports (template π.χ. `eth` × 24) → σύνδεση με δεύτερη συσκευή **σε άλλο rack**.
2. Δεύτερη προσπάθεια στο ίδιο port → απόρριψη «Το port είναι ήδη συνδεδεμένο».
3. Πίνακας συνδέσεων: >40 εγγραφές, αναζήτηση, pagination (σύνολο ορατό), επεξεργασία καλωδίου, αποσύνδεση.
4. Εκτύπωση → print preview με rack + πίνακα, χωρίς rail.
5. Διαγραφή συσκευής → εμφανίζει πόσα ports/links θα χαθούν πριν επιβεβαιώσεις.
6. Κουμπί σύνδεσης συσκευής (SSH/Telnet/COM/RouterOS client) από το rack → ανοίγει το σωστό profile.

## 6. Διάγραμμα
1. Κόμβοι = συσκευές, γραμμές = συνδέσεις θυρών· labels θυρών/καλωδίου ορατά.
2. Auto-layout χωρίς επικαλύψεις, drag/zoom/pan, fit-to-view.
3. Κλείσε και ξανάνοιξε την εφαρμογή → οι θέσεις κόμβων παραμένουν.
4. Export SVG και PNG → αρχεία ανοίγουν.

## 7. Update (το κρίσιμο, μη επαληθευμένο)
1. Άνοιξε τη σελίδα των releases του repo και σημείωσε την έκδοση.
2. Ρυθμίσεις → GitHub repo `owner/name` → **Έλεγχος** → εμφανίζει νεότερη έκδοση.
3. **Κατέβασε & ετοίμασε** → `apply-update.cmd` στο staging dir (εκτός bundle).
4. Κλείσε το MUXIVRA (όχι force kill) και τρέξε το `apply-update.cmd`.
5. Αναμενόμενο: περιμένει το PID, κρατά backup, κάνει swap, ανοίγει τη νέα έκδοση, τα **δεδομένα και το vault παραμένουν**.
6. Αρνητικό: αντικατέστησε το `release.json.sig` με άκυρο → η προετοιμασία πρέπει να αποτύχει (`bad_signature`) και να μη γίνει swap.
7. Άνοιξε δεύτερο `pythonw.exe` (π.χ. άλλη εφαρμογή) πριν το 4 → πρέπει να **επιζήσει** (δεν σκοτώνουμε ξένα processes).
8. Σε αποτυχία robocopy: το script επαναφέρει το backup και η παλιά έκδοση ξεκινά.

## 8. Backup / restore
1. Ρυθμίσεις/CLI: export (JSON) → περιέχει sites/racks/devices/ports/links, **χωρίς** secrets.
2. Restore σε καθαρό data dir → ίδια δομή, χωρίς διπλοεγγραφές.
3. Αντίγραφο του `data\` σε άλλον Windows χρήστη → το vault **δεν** ανοίγει (DPAPI), όπως αναμένεται.
