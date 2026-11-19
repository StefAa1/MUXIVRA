# Πώς γίνονται τα updates μέσα από το GitHub

Το MUXIVRA δεν κατεβάζει «ό,τι βρει». Κάθε έκδοση έχει **υπογεγραμμένο manifest**
(`release.json` + `release.json.sig`, Ed25519) και η εφαρμογή δέχεται μόνο ό,τι
επαληθεύεται με το pinned δημόσιο κλειδί που έχει μέσα της.

## 1. Πού ανεβαίνει ο κώδικας

Repo: `StefAa1/MUXIVRA` (remote `origin = git@github.com:StefAa1/MUXIVRA.git`).

Ο server δεν έχει πρόσβαση push ακόμη. Ένα από τα δύο:

**A. Deploy key (προτεινόμενο, μία φορά)**
1. GitHub → repo `StefAa1/MUXIVRA` → **Settings → Deploy keys → Add deploy key**
2. Title: `muxivra-build-server`
3. Key (ολόκληρη η γραμμή):
   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK0icfey0Ra5jUmHcnLVYVoE1/j1LNoGdo3v+OtgPt01 hermes@muxivra
   ```
4. **Να τσεκάρεις «Allow write access»** (χωρίς αυτό γίνεται μόνο fetch)
5. Save. Μετά ο server κάνει `git push` και `git push --tags`.

**B. Collaborator + δικό σου κλειδί** — αν προτιμάς να μην υπάρχει deploy key,
πρόσθεσε τον λογαριασμό/κλειδί σου ως collaborator και πες μου να αλλάξω το remote.

## 2. Τι κάνει ο server μετά

```bash
cd /root/sshdeck
git push -u origin main
git tag -a v0.4.9 -m "0.4.9"
git push origin v0.4.9
```

Το push του tag ενεργοποιεί το `.github/workflows/release.yml`:

- τρέχει τα tests,
- χτίζει το portable bundle,
- υπογράφει το manifest με το secret `MUXIVRA_SIGNING_KEY_B64`,
- δημιουργεί GitHub Release με assets:
  `MUXIVRA-portable-win64.zip`, `release.json`, `release.json.sig`, `release.pubkey.txt`.

**Απαραίτητο secret (μία φορά):** repo → Settings → Secrets and variables → Actions →
New repository secret, όνομα `MUXIVRA_SIGNING_KEY_B64`, τιμή το περιεχόμενο του
`/root/sshdeck-secrets/signing-key.b64.txt` (160 χαρακτήρες). Χωρίς αυτό το release
σκάει σκόπιμα: «a release without a signature is refused by every client».

Αν δεν θέλεις να δώσεις το κλειδί στο GitHub, εναλλακτικά ανεβάζεις εσύ χειροκίνητα
τα 4 assets σε ένα Release (ίδια ονόματα αρχείων) — η εφαρμογή ελέγχει μόνο την
υπογραφή, όχι ποιος τα ανέβασε.

## 3. Πώς κάνει update η εφαρμογή

Στο MUXIVRA: **Settings → Updates**

1. Repo: `StefAa1/MUXIVRA` → **Save**
2. **Check** → δείχνει την τελευταία έκδοση (διαβάζει το `release.json` του release)
3. **Prepare/Install** → κατεβάζει το zip, επαληθεύει sha256 + Ed25519,
   κάνει backup της βάσης με SQLite backup API, φτιάχνει staging **έξω** από τον
   φάκελο της εγκατάστασης, κλείνει μόνο το δικό μας PID και εφαρμόζει.
   Αν το restore ή το startup healthcheck αποτύχει → rollback, όχι «επιτυχία».

Η εφαρμογή δέχεται μόνο `https://` hosts από allowlist και μόνο υπογεγραμμένο
manifest· αλλιώς επιστρέφει `bad_signature` / `insecure_url` / `untrusted_host`.

## 4. Τοπική διανομή (χωρίς GitHub)

Το τρέχον build είναι διαθέσιμο και τοπικά:
`http://10.10.1.16:8080/21YeHQBmys4N/MUXIVRA-portable-win64.zip`
μαζί με `release.json` και `release.json.sig` — ίδια αρχεία με του GitHub Release.
