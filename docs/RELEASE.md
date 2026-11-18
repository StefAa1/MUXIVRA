# Release runbook — MUXIVRA portable (Windows x64)

Πώς φτιάχνεται, υπογράφεται και δημοσιεύεται ένα release, και πώς γίνεται rollback.
Ο updater **αρνείται** κάθε release χωρίς έγκυρη υπογραφή — γι' αυτό η υπογραφή είναι μέρος
της διαδικασίας, όχι προαιρετικό βήμα.

| Τι | Πού |
| --- | --- |
| Signing tool | `tools/sign_release.py` |
| CI | `.github/workflows/release.yml` (trigger: tag `v*`) |
| Build | `scripts/build_windows_portable.py` → `dist/MUXIVRA-portable-win64.zip` |
| Έλεγχος bundle | `scripts/verify_bundle.py` |
| Client verifier | `sshdeck/update.py` (`verify_manifest`, `fetch_manifest`, `download_verified`) |

## 0. Τι περιμένει ο updater (συμβόλαιο)

Assets στο GitHub release:

| Asset | Περιεχόμενο |
| --- | --- |
| `MUXIVRA-portable-win64.zip` | το portable bundle |
| `release.json` | `version` (χωρίς `v`), `archive`, `sha256` (hex), `built_at` (ISO8601) |
| `release.json.sig` | base64 Ed25519 υπογραφή πάνω στα **ακριβή bytes** του `release.json` |
| `release.pubkey.txt` | base64 raw 32-byte δημόσιο κλειδί (προαιρετικό asset, για χειροκίνητο έλεγχο) |

Το manifest μπορεί να έχει και προαιρετικό `repo` (`owner/name`) — ο client αγνοεί άγνωστα πεδία.

Ο client ελέγχει με σειρά: υπογραφή manifest → `version` == tag → όνομα archive == asset name →
`sha256` → περιεχόμενα zip (traversal/symlink/όρια). Οτιδήποτε αποκλίνει, το update απορρίπτεται
και **δεν** αγγίζει τα εγκατεστημένα αρχεία.

## 1. Μία φορά: κλειδί υπογραφής

Το κλειδί μένει **έξω από το repo** (ο tool αρνείται PEM μέσα στο working tree) και δεν μπαίνει ποτέ
σε chat, issue, log ή commit.

```bash
mkdir -p ~/keys && chmod 700 ~/keys
python - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
k = Ed25519PrivateKey.generate()
open("/root/keys/muxivra-release.pem", "wb").write(
    k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
PY
chmod 600 ~/keys/muxivra-release.pem
python tools/sign_release.py --key ~/keys/muxivra-release.pem --print-pubkey   # base64 pubkey
```

- **Backup**: αντίγραφο του `.pem` σε offline μέσο (USB/password manager). Αν χαθεί το κλειδί,
  δεν μπορείς να υπογράψεις update για τις υπάρχουσες εγκαταστάσεις — μόνο rotation (§7).
- **GitHub secret**: μόνο το base64 του κλειδιού, ποτέ αρχείο στο repo.

```bash
base64 -w0 ~/keys/muxivra-release.pem | gh secret set MUXIVRA_SIGNING_KEY_B64 --repo StefAa1/MUXIVRA
gh secret list --repo StefAa1/MUXIVRA      # επιβεβαίωση ύπαρξης (η τιμή δεν διαβάζεται πίσω)
```

Το secret διαβάζεται μόνο στο βήμα "Sign release manifest" ως env var· ο tool το κρατά στη μνήμη
και δεν γράφει ποτέ ιδιωτικό κλειδί σε δίσκο. Στο τέλος το workflow σαρώνει τα **tracked** αρχεία
για μπλοκ `-----BEGIN … PRIVATE KEY-----` με σώμα base64 και αποτυγχάνει αν βρει κάτι.

> Γνωστό fixture: το `testdata/deploy_key` είναι συνθετικό OpenSSH κλειδί (δημόσιο στο repo από
> το 0.2.0) για τα `scripts/seed_demo.py`, `live_check.py`, `wine_e2e.py`. Είναι σε allowlist του
> ελέγχου με warning — **μην** το χρησιμοποιήσεις σε πραγματικό host.

## 2. Δημόσιο κλειδί στον client (μία φορά ανά εγκατάσταση)

Ο client διαβάζει `update-pubkey.txt` από τον data dir ή το env `SSHDECK_UPDATE_PUBKEY`:

```powershell
# Windows, default data dir
notepad "%LOCALAPPDATA%\MUXIVRA\data\update-pubkey.txt"   # μία γραμμή: base64 pubkey
```

```bash
# Linux / δοκιμές
echo "<base64 pubkey>" > "${XDG_DATA_HOME:-$HOME/.local/share}/muxivra/data/update-pubkey.txt"
# ή
export SSHDECK_UPDATE_PUBKEY="<base64 pubkey>"
```

Έλεγχος ότι ο client μπορεί να επαληθεύσει: Ρυθμίσεις → *Update* — το `/update/state` δείχνει
`signing_key.configured: true` και ένα σύντομο fingerprint (sha256 του κλειδιού, 16 hex).
Αν δείχνει `{"configured": false, "reason": "no_pubkey"}`, **κάθε** update θα απορρίπτεται —
αυτό είναι σκόπιμο (fail closed).

## 3. Release (κανονική ροή, CI)

1. **Bump έκδοσης** στο `sshdeck/__init__.py` (`__version__ = "0.4.0"`). Χωρίς αυτό το workflow
   σταματά στο βήμα "Verify tag matches package version" — ο updater συγκρίνει tag και manifest,
   και ο client χρησιμοποιεί το `__version__` ως "τρέχουσα", άρα mismatch = επ' άπειρον prompts.
2. **CHANGELOG.md**: νέα ενότητα `## [x.y.z] - YYYY-MM-DD`. Το workflow κόβει από εκεί τις
   release notes (fallback: σύντομο κείμενο + `CHANGELOG.md`).
3. Commit, μετά tag και push (χειροκίνητο — απαιτεί δικό σου push access):

```bash
git add sshdeck/__init__.py CHANGELOG.md
git commit -m "Release x.y.z"
git tag -a vx.y.z -m "MUXIVRA x.y.z"
git push origin main && git push origin vx.y.z
```

4. Το workflow (`build` → `publish`) τρέχει αυτόματα:

| Βήμα | Τι κάνει |
| --- | --- |
| Tests | `.venv/bin/pytest` — ένα κόκκινο test μπλοκάρει το release |
| Version guard | `GITHUB_REF_NAME` == `sshdeck.__version__` |
| Build | `scripts/build_windows_portable.py` (CPython 3.12 embeddable, win_amd64 wheels, mingw launcher) |
| Verify bundle | `scripts/verify_bundle.py` (bundle == source, hygiene) |
| Sign | `tools/sign_release.py` με `MUXIVRA_SIGNING_KEY_B64`, `built_at` = commit timestamp |
| Verify (client path) | `sshdeck.update.verify_manifest` πάνω στα γραμμένα αρχεία |
| Leak check | `grep` για private key material |
| Independent verify | άλλο job/runner: `cryptography` μόνο, signature + sha256 + version vs tag |
| Publish | ανεβάζει 4 assets· αν το release υπάρχει, `gh release upload --clobber` |
| Verify published | ελέγχει ότι τα 4 assets είναι όντως στο release |

5. Επιβεβαίωση release:

```bash
gh release view vx.y.z --repo StefAa1/MUXIVRA --json assets --jq '.assets[].name'
# MUXIVRA-portable-win64.zip  release.json  release.json.sig  release.pubkey.txt
```

Αν το job αποτύχει μετά το publish (π.χ. στο τελευταίο βήμα), ξανατρέξε το από το Actions UI
("Re-run failed jobs") — δεν χρειάζεται νέο tag. Το workflow δεν έχει `workflow_dispatch`:
τρέχει μόνο από tag ώστε να μην υπάρχει τρόπος να δημοσιευτεί release από κλαδί.

## 4. Release χωρίς CI (offline / δοκιμή)

Ίδια βήματα, με το ίδιο tool, τοπικά:

```bash
.venv/bin/pytest -o addopts="" -q
.venv/bin/python scripts/build_windows_portable.py
.venv/bin/python scripts/verify_bundle.py
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct) \
.venv/bin/python tools/sign_release.py \
    --zip dist/MUXIVRA-portable-win64.zip \
    --version 0.4.0 --out dist --repo StefAa1/MUXIVRA
# έλεγχος πριν ανέβει οτιδήποτε
.venv/bin/python tools/sign_release.py --key ~/keys/muxivra-release.pem --print-pubkey
```

Έξοδος του tool: `version`, `sha256`, μέγεθος/entries του zip, διαδρομές `release.json` /
`release.json.sig`, το base64 δημόσιο κλειδί, και `self-check: signature verified against the
written files`. Exit codes: `0` ok, `1` αποτυχία υπογραφής/self-check, `2` λάθος input
(λάθος κλειδί, λάθος version, zip που ο updater θα απέρριπτε).

Το tool αρνείται εκ των προτέρων zip που παραβιάζει τα όρια του updater:
> 400 MiB asset, 800 MiB uncompressed, 20 000 entries, χωρίς `MUXIVRA.exe`/`MUXIVRA.cmd`.
> Έτσι δεν δημοσιεύεται release που κάθε client θα απέρριπτε μετά τη λήψη.

## 5. Backup πριν το update (client)

Το update **δεν** αγγίζει τον data dir (`%LOCALAPPDATA%\MUXIVRA\data`) — μόνο τον φάκελο του
προγράμματος. Πριν πατήσεις "Κατέβασε & ετοίμασε":

```powershell
# κρατά database + vault key + known hosts
robocopy "%LOCALAPPDATA%\MUXIVRA\data" "%USERPROFILE%\Desktop\muxivra-data-backup" /E
```

Κατά την εφαρμογή, το `apply-update.cmd` μετακινεί τον παλιό φάκελο σε
`%LOCALAPPDATA%\MUXIVRA\updates\backup-YYYYMMDD-HHMMSS` και μόνο τότε αντιγράφει τον νέο· αν το
copy αποτύχει, επαναφέρει το backup αυτόματα.

Χειροκίνητο rollback σε αυτό το μηχάνημα:

1. Κλείσε το MUXIVRA (το script περιμένει το PID, μην το σκοτώσεις).
2. Μετονόμασε/σβήσε τον τρέχοντα φάκελο εγκατάστασης.
3. Μετονόμασε το `backup-…` πίσω στη θέση του (ίδιο όνομα φακέλου).
4. Άνοιξε `MUXIVRA.exe` — το `data` μένει ανέγγιχτο, άρα και τα credentials.

## 6. Rollback ενός δημοσιευμένου release

Ο updater εφαρμόζει μόνο **μεγαλύτερη** έκδοση (`is_newer`). Άρα:

- **Λάθος build σε πρόσφατο release**: μην ξαναχρησιμοποιήσεις το ίδιο version number. Κόψε
  `x.y.z+1` με το προηγούμενο καλό zip (ξανά signed) — αυτό φτάνει σε όλους, και όσοι πρόλαβαν
  το χαλασμένο παίρνουν διόρθωση.
- **Διαγραφή release**: `gh release delete vx.y.z --repo StefAa1/MUXIVRA --yes`. Δεν βοηθά όσους
  το κατέβασαν ήδη· ο updater θα δει το προηγούμενο `latest` και δεν θα προσφέρει downgrade.
  Κράτα το tag ή σβήσε και τα δύο, αλλά πάντα ενημέρωσε το CHANGELOG.
- **Κακό zip, σωστό manifest**: αδύνατο — το `sha256` είναι μέσα στο υπογεγραμμένο manifest, άρα
  αλλαγή zip = `bad_digest` και απόρριψη.

## 7. Rotation / απώλεια κλειδιού

Ο client εμπιστεύεται **ένα** pinned κλειδί· δεν υπάρχει αυτόματη ανταλλαγή. Άρα:

1. Νέο ζεύγος (§1) → `gh secret set MUXIVRA_SIGNING_KEY_B64` με το νέο κλειδί.
2. Κόψε release με το νέο κλειδί και δώσε το νέο base64 pubkey μαζί με το release notes.
3. Κάθε χρήστης αντικαθιστά το `update-pubkey.txt` (ή το `SSHDECK_UPDATE_PUBKEY`) **χειροκίνητα**
   πριν το επόμενο update. Μέχρι τότε ο client απορρίπτει (`bad_signature`) — σωστό, fail closed.
4. Αν το παλιό κλειδί διέρρευσε: αφαίρεσε το secret, θεώρησε κάθε release με την παλιά υπογραφή
   μη έμπιστο, και ενημέρωσε τους χρήστες ότι η αλλαγή είναι υποχρεωτική.

## 8. Γιατί ένα unsigned release απορρίπτεται

Χωρίς υπογραφή, ο client δεν μπορεί να ξεχωρίσει ένα release του maintainer από ένα asset που
ανέβασε ή αντικατέστησε κάποιος άλλος (κλεμμένο token, compromised account, MITM σε mirror).
Το `sha256` μόνο δεν αρκεί: ταξιδεύει στο ίδιο μη έμπιστο κανάλι με το zip. Η Ed25519 υπογραφή
πάνω στο manifest δένει *version + όνομα + hash* με το κλειδί που είναι ήδη pinned στη συσκευή,
και ο updater προτιμά να μη κάνει update καθόλου παρά να τρέξει άγνωστο binary.

## 9. Troubleshooting

| Μήνυμα client | Αιτία | Λύση |
| --- | --- | --- |
| `no_pubkey` | λείπει `update-pubkey.txt` / env | §2 |
| `bad_pubkey` | δεν είναι 32-byte base64/hex | ξαναγράψε το από `--print-pubkey` |
| `bad_signature` | λάθος κλειδί, ή πειραγμένο `release.json` | έλεγξε secret + ότι ανέβηκαν και τα 3 assets |
| `unsigned_release` | λείπουν `release.json`/`.sig` από το release | ξανατρέξε το publish job |
| `version_mismatch` | manifest version ≠ tag | σωστό `--version` / νέο tag |
| `archive_mismatch` | άλλο όνομα zip ως asset | ανέβασε το zip με το ακριβές όνομα |
| `bad_digest` | zip άλλαξε μετά την υπογραφή | ξανά sign το ίδιο zip, χωρίς rebuild |
| `too_large` / `too_many_entries` | όρια updater | §4 (το tool τα πιάνει πριν το publish) |
| `host_not_allowed` (άνευ update) | `Host` header εκτός allow-list | `SSHDECK_ALLOWED_HOSTS` |

Τοπικός έλεγχος ενός release πριν το ανεβάσεις:

```bash
python - <<'PY'
import base64, hashlib, json, pathlib
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
raw = pathlib.Path("dist/release.json").read_bytes()
sig = base64.b64decode(pathlib.Path("dist/release.json.sig").read_text())
pub = base64.b64decode(pathlib.Path("dist/release.pubkey.txt").read_text())
Ed25519PublicKey.from_public_bytes(pub).verify(sig, raw)
m = json.loads(raw)
assert hashlib.sha256(pathlib.Path("dist", m["archive"]).read_bytes()).hexdigest() == m["sha256"]
print("OK", m["version"], m["sha256"])
PY
```
