#!/usr/bin/env python3
"""Sign a MUXIVRA portable release before it is published on GitHub.

Produces the two assets the updater requires next to the zip:

    release.json       version / archive / sha256 / built_at
    release.json.sig   base64 Ed25519 signature over the EXACT bytes of release.json

Usage (local):

    python tools/sign_release.py \
        --zip dist/MUXIVRA-portable-win64.zip \
        --version 0.4.0 \
        --key ~/keys/muxivra-release.pem

Usage (CI, key from a secret — never touches disk):

    MUXIVRA_SIGNING_KEY_B64="$SECRET" python tools/sign_release.py \
        --zip dist/MUXIVRA-portable-win64.zip --version "${TAG#v}"

The private key is only ever held in memory. Nothing in this tool prints it, and
no key material is written anywhere. The public key is printed so it can be
pinned on clients (data/update-pubkey.txt or SSHDECK_UPDATE_PUBKEY).

Accepted key formats:
  * unencrypted PKCS#8 / OpenSSH Ed25519 private key (PEM), via --key or env
  * base64 (or hex) of the 32-byte Ed25519 seed          -> env MUXIVRA_SIGNING_KEY_B64
  * base64 (or hex) of the 64-byte libsodium secret key  -> seed||pubkey, cross-checked

Exit codes: 0 success, 1 signing/self-check failure, 2 invalid input.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_NAME = "release.json"
SIGNATURE_NAME = "release.json.sig"

# Client-side limits (sshdeck/update.py). A release that violates them would be
# downloaded and then refused by every installed client, so we fail at build time.
try:  # single source of truth when running from the repository
    sys.path.insert(0, str(ROOT))
    from sshdeck.update import (  # type: ignore
        MAX_ASSET_BYTES,
        MAX_ENTRIES,
        MAX_EXTRACTED_BYTES,
    )
except Exception:  # pragma: no cover - standalone copy of the tool
    MAX_ASSET_BYTES = 400 * 1024 * 1024
    MAX_EXTRACTED_BYTES = 800 * 1024 * 1024
    MAX_ENTRIES = 20000

VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")
BUNDLE_MARKERS = ("MUXIVRA.exe", "MUXIVRA.cmd")
INNER_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


class SignError(Exception):
    """Expected, user-facing failure — printed without a traceback."""


# --------------------------------------------------------------------- output
def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def info(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print(f"::warning::{message}" if in_actions() else f"warning: {message}", file=sys.stderr, flush=True)


def fail(message: str, code: int = 2) -> "None":
    text = f"::error::{message}" if in_actions() else f"error: {message}"
    print(text, file=sys.stderr, flush=True)
    raise SystemExit(code)


def set_output(**values: str) -> None:
    """Expose values to later GitHub Actions steps when running in CI."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:
        warn(f"could not write GITHUB_OUTPUT: {exc}")


# ----------------------------------------------------------------------- keys
def _looks_like_key_material(text: str) -> bool:
    return "BEGIN" in text or bool(re.fullmatch(r"[A-Za-z0-9+/=\s\-_]+", text.strip()))


def _decode_b64_or_hex(raw: str) -> bytes:
    text = "".join(raw.split())
    if re.fullmatch(r"[0-9a-fA-F]{64}|[0-9a-fA-F]{128}", text):
        return bytes.fromhex(text)
    text = text.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignError("το κλειδί δεν είναι έγκυρο base64 ή hex") from exc


def _check_key_file(path: Path) -> None:
    """Refuse a key inside the working tree, warn on loose permissions."""
    try:
        resolved = path.resolve()
        repo = Path(
            os.environ.get("GIT_WORK_TREE") or ROOT
        ).resolve()
        if resolved.is_relative_to(repo) and (repo / ".git").exists():
            if os.environ.get("ALLOW_KEY_IN_REPO") == "1":
                warn(f"το ιδιωτικό κλειδί βρίσκεται μέσα στο repo: {resolved}")
            else:
                raise SignError(
                    "το ιδιωτικό κλειδί βρίσκεται μέσα στο repository "
                    f"({resolved}). Μετακίνησέ το έξω από το repo "
                    "(π.χ. ~/keys/muxivra-release.pem) ή όρισε ALLOW_KEY_IN_REPO=1."
                )
        if os.name == "posix":
            mode = resolved.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                warn(f"το αρχείο κλειδιού είναι προσβάσιμο σε group/others ({oct(mode & 0o777)}); chmod 600 συνιστάται")
    except OSError as exc:
        raise SignError(f"δεν διαβάζεται το αρχείο κλειδιού: {exc}") from exc


def load_key_material(key_arg: str | None) -> bytes:
    """Return the raw key text from --key (path, '-' or inline) or the env var."""
    if key_arg:
        if key_arg == "-":
            text = sys.stdin.read()
            if not text.strip():
                raise SignError("δεν δόθηκε κλειδί στο stdin")
            return text.encode("utf-8")
        candidate = Path(key_arg).expanduser()
        if candidate.is_dir():
            raise SignError(f"το --key δείχνει σε φάκελο, όχι αρχείο: {candidate}")
        if candidate.is_file():
            _check_key_file(candidate)
            try:
                return candidate.read_bytes()
            except OSError as exc:
                raise SignError(f"δεν διαβάζεται το αρχείο κλειδιού: {exc}") from exc
        if _looks_like_key_material(key_arg):
            return key_arg.encode("utf-8")
        raise SignError(f"το αρχείο κλειδιού δεν βρέθηκε: {key_arg}")

    env_value = os.environ.get("MUXIVRA_SIGNING_KEY_B64", "").strip()
    if not env_value:
        raise SignError(
            "δεν δόθηκε κλειδί: χρησιμοποίησε --key <pem> ή όρισε MUXIVRA_SIGNING_KEY_B64"
        )
    return env_value.encode("utf-8")


def load_private_key(material: bytes):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as exc:  # pragma: no cover
        raise SignError("λείπει η βιβλιοθήκη 'cryptography' (pip install cryptography)") from exc

    text = material.decode("utf-8", errors="replace").strip()
    if not text:
        raise SignError("το κλειδί είναι κενό")

    if "BEGIN" in text:
        try:
            key = load_pem_private_key(text.encode("utf-8"), password=None)
        except TypeError as exc:
            raise SignError("το κλειδί είναι κρυπτογραφημένο με passphrase — χρησιμοποίησε unencrypted PKCS#8") from exc
        except ValueError as exc:
            raise SignError(f"μη αναγνώσιμο PEM ιδιωτικό κλειδί: {exc}") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise SignError(f"το κλειδί δεν είναι Ed25519 (είναι {type(key).__name__})")
        return key

    raw = _decode_b64_or_hex(text)
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    if len(raw) == 64:  # libsodium secret key = seed || public key
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        seed, pub = raw[:32], raw[32:]
        key = Ed25519PrivateKey.from_private_bytes(seed)
        derived = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if derived != pub:
            raise SignError("το 64-byte κλειδί δεν είναι έγκυρο (το δημόσιο μέρος δεν ταιριάζει)")
        return key
    raise SignError(f"μη αναγνώσιμο κλειδί: {len(raw)} bytes (αναμένονται 32 ή 64, ή PEM)")


def public_b64(private_key) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    return base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")


# --------------------------------------------------------------------- checks
def normalise_version(text: str) -> str:
    version = (text or "").strip().lstrip("vV")
    if not VERSION_RE.match(version):
        raise SignError(f"μη έγκυρη έκδοση «{text}» — αναμένεται π.χ. 0.4.0 ή v0.4.0 (αριθμοί και τελείες)")
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_archive(archive: Path) -> dict:
    """Preflight the zip against the updater's acceptance rules."""
    try:
        size = archive.stat().st_size
    except OSError as exc:
        raise SignError(f"δεν διαβάζεται το zip: {exc}") from exc
    if size == 0:
        raise SignError("το zip είναι κενό")
    if size > MAX_ASSET_BYTES:
        raise SignError(
            f"το zip είναι {size / 1024 / 1024:.1f} MiB — το όριο του updater είναι "
            f"{MAX_ASSET_BYTES / 1024 / 1024:.0f} MiB"
        )
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise SignError(f"το zip έχει {len(infos)} entries — το όριο είναι {MAX_ENTRIES}")
            uncompressed = sum(item.file_size for item in infos)
            if uncompressed > MAX_EXTRACTED_BYTES:
                raise SignError(
                    f"το zip αποσυμπιέζεται σε {uncompressed / 1024 / 1024:.0f} MiB — "
                    f"το όριο είναι {MAX_EXTRACTED_BYTES / 1024 / 1024:.0f} MiB"
                )
            names = zf.namelist()
            if not any(Path(name).name in BUNDLE_MARKERS for name in names):
                raise SignError("το zip δεν περιέχει MUXIVRA.exe / MUXIVRA.cmd — ο updater θα το απέρριπτε")
            inner_version = None
            for name in names:
                if name.endswith("app/sshdeck/__init__.py") or name == "app/sshdeck/__init__.py":
                    match = INNER_VERSION_RE.search(zf.read(name).decode("utf-8", "replace"))
                    inner_version = match.group(1) if match else None
                    break
    except zipfile.BadZipFile as exc:
        raise SignError(f"μη έγκυρο zip: {exc}") from exc
    return {
        "bytes": size,
        "entries": len(infos),
        "uncompressed": uncompressed,
        "inner_version": inner_version,
    }


def built_at(wanted: str | None) -> str:
    if wanted:
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$", wanted.strip()):
            raise SignError(f"μη έγκυρο --built-at «{wanted}» — αναμένεται ISO8601, π.χ. 2026-11-18T20:00:00Z")
        return wanted.strip()
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch.isdigit():
        moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ----------------------------------------------------------------------- main
def sign(args) -> int:
    private_key = load_private_key(load_key_material(args.key))
    pubkey = public_b64(private_key)

    if args.print_pubkey:
        info(pubkey)
        set_output(pubkey_b64=pubkey)
        return 0

    if not args.zip:
        raise SignError("δεν δόθηκε --zip")
    if not args.version:
        raise SignError("δεν δόθηκε --version")

    archive = Path(args.zip).expanduser()
    if not archive.is_file():
        raise SignError(f"το zip δεν βρέθηκε: {archive}")
    version = normalise_version(args.version)
    facts = inspect_archive(archive)

    if facts["inner_version"] and facts["inner_version"].lstrip("vV") != version:
        warn(
            f"το --version είναι {version} αλλά το bundle περιέχει sshdeck {facts['inner_version']} — "
            "ενημέρωσε το sshdeck/__init__.py __version__ πριν το release"
        )

    digest = sha256_file(archive)
    manifest = {
        "version": version,
        "archive": archive.name,
        "sha256": digest,
        "built_at": built_at(args.built_at),
    }
    if args.repo:
        manifest["repo"] = args.repo

    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    signature = private_key.sign(manifest_bytes)

    out_dir = Path(args.out).expanduser() if args.out else archive.parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SignError(f"δεν δημιουργείται ο φάκελος εξόδου: {exc}") from exc

    manifest_path = out_dir / MANIFEST_NAME
    signature_path = out_dir / SIGNATURE_NAME
    pubkey_path = out_dir / args.pubkey_name
    signature_b64 = base64.b64encode(signature).decode("ascii")

    for path, data in (
        (manifest_path, manifest_bytes),
        (signature_path, (signature_b64 + "\n").encode("ascii")),
        (pubkey_path, (pubkey + "\n").encode("ascii")),
    ):
        temp = path.with_name(path.name + ".tmp")
        try:
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise SignError(f"δεν γράφτηκε το {path}: {exc}") from exc

    # Self-check from disk: exactly what a client will download and verify.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    read_manifest = manifest_path.read_bytes()
    read_sig = base64.b64decode(signature_path.read_text(encoding="ascii").strip(), validate=True)
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey)).verify(read_sig, read_manifest)
    except InvalidSignature as exc:
        raise SignError("self-check απέτυχε: η υπογραφή δεν επαληθεύεται") from exc
    if sha256_file(archive) != json.loads(read_manifest)["sha256"]:
        raise SignError("self-check απέτυχε: το sha256 άλλαξε μετά την υπογραφή")

    size_mib = facts["bytes"] / 1024 / 1024
    info(f"version   : {version}")
    info(f"archive   : {archive} ({size_mib:.1f} MiB, {facts['entries']} entries)")
    info(f"sha256    : {digest}")
    info(f"built_at  : {manifest['built_at']}")
    info(f"manifest  : {manifest_path} ({len(manifest_bytes)} bytes)")
    info(f"signature : {signature_path} ({len(signature)} bytes, base64)")
    info(f"public key: {pubkey}")
    info("self-check: signature verified against the written files")
    info("")
    info("Pin the public key on the client once:")
    info(f"  echo {pubkey} > <data>/update-pubkey.txt      # or SSHDECK_UPDATE_PUBKEY")

    set_output(
        version=version,
        sha256=digest,
        manifest=str(manifest_path),
        signature=str(signature_path),
        pubkey_b64=pubkey,
        archive=str(archive),
        archive_bytes=str(facts["bytes"]),
    )
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Υπογραφή MUXIVRA release (release.json + release.json.sig)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Το ιδιωτικό κλειδί δεν γράφεται ποτέ σε αρχείο και δεν εκτυπώνεται.",
    )
    parser.add_argument("--zip", help="portable zip προς υπογραφή (π.χ. dist/MUXIVRA-portable-win64.zip)")
    parser.add_argument("--version", help="έκδοση χωρίς 'v' (π.χ. 0.4.0)")
    parser.add_argument(
        "--key",
        help="PEM αρχείο κλειδιού, '-' για stdin, ή inline κλειδί· αλλιώς MUXIVRA_SIGNING_KEY_B64",
    )
    parser.add_argument("--out", help="φάκελος εξόδου (default: ο φάκελος του zip)")
    parser.add_argument("--built-at", help="ISO8601 timestamp (default: τώρα· SOURCE_DATE_EPOCH γίνεται σεβαστό)")
    parser.add_argument("--repo", help="owner/name, προαιρετικό πεδίο στο manifest")
    parser.add_argument("--pubkey-name", default="release.pubkey.txt", help="όνομα αρχείου δημόσιου κλειδιού")
    parser.add_argument("--print-pubkey", action="store_true", help="τύπωσε μόνο το base64 δημόσιο κλειδί και έξοδος")
    parser.add_argument("--debug", action="store_true", help="δείξε traceback σε απρόσμενο σφάλμα")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return sign(args)
    except SignError as exc:
        fail(str(exc))
        return 2
    except KeyboardInterrupt:
        fail("ακυρώθηκε", 1)
        return 1
    except Exception as exc:  # noqa: BLE001 - never leak a traceback by default
        if args.debug:
            raise
        fail(f"απρόσμενο σφάλμα: {type(exc).__name__}: {exc}", 1)
        return 1


if __name__ == "__main__":
    sys.exit(main())
