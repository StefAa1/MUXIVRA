# MUXIVRA 0.4.8 — hardware catalog, English UI, diagram icon fixes

## Hardware catalog
- `sshdeck/catalog.py`: 4 vendors (Cisco, MikroTik, Aruba, Ubiquiti), 22 models with the
  real port layouts and vendor naming (`Gi0/1`, `ether1`, `1`, `Port 1`).
- `POST /api/netdoc/devices` accepts `model_key` and creates the model's ports;
  `GET /api/netdoc/catalog` and `/catalog/{vendor}` expose the data.
- Guided wizard step 1: vendor → model, with kind/units applied and a port preview
  (“switch · 1U · 24× RJ45 + 4× SFP”).

## Diagram
- Kind icon moved into a badge inside the node; arrow glyphs no longer cross the shape border.
- New kinds: `firewall`, `ap` (used by the catalog models).

## Language
- Whole UI and operator-facing messages in English.

## Verification
- `pytest` (Linux): see release run.
- `node --check` on all six web assets.
- DOM checks: wizard catalog selects, diagram badges, racks layout.
