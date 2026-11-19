# MUXIVRA 0.4.6 — τι παραδόθηκε, τι μένει

Build: `0.4.6+2e8854f` · SHA-256 `7da5e2b98d3c87bf100078d625077afa1d1da7b95229ca32e15e3dafce43da33`
(υπογεγραμμένο `release.json` / `release.json.sig`, self-check OK)

## Ολοκληρώθηκαν

| Περιοχή | Υλοποίηση | Πού δοκιμάστηκε |
|---|---|---|
| Customer-scoped SSH trust | `known_hosts.d/customer-<id>`, `Target.customer_id`, migration του παλιού αρχείου στον «Μη ταξινομημένο» | Linux pytest |
| Configuration workspace | `sshdeck/workspace.py`: adapters MikroTik/Cisco, versions (κρυπτογραφημένες), diff με redaction, apply με stale-hash guard + confirmation, reconcile | pytest (fixtures) + Wine HTTP |
| Isolated web management | `sshdeck/webview_surface.py`: ξεχωριστό process, per-customer profile, χωρίς bridge/app cookies, per-destination cert trust, έντιμο fallback σε system browser | pytest + Wine (config endpoint) |
| Planned topology | `devices.origin`, `planned_links`, ξεχωριστά από φυσικά καλώδια | pytest + Wine |
| Διάγραμμα design/observed | dataset switch, διακεκομμένα planned nodes/edges, legend, layout key ανά customer+dataset | DOM (Chromium) |
| Guided προσθήκη συσκευής | `web/wizard.js`: 3 βήματα, inline χώρος/rack/credential, έλεγχος σύγκρουσης U, rollback host σε αποτυχία τοποθέτησης | DOM (Chromium) |
| Νέα πλοήγηση | Συσκευές · Καλωδίωση · Διάγραμμα · Τερματικά + Βοηθητικά, μόνιμη γραμμή context | DOM (Chromium) |
| Διάγραμμα δίπλα στο terminal | split pane με draggable divider, persisted width/state | DOM (Chromium) |
| Ενέργειες συσκευής | «Web management» + «Configuration» στο modal συσκευής, scoped | DOM (Chromium) |

## Χρειάζεται πραγματική συσκευή (δεν δηλώνεται επαληθευμένο)

- Ανάκτηση configuration μέσω SSH σε MikroTik/Cisco (ο adapter διαβάζει πραγματικό CLI output).
- Apply εντολών στη συσκευή: το backend το υποστηρίζει με stale guard, αλλά δεν εκτίθεται από το UI.
- WinBox launch και το παράθυρο web management (Qt WebEngine) σε πραγματικά Windows.
- SSH host-key approval/forget σε πραγματικό sshd με per-customer keys.

## Ακόμη ανοιχτά

- `origin` δεν εμφανίζεται ακόμη στη λίστα συσκευών (μόνο στο διάγραμμα και στο API).
- Observation chip πάνω στους κόμβους του διαγράμματος (υπάρχει μόνο στο legend).
- GitHub push/tag/release: λείπει deploy key· όλα τα commits είναι τοπικά.
