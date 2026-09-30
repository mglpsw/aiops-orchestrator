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
# When --toolrepo-sha is given, it is verified against the verifiable
# source identity (.source-commit / .toolrepo-sha attestation, or git
# rev-parse HEAD in git checkouts) and MUST be the full lowercase 40-hex commit SHA.
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
TOOLREPO_SHA_PROVIDED=0
if [ "${1:-}" = "--toolrepo-sha" ]; then
    TOOLREPO_SHA_PROVIDED=1
    TOOLREPO_SHA="${2:-}"
    shift 2 || true
fi

# Validate whenever the flag was given at all, including an explicitly
# empty value (--toolrepo-sha ""). Checking `-n "$TOOLREPO_SHA"` alone would
# silently treat an empty argument the same as the flag never being passed,
# letting a broken/empty CI variable slip through as "no pin requested"
# instead of being rejected as an invalid pin.
if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
    if ! [[ "$TOOLREPO_SHA" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Blocked: --toolrepo-sha must be a full lowercase 40-character commit SHA, not empty, a branch/tag, or a short SHA." >&2
        exit 2
    fi
    ACTUAL_SHA=""
    if [ -f "$ROOT_DIR/.source-commit" ]; then
        ACTUAL_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.source-commit")"
    elif [ -f "$ROOT_DIR/.toolrepo-sha" ]; then
        ACTUAL_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.toolrepo-sha")"
    elif [ -d "$ROOT_DIR/.git" ] || git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        ACTUAL_SHA="$(cd "$ROOT_DIR" && git rev-parse HEAD 2>/dev/null || true)"
    else
        echo "Blocked: unable to resolve source identity in '$ROOT_DIR' (not a git repository and no source attestation found)." >&2
        exit 2
    fi

    if ! [[ "$ACTUAL_SHA" =~ ^[0-9a-f]{40}$ ]]; then
        echo "Blocked: resolved source identity '$ACTUAL_SHA' is not a valid 40-character commit SHA." >&2
        exit 2
    fi

    if [ "$TOOLREPO_SHA" != "$ACTUAL_SHA" ]; then
        echo "Blocked: --toolrepo-sha ($TOOLREPO_SHA) does not match source identity ($ACTUAL_SHA)." >&2
        exit 2
    fi
fi

if [ ! -f "$LOCK_FILE" ]; then
    echo "Blocked: $LOCK_FILE not found." >&2
    exit 2
fi

PYTHON_BIN="${AGENT_REVIEW_PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Blocked: selected Python interpreter '$PYTHON_BIN' not found." >&2
    exit 2
fi

PLATFORM_STATUS="$("$PYTHON_BIN" -I -S -c '
import sys, platform, struct
impl = platform.python_implementation()
ver = f"{sys.version_info.major}.{sys.version_info.minor}"
os_name = platform.system()
mach = platform.machine()
libc_name, libc_ver = platform.libc_ver()
pointer_bits = struct.calcsize("P") * 8

errors = []
if impl != "CPython" or ver != "3.11":
    errors.append(f"interpreter {impl} {ver} (required: CPython 3.11)")
if os_name != "Linux":
    errors.append(f"OS {os_name} (required: Linux)")
if mach not in ("x86_64", "AMD64"):
    errors.append(f"architecture {mach} (required: x86_64)")
if pointer_bits != 64:
    errors.append(f"word size {pointer_bits}-bit (required: 64-bit)")
libc_desc = libc_name if libc_name else "non-glibc/musl"
if libc_name.lower() != "glibc":
    errors.append(f"libc {libc_desc} (required: glibc >= 2.17)")
else:
    try:
        parts = [int(p) for p in libc_ver.split(".")[:2]]
        if len(parts) < 2 or tuple(parts) < (2, 17):
            errors.append(f"glibc {libc_ver} (required: glibc >= 2.17)")
    except Exception:
        errors.append(f"unparseable glibc version {libc_ver!r} (required: glibc >= 2.17)")

if errors:
    print("INCOMPATIBLE: " + "; ".join(errors))
else:
    print("OK")
' 2>/dev/null || echo "PROBE_FAILED")"

if [ "$PLATFORM_STATUS" != "OK" ]; then
    echo "Blocked: requirements-agent-review.lock is qualified for CPython 3.11 on Linux x86_64 (glibc >= 2.17); platform incompatibility detected: $PLATFORM_STATUS." >&2
    exit 2
fi

if [ -z "$VENV_DIR" ]; then
    echo "Blocked: target venv directory cannot be empty." >&2
    exit 2
fi

VENV_TARGET="$("$PYTHON_BIN" -I -S -c '
import os, sys
raw = sys.argv[1]
if not raw or not raw.strip():
    sys.exit(2)
print(os.path.abspath(raw))
' "$VENV_DIR" 2>/dev/null || true)"

if [ -z "$VENV_TARGET" ]; then
    echo "Blocked: target venv directory '$VENV_DIR' is invalid or cannot be normalized." >&2
    exit 2
fi

if [ -e "$VENV_TARGET" ] || [ -L "$VENV_TARGET" ]; then
    if [ "$VENV_DIR" != "$VENV_TARGET" ]; then
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path (requested: '$VENV_DIR', canonical target: '$VENV_TARGET')." >&2
    else
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: $VENV_TARGET" >&2
    fi
    exit 2
fi

"$PYTHON_BIN" -I -S -m venv "$VENV_TARGET"
# Deliberately does NOT run `pip install --upgrade pip` first: that step
# would fetch whatever pip version happens to be latest at install time,
# an unpinned, unverified download that undermines reproducibility between
# two installs of the same lock file. The venv's own bundled pip (from
# Python's ensurepip) already supports --require-hashes.
PIP_CONFIG_FILE=/dev/null "$VENV_TARGET/bin/python3" -I -m pip --isolated install --require-hashes --no-deps -r "$LOCK_FILE"

echo "AgentReview toolrepo venv ready at: $VENV_TARGET"
echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
    echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
fi
