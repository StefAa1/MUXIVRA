# Changelog

All notable changes to MUXIVRA. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow `MAJOR.MINOR.PATCH` and match `sshdeck/__init__.py` plus the git tag (`v0.4.0`).

Release status: **0.4.0 is ready but untagged** — `sshdeck/__init__.py` still reports `0.3.1`.
Bump it, commit, then tag (see [docs/RELEASE.md](docs/RELEASE.md)); the release workflow refuses a
tag that does not match the package version.

## [0.4.0] - 2026-11-18

### Added

- **Signed updates.** A release is only applicable when `release.json` carries a valid Ed25519
  signature (`release.json.sig`) from the pinned public key — `data/update-pubkey.txt` or
  `SSHDECK_UPDATE_PUBKEY`. With no key configured, every update is refused (`no_pubkey`).
- **Release tooling and CI.** `tools/sign_release.py` produces the manifest + signature from the
  portable zip (pre-flighting the updater's own size/entry/content limits);
  `.github/workflows/release.yml` builds, tests, signs and publishes on a `v*` tag;
  `docs/RELEASE.md` is the runbook.
- **Safe self-update.** Staging happens outside the bundle, `apply-update.cmd` waits for the
  current PID (no more `taskkill` of a second instance), keeps a timestamped backup and rolls back
  if the copy fails.
- **Per-user data directory.** Database, vault key and known hosts move to
  `%LOCALAPPDATA%\MUXIVRA\data` (XDG on Linux), so replacing the program folder never touches user
  data; an existing `<bundle>/data` is migrated once, with a file-by-file fallback across devices.
- **Host key approval.** An unknown or changed host key is surfaced with its fingerprint and can be
  trusted or forgotten explicitly (`GET/POST/DELETE /hosts/{id}/trust`).
- **Vault lock.** The vault can be locked and unlocked without restarting (`/vault`, `/vault/lock`,
  `/vault/unlock`), with an optional idle auto-lock (`SSHDECK_AUTO_LOCK_SECONDS`).
- **Rack documentation.** Device editing with rack/slot validation, port search, link editing,
  paged link lists, device dependents, device⇄host linking and a rack/cable diagram.
- **Signing-key status.** `GET /update/state` reports whether a signing key is configured and its
  short fingerprint; `/update/prepare` refuses to stage an update it cannot verify (`502 no_pubkey`
  / `bad_signature`) and logs `update_rejected`.

### Security

- DNS-rebinding and cross-origin guard: `Host` and `Origin` are checked against an allow-list
  (loopback plus `SSHDECK_ALLOWED_HOSTS`); a foreign `Host` gets `403 host_not_allowed`.
- Update downloads are restricted to `https` and GitHub hosts, with asset/manifest size caps,
  and archives are rejected for absolute paths, traversal, drive letters, symlinks, too many
  entries or excessive expansion.

### Changed

- `one link per port` is enforced on create **and** edit (`409 port_already_linked`), so cabling
  cannot be double-booked by a later change.
- Version strings tolerate a `v` prefix; release compare pads missing components.

## [0.3.1] - 2026-11-18

### Fixed

- A zero-byte `vault.key` (interrupted first run, restored backup) is re-created instead of
  failing every credential operation.
- A clearer error when the vault key exists but cannot be read, instead of a generic 500.

## [0.3.0] - 2026-11-18

### Added

- **Racks & cables** module: rooms → racks → devices (U position) → ports (bulk 24) → links with
  cable type/length, and `links.csv` export. NETDOC parity: visual rack, quick connect, audit log.
- **GitHub updater**: check the latest release, download the `.zip` and verify `sha256`.
- **DPAPI key wrapping** on Windows: the vault key is wrapped per user, so a copy of `data/` on
  another machine cannot decrypt stored credentials.
- MUXIVRA branding across the UI and the portable bundle.

### Fixed

- Creating a credential on Windows returned HTTP 500 because the DPAPI key wrap failed.

## [0.2.0] - 2026-11-18

### Added

- Initial MUXIVRA release: inventory with groups/tags/notes, multi-tab SSH terminal, Telnet
  (RFC 854 with IAC/NAWS), serial/COM sessions, encrypted vault (Fernet) with per-install key,
  SFTP browser, snippets, `~/.ssh/config` and Keeper Secrets Manager import, TOFU host keys,
  and the portable Windows bundle with its launcher.

[0.4.0]: https://github.com/StefAa1/MUXIVRA/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/StefAa1/MUXIVRA/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/StefAa1/MUXIVRA/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/StefAa1/MUXIVRA/releases/tag/v0.2.0
