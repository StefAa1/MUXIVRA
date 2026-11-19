#!/usr/bin/env bash
# Sign the release manifest with the key that lives on the signing host.
# The key never touches this machine's disk: it is read over SSH, converted to
# the 44-char base64 seed in memory, and passed to sign_release.py via env.
#
#   scripts/sign_release_remote.sh 0.5.2
set -euo pipefail

VERSION="${1:?usage: sign_release_remote.sh <version>}"
KEY_HOST="${MUXIVRA_KEY_HOST:-root@10.10.1.120}"
KEY_PATH="${MUXIVRA_KEY_PATH:-/root/muxivra-signing/update-signing.key}"
SSH_KEY="${MUXIVRA_SSH_KEY:-/root/.ssh/muxivra_signer}"
REPO="${MUXIVRA_REPO:-StefAa1/MUXIVRA}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

SEED_B64="$(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o ConnectTimeout=10 "$KEY_HOST" \
  "python3 -c \"import base64,sys;from cryptography.hazmat.primitives import serialization as s;k=s.load_pem_private_key(open('$KEY_PATH','rb').read(),password=None);print(base64.b64encode(k.private_bytes(s.Encoding.Raw,s.PrivateFormat.Raw,s.NoEncryption())).decode())\"")"

if [ "${#SEED_B64}" -ne 44 ]; then
  echo "unexpected key material length from $KEY_HOST" >&2
  exit 2
fi

cd "$ROOT"
MUXIVRA_SIGNING_KEY_B64="$SEED_B64" .venv/bin/python tools/sign_release.py \
  --zip dist/MUXIVRA-portable-win64.zip \
  --version "$VERSION" \
  --repo "$REPO" | grep -E "manifest|signature|self-check|error"
