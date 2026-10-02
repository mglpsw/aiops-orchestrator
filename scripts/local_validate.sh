#!/usr/bin/env bash
# Full offline validation; receipt defaults to a temporary directory.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
exec python3 -m scripts.local_validation "$@"
