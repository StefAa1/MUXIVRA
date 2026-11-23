#!/usr/bin/env bash
# Ένα release τη φορά, χωρίς ασυμφωνίες tag ↔ BUILD.txt ↔ artifacts.
#
# Χρήση:  scripts/release.sh 0.6.4
# Κάνει: έλεγχος ότι το version στο source είναι το ίδιο, καθαρό build (ένα),
# portable zip + installer + signature, tag στο ΙΔΙΟ commit, push, και στο
# τέλος τυπώνει τα sha256 για επαλήθευση.
set -euo pipefail

VERSION="${1:?χρήση: scripts/release.sh <version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

SOURCE_VERSION="$("$PY" - <<'PY'
import re, pathlib
text = pathlib.Path("sshdeck/__init__.py").read_text()
print(re.search(r'__version__ = "([^"]+)"', text).group(1))
PY
)"

if [[ "$SOURCE_VERSION" != "$VERSION" ]]; then
  echo "✖ το source λέει $SOURCE_VERSION αλλά ζητήθηκε $VERSION" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "✖ υπάρχουν uncommitted αλλαγές — commit πρώτα, ώστε το BUILD.txt να δείχνει το σωστό commit" >&2
  exit 1
fi

COMMIT="$(git rev-parse --short HEAD)"
echo "→ release $VERSION @ $COMMIT"
echo "→ tests"
"$PY" -m pytest -q | tail -2

echo "→ portable bundle + zip"
"$PY" scripts/build_windows_portable.py | tail -1
echo "→ installer"
"$PY" scripts/build_installer.py | tail -3
echo "→ sign manifest"
./scripts/sign_release_remote.sh "$VERSION" | tail -1

BUNDLE_BUILD="$(head -1 dist/MUXIVRA-portable-win64/BUILD.txt)"
if [[ "$BUNDLE_BUILD" != "$VERSION+$COMMIT" ]]; then
  echo "✖ BUILD.txt λέει $BUNDLE_BUILD, περίμενα $VERSION+$COMMIT" >&2
  exit 1
fi

echo "→ tag v$VERSION @ $COMMIT"
git tag -a "v$VERSION" -m "MUXIVRA $VERSION"
git push origin HEAD
git push origin "v$VERSION"

echo
echo "✔ $VERSION έτοιμο"
cat "dist/MUXIVRA-Setup-$VERSION.exe.sha256"
"$PY" - <<PY
import hashlib, json, pathlib
z = pathlib.Path("dist/MUXIVRA-portable-win64.zip")
print("zip   :", hashlib.sha256(z.read_bytes()).hexdigest())
print("manifest:", json.loads(pathlib.Path("dist/release.json").read_text())["sha256"])
PY
echo "GitHub: https://github.com/StefAa1/MUXIVRA/releases/tag/v$VERSION (CI χτίζει ξεχωριστά το ίδιο commit)"
