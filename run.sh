#!/usr/bin/env bash
# Start SSHDeck (default: 127.0.0.1:8722)
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m sshdeck serve "$@"
