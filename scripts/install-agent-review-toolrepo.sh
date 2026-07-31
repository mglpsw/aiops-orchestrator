#!/usr/bin/env bash
# AgentReview offline toolrepo -- minimal, dedicated, hash-pinned install.
#
# Creates a venv separate from the runtime AIOps environment and installs
# only requirements-agent-review.lock with pip --require-hashes --no-deps.
# Does not touch requirements.txt/requirements-dev.txt or any FastAPI/
# Uvicorn/SQLAlchemy/database-driver dependency.
#
# Usage:
#   bash scripts/install-agent-review-toolrepo.sh <venv-dir> [--toolrepo-sha <40-hex-sha>]
#
# When --toolrepo-sha is given, it is verified against `git rev-parse HEAD`
# of this checkout and MUST be the full lowercase 40-character commit SHA.
# A branch name, tag name, or abbreviated SHA is rejected -- this script
# never treats a moving ref as a valid consumption pin.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$ROOT_DIR/requirements-agent-review.lock"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <venv-dir> [--toolrepo-sha <40-hex-sha>]" >&2
    exit 2
fi

VENV_DIR="$1"
shift || true

TOOLREPO_SHA=""
if [ "${1:-}" = "--toolrepo-sha" ]; then
    TOOLREPO_SHA="${2:-}"
    shift 2 || true
fi

if [ -n "$TOOLREPO_SHA" ]; then
    if ! [[ "$TOOLREPO_SHA" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Blocked: --toolrepo-sha must be a full lowercase 40-character commit SHA, not a branch/tag/short SHA." >&2
        exit 2
    fi
    ACTUAL_SHA="$(cd "$ROOT_DIR" && git rev-parse HEAD)"
    if [ "$TOOLREPO_SHA" != "$ACTUAL_SHA" ]; then
        echo "Blocked: --toolrepo-sha ($TOOLREPO_SHA) does not match this checkout's HEAD ($ACTUAL_SHA)." >&2
        exit 2
    fi
fi

if [ ! -f "$LOCK_FILE" ]; then
    echo "Blocked: $LOCK_FILE not found." >&2
    exit 2
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python3" -m pip install --upgrade pip --quiet
"$VENV_DIR/bin/python3" -m pip install --require-hashes --no-deps -r "$LOCK_FILE"

echo "AgentReview toolrepo venv ready at: $VENV_DIR"
echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
if [ -n "$TOOLREPO_SHA" ]; then
    echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
fi
