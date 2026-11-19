# Phase B — status (MUXIVRA 0.4.4)

## Delivered and verified

- **Customers data model** (schema v7): `customers` (uuid, name, code, color, notes, archived),
  `sites.customer_id`, `hosts.customer_id`, `credentials.customer_id`. Racks/devices/ports/links
  inherit ownership through site → rack → device.
- **Migration**: every pre-existing site/host/credential is adopted by
  «Μη ταξινομημένος πελάτης» (`customers.backfill_ownership`, runs inside `db.migrate()`); ids,
  UUIDs, vault secrets and links are untouched.
- **Request-scoped scope**: `x-customer-id` header (or `?customer=`), resolved per request in
  `customers.request_customer`; there is no process-wide mutable selection.
- **Scoped reads/writes**: hosts, credentials, sites tree, rack/device create+update+delete,
  device⇄profile link, cable create, export/import. Cross-customer cable → `422 cross_customer`,
  cross-customer device/profile → `409/422`, cross-customer rack inside a site → rejected.
- **Same name/IP allowed**: two customers with a «Router» host on 192.168.1.1 stay separate in
  lists, tree, export and profile mapping (tests/test_customers.py).
- **UI**: permanent customer selector + «+ Πελάτης» in the sidebar, `api()` sends the header,
  switching reloads hosts/credentials/snippets/racks with the new scope.

## Pending (next milestone, in this order)

1. Guided «Προσθήκη συσκευής» (3 steps: Συσκευή → Πρόσβαση → Τοποθέτηση) with draft preservation,
   inline validation and a single transaction across device/profile/placement.
2. Navigation restructure: top bar with customer/installation/search/primary action, sections
   Συσκευές · Καλωδίωση · Διάγραμμα · Τερματικά, with vault/snippets/log/imports under a helper nav.
3. Device page with tabs Γενικά / Πρόσβαση / Θύρες / Συνδέσεις, primary-profile connect button and
   additional-profile dropdown, vault-locked → unlock → continue pending target.
4. «Νέο καλώδιο» as an explicit flow (A → B → summary → save); occupied port click opens peer/cable
   actions instead of a disabled cell.
5. Graph scoped per customer with installation filter and per-customer layout keys
   (`muxivra.diagram.positions.<customer-uuid>`).
6. Terminal tab identity (customer/site/device in the tab) and «customer changed while tab open»
   marking.
7. Typography/flow pass (14–16px, 40px fields, 44px primary actions) verified at 1366×768,
   1920×1080, 2560×1440 and 125% scaling.
8. Demo dataset as an explicit, separate seed script (never mixed with real data).

## Interfaces that must not change

- `POST /api/customers`, `GET /api/customers`, `PATCH /api/customers/{id}`,
  `GET /api/customers/{id}/summary`.
- Ownership checks live in `sshdeck/customers.py` (`assert_same_customer`, `*_owner`); new endpoints
  must call them rather than trusting the UI.
- Documentation export format `muxivra.inventory/1` is per-customer and never contains credentials.

## Known limits

- Single local operator: this is logical separation, **not** multi-user isolation (no RBAC).
- Application backup (whole data dir) and per-customer export are different things and stay separate.
