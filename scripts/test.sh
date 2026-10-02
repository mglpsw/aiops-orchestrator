#!/usr/bin/env bash
# Canonical offline runner; no installs. --serial, --lane, and pytest filters.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
exec python3 -m scripts.test_runner "$@"
