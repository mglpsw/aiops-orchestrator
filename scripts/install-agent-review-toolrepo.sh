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

# ASCII shell-escaped, delimited display only; caller values remain unchanged.
render_path() {
    local LC_ALL=C
    printf '<%q>' "$1"
}

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
[ "$SCRIPT_DIR" = "${BASH_SOURCE[0]}" ] && SCRIPT_DIR="."
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LOCK_FILE="$ROOT_DIR/requirements-agent-review.lock"

if [ $# -lt 1 ]; then
    echo "Usage: $(render_path "$0") <venv-dir> [--toolrepo-sha <40-hex-sha>]" >&2
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
    GIT_SEARCH_PATH="$(getconf PATH 2>/dev/null || echo "/bin:/usr/bin")"
    GIT_BIN="$(PATH="$GIT_SEARCH_PATH" command -v git 2>/dev/null || true)"
    if [ -n "$GIT_BIN" ]; then
        GIT_TOPLEVEL="$(env -i PATH="$GIT_SEARCH_PATH" LC_ALL=C LANG=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0 GIT_OPTIONAL_LOCKS=0 "$GIT_BIN" -C "$ROOT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
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
        echo "Blocked: attestation file in '$(render_path "$ROOT_DIR")/.source-commit' cannot be a symlink." >&2
        exit 2
    fi
    if [ -L "$ROOT_DIR/.toolrepo-sha" ]; then
        echo "Blocked: attestation file in '$(render_path "$ROOT_DIR")/.toolrepo-sha' cannot be a symlink." >&2
        exit 2
    fi

    if [ "$IS_GIT" = "1" ]; then
        # Git HEAD is authoritative whenever Git identity is available
        ACTUAL_SHA="$(env -i PATH="$GIT_SEARCH_PATH" LC_ALL=C LANG=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0 GIT_OPTIONAL_LOCKS=0 "$GIT_BIN" -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || true)"
        if [ -f "$ROOT_DIR/.source-commit" ]; then
            SC_SHA="$(< "$ROOT_DIR/.source-commit")" || true
            SC_SHA="${SC_SHA//[[:space:]]/}"
            if [ "$SC_SHA" != "$ACTUAL_SHA" ]; then
                echo "Blocked: .source-commit ($SC_SHA) does not match Git HEAD ($ACTUAL_SHA)." >&2
                exit 2
            fi
        fi
        if [ -f "$ROOT_DIR/.toolrepo-sha" ]; then
            TS_SHA="$(< "$ROOT_DIR/.toolrepo-sha")" || true
            TS_SHA="${TS_SHA//[[:space:]]/}"
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
            SC_SHA="$(< "$ROOT_DIR/.source-commit")" || true
            SC_SHA="${SC_SHA//[[:space:]]/}"
        fi
        if [ -f "$ROOT_DIR/.toolrepo-sha" ]; then
            TS_SHA="$(< "$ROOT_DIR/.toolrepo-sha")" || true
            TS_SHA="${TS_SHA//[[:space:]]/}"
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
            echo "Blocked: unable to resolve source identity in '$(render_path "$ROOT_DIR")' (not a git repository and no source attestation found)." >&2
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
    echo "Blocked: $(render_path "$LOCK_FILE") not found." >&2
    exit 2
fi

if [ -n "${AGENT_REVIEW_PYTHON:-}" ]; then
    PYTHON_BIN="$(command -v "$AGENT_REVIEW_PYTHON" 2>/dev/null || true)"
    if [ -z "$PYTHON_BIN" ]; then
        echo "Blocked: selected Python interpreter '$(render_path "$AGENT_REVIEW_PYTHON")' not found." >&2
        exit 2
    fi
    if [[ "$PYTHON_BIN" != /* ]]; then
        if [[ "$PYTHON_BIN" == */* ]]; then
            PYTHON_BIN="$(cd "${PYTHON_BIN%/*}" 2>/dev/null && pwd -P)/${PYTHON_BIN##*/}"
        else
            PYTHON_BIN="$PWD/$PYTHON_BIN"
        fi
    fi
else
    PYTHON_BIN="python3"
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        echo "Blocked: selected Python interpreter '$(render_path "$PYTHON_BIN")' not found." >&2
        exit 2
    fi
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
if "\x00" in raw or "\n" in raw or "\r" in raw:
    sys.exit(2)
raw_bytes = os.fsencode(raw)
if len(raw_bytes) > 4096:
    sys.exit(2)
target = os.path.abspath(raw)
target_bytes = os.fsencode(target)
if len(target_bytes) > 4096:
    sys.exit(2)
for part in target.split(os.sep):
    if len(os.fsencode(part)) > 255:
        sys.exit(2)
print(target)
' "$VENV_DIR" 2>/dev/null || true)"

if [ -z "$VENV_TARGET" ]; then
    echo "Blocked: target venv directory '$(render_path "$VENV_DIR")' is invalid or cannot be normalized." >&2
    exit 2
fi

# Parent creation belongs to the authority, after capability admission.

if [ -e "$VENV_TARGET" ] || [ -L "$VENV_TARGET" ]; then
    if [ "$VENV_DIR" != "$VENV_TARGET" ]; then
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path (requested: '$(render_path "$VENV_DIR")', canonical target: '$(render_path "$VENV_TARGET")')." >&2
    else
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: $(render_path "$VENV_TARGET")" >&2
    fi
    exit 2
fi

# Transfer the installer PID and signal destination to the sole authority.
# The selected/qualified bootstrap executes the complete stdlib helper. No
# background reaper, Bash KILL timeout, FIFO witness or second commit classifier.
INSTALL_AUTHORITY="$ROOT_DIR/scripts/agent-review-install-authority.py"
if [ ! -f "$INSTALL_AUTHORITY" ]; then
    echo "Blocked: installation authority missing: $(render_path "$INSTALL_AUTHORITY")" >&2
    exit 2
fi
AUTHORITY_CODE="$(< "$INSTALL_AUTHORITY")" || {
    echo "Blocked: cannot read installation authority: $(render_path "$INSTALL_AUTHORITY")" >&2
    exit 2
}
if [ -z "$AUTHORITY_CODE" ]; then
    echo "Blocked: empty installation authority: $(render_path "$INSTALL_AUTHORITY")" >&2
    exit 2
fi
exec "$PYTHON_BIN" -I -S -c "$AUTHORITY_CODE" "$PYTHON_BIN" "$VENV_DIR" "$VENV_TARGET" "$LOCK_FILE" "$TOOLREPO_SHA"
