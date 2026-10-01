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
    IS_GIT=0
    if command -v git >/dev/null 2>&1; then
        GIT_TOPLEVEL="$(git -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
        if [ -n "$GIT_TOPLEVEL" ]; then
            ROOT_DIR_REAL="$(cd "$ROOT_DIR" && pwd -P)"
            GIT_TOPLEVEL_REAL="$(cd "$GIT_TOPLEVEL" && pwd -P)"
            if [ "$ROOT_DIR_REAL" = "$GIT_TOPLEVEL_REAL" ]; then
                IS_GIT=1
            fi
        fi
    fi

    # Reject symlinked attestation files fail-closed
    if [ -L "$ROOT_DIR/.source-commit" ]; then
        echo "Blocked: attestation file in '$ROOT_DIR/.source-commit' cannot be a symlink." >&2
        exit 2
    fi
    if [ -L "$ROOT_DIR/.toolrepo-sha" ]; then
        echo "Blocked: attestation file in '$ROOT_DIR/.toolrepo-sha' cannot be a symlink." >&2
        exit 2
    fi

    if [ "$IS_GIT" = "1" ]; then
        # Git HEAD is authoritative whenever Git identity is available
        ACTUAL_SHA="$(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
        if [ -f "$ROOT_DIR/.source-commit" ]; then
            SC_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.source-commit")"
            if [ "$SC_SHA" != "$ACTUAL_SHA" ]; then
                echo "Blocked: .source-commit ($SC_SHA) does not match Git HEAD ($ACTUAL_SHA)." >&2
                exit 2
            fi
        fi
        if [ -f "$ROOT_DIR/.toolrepo-sha" ]; then
            TS_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.toolrepo-sha")"
            if [ "$TS_SHA" != "$ACTUAL_SHA" ]; then
                echo "Blocked: .toolrepo-sha ($TS_SHA) does not match Git HEAD ($ACTUAL_SHA)." >&2
                exit 2
            fi
        fi
    else
        # Standalone directory: resolve from attestation files
        SC_SHA=""
        TS_SHA=""
        if [ -f "$ROOT_DIR/.source-commit" ]; then
            SC_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.source-commit")"
        fi
        if [ -f "$ROOT_DIR/.toolrepo-sha" ]; then
            TS_SHA="$(tr -d '[:space:]' < "$ROOT_DIR/.toolrepo-sha")"
        fi

        if [ -n "$SC_SHA" ] && [ -n "$TS_SHA" ]; then
            if [ "$SC_SHA" != "$TS_SHA" ]; then
                echo "Blocked: conflicting standalone attestations: .source-commit ($SC_SHA) != .toolrepo-sha ($TS_SHA)." >&2
                exit 2
            fi
            ACTUAL_SHA="$SC_SHA"
        elif [ -n "$SC_SHA" ]; then
            ACTUAL_SHA="$SC_SHA"
        elif [ -n "$TS_SHA" ]; then
            ACTUAL_SHA="$TS_SHA"
        else
            echo "Blocked: unable to resolve source identity in '$ROOT_DIR' (not a git repository and no source attestation found)." >&2
            exit 2
        fi
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

CLEANUP_TARGET=""
CLEANUP_IDENTITY=""
ARM_CLEANUP=0
CLAIM_TOKEN_FILE=""

cleanup_owned_target() {
    local primary_status="$1"
    if [ -n "${CLAIM_TOKEN_FILE:-}" ] && [ -s "$CLAIM_TOKEN_FILE" ] && [ -z "$CLEANUP_IDENTITY" ]; then
        CLEANUP_IDENTITY="$(tr -d '[:space:]' < "$CLAIM_TOKEN_FILE" 2>/dev/null || true)"
        CLEANUP_TARGET="$VENV_TARGET"
        ARM_CLEANUP=1
    fi
    if [ "$ARM_CLEANUP" -eq 1 ] && [ -n "$CLEANUP_TARGET" ] && [ "$CLEANUP_TARGET" != "/" ]; then
        if [ -z "$CLEANUP_IDENTITY" ]; then
            echo "Warning: AgentReview toolrepo cleanup skipped: directory object identity was never verified; preserving original install failure $primary_status." >&2
            if [ -n "${CLAIM_TOKEN_FILE:-}" ]; then
                rm -f "$CLAIM_TOKEN_FILE" 2>/dev/null || true
            fi
            return 0
        fi
        if [ -L "$CLEANUP_TARGET" ]; then
            echo "Warning: AgentReview toolrepo cleanup skipped: target '$CLEANUP_TARGET' is a symlink; preserving original install failure $primary_status." >&2
            if [ -n "${CLAIM_TOKEN_FILE:-}" ]; then
                rm -f "$CLAIM_TOKEN_FILE" 2>/dev/null || true
            fi
            return 0
        fi
        local current_identity
        current_identity="$(stat -c "%d:%i" "$CLEANUP_TARGET" 2>/dev/null || true)"
        if [ "$current_identity" != "$CLEANUP_IDENTITY" ]; then
            echo "Warning: AgentReview toolrepo cleanup skipped: target '$CLEANUP_TARGET' identity changed ($current_identity != $CLEANUP_IDENTITY); preserving original install failure $primary_status." >&2
            if [ -n "${CLAIM_TOKEN_FILE:-}" ]; then
                rm -f "$CLAIM_TOKEN_FILE" 2>/dev/null || true
            fi
            return 0
        fi
        if [ -e "$CLEANUP_TARGET" ]; then
            if rm -rf "$CLEANUP_TARGET"; then
                :
            else
                local cleanup_status=$?
                echo "Warning: AgentReview toolrepo cleanup failed with status $cleanup_status for target '$CLEANUP_TARGET'; preserving original install failure $primary_status." >&2
            fi
        fi
    fi
    if [ -n "${CLAIM_TOKEN_FILE:-}" ]; then
        rm -f "$CLAIM_TOKEN_FILE" 2>/dev/null || true
    fi
}

handle_exit() {
    local original_status=$?
    trap - EXIT INT TERM
    if [ "$original_status" -eq 0 ]; then
        original_status=1
    fi
    cleanup_owned_target "$original_status"
    exit "$original_status"
}

handle_int() {
    trap - EXIT INT TERM
    cleanup_owned_target 130
    exit 130
}

handle_term() {
    trap - EXIT INT TERM
    cleanup_owned_target 143
    exit 143
}

trap handle_exit EXIT
trap handle_int INT
trap handle_term TERM

VENV_PARENT="$(dirname "$VENV_TARGET")"
if [ ! -d "$VENV_PARENT" ]; then
    mkdir -p "$VENV_PARENT" || {
        echo "Blocked: failed to create parent directory for target: $VENV_PARENT" >&2
        exit 2
    }
fi

CLAIM_TOKEN_FILE="$(mktemp "$VENV_PARENT/.agent_review_token.XXXXXX")" || {
    echo "Blocked: failed to create claim token file in $VENV_PARENT" >&2
    exit 2
}

CLAIM_OUTPUT=""
CLAIM_STATUS=0
CLAIM_OUTPUT="$("$PYTHON_BIN" -I -S -c '
import os, sys, subprocess

target = sys.argv[1]
token_file = sys.argv[2]
try:
    res = subprocess.run(["mkdir", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode != 0:
        sys.exit(2)
except Exception:
    sys.exit(3)

try:
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
except Exception:
    sys.exit(4)

try:
    orig_st = os.fstat(fd)
    if orig_st.st_dev <= 0 or orig_st.st_ino <= 0:
        os.close(fd)
        sys.exit(4)
except Exception:
    os.close(fd)
    sys.exit(4)

try:
    with os.scandir(target) as it:
        if any(it):
            os.close(fd)
            sys.exit(5)
except Exception:
    os.close(fd)
    sys.exit(5)

try:
    if os.path.islink(target):
        os.close(fd)
        sys.exit(5)
    cur_st = os.stat(target)
    if (cur_st.st_dev, cur_st.st_ino) != (orig_st.st_dev, orig_st.st_ino):
        os.close(fd)
        sys.exit(5)
except Exception:
    os.close(fd)
    sys.exit(5)

os.close(fd)
ident = f"{orig_st.st_dev}:{orig_st.st_ino}"
try:
    with open(token_file, "w") as f:
        f.write(ident)
except Exception:
    sys.exit(4)
print(ident)
sys.exit(0)
' "$VENV_TARGET" "$CLAIM_TOKEN_FILE" 2>/dev/null)" || CLAIM_STATUS=$?

if [ "$CLAIM_STATUS" -eq 130 ]; then
    handle_int
elif [ "$CLAIM_STATUS" -eq 143 ]; then
    handle_term
elif [ "$CLAIM_STATUS" -eq 2 ]; then
    if [ "$VENV_DIR" != "$VENV_TARGET" ]; then
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path (requested: '$VENV_DIR', canonical target: '$VENV_TARGET')." >&2
    else
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: $VENV_TARGET" >&2
    fi
    exit 2
elif [ "$CLAIM_STATUS" -eq 5 ]; then
    echo "Blocked: directory object identity for '$VENV_TARGET' was substituted before acquisition finalized." >&2
    exit 2
elif [ "$CLAIM_STATUS" -ne 0 ] || [ -z "$CLAIM_OUTPUT" ]; then
    echo "Blocked: unable to acquire verified directory object identity for '$VENV_TARGET' (claim status $CLAIM_STATUS)." >&2
    exit 2
fi

CLEANUP_TARGET="$VENV_TARGET"
CLEANUP_IDENTITY="$CLAIM_OUTPUT"
ARM_CLEANUP=1
rm -f "$CLAIM_TOKEN_FILE" 2>/dev/null || true
CLAIM_TOKEN_FILE=""

"$PYTHON_BIN" -I -S -m venv "$VENV_TARGET"
# Deliberately does NOT run `pip install --upgrade pip` first: that step
# would fetch whatever pip version happens to be latest at install time,
# an unpinned, unverified download that undermines reproducibility between
# two installs of the same lock file. The venv's own bundled pip (from
# Python's ensurepip) already supports --require-hashes.
PIP_CONFIG_FILE=/dev/null "$VENV_TARGET/bin/python3" -I -m pip --isolated install --require-hashes --no-deps -r "$LOCK_FILE"

ARM_CLEANUP=0
trap - EXIT INT TERM

echo "AgentReview toolrepo venv ready at: $VENV_TARGET"
echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
    echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
fi
