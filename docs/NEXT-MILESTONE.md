# MUXIVRA — εργασίες σε εξέλιξη (long-task notes)

Στόχος: ενιαίο επόμενο build που περιλαμβάνει
1. customer-scoped SSH trust
2. guided «Προσθήκη συσκευής» + νέα πλοήγηση
3. SSH/Telnet/COM + web management από την ίδια συσκευή
4. διάγραμμα δίπλα σε terminal/web/config (split view)
5. σχεδιασμός (planned) και προβολή (observed) δικτύου
6. ανάκτηση configuration, εκδόσεις/diff, συγχρονισμός λογικών στοιχείων με το διάγραμμα

## Καταστάσεις
- [ ] trust: per-customer known_hosts + migration
- [ ] schema v8: config_versions, devices.origin, device_observed
- [ ] config module + adapters (mikrotik, cisco) + diff + apply guard
- [ ] web management surface (Qt6 WebEngine window, ξεχωριστό profile ανά πελάτη)
- [ ] UI: wizard 3 βημάτων
- [ ] UI: nav Συσκευές · Καλωδίωση · Διάγραμμα · Τερματικά
- [ ] UI: split diagram | terminal/web/config
- [ ] UI: diagram design mode (planned devices/links)
- [ ] config panel: fetch/versions/diff/apply + sync
- [ ] tests (Linux) + fixtures για adapters
- [ ] build/sign/publish + πίνακας

## Σταθερές
- Δεδομένα χρήστη: /root/sshdeck/data (μην πειράζεται).
- Test fixtures σε scratch, όχι στην ενεργή βάση.
- Καμία εγγραφή σε πραγματικό εξοπλισμό χωρίς ρητό apply + επιβεβαίωση.
- Ό,τι χρειάζεται πραγματική συσκευή δηλώνεται χωριστά ως ανεπιβεβαίωτο.
