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

VENV_PARENT="$(dirname "$VENV_TARGET")"
if [ ! -d "$VENV_PARENT" ]; then
    mkdir -p "$VENV_PARENT" || {
        echo "Blocked: failed to create parent directory for target: $VENV_PARENT" >&2
        exit 2
    }
fi

if [ -e "$VENV_TARGET" ] || [ -L "$VENV_TARGET" ]; then
    if [ "$VENV_DIR" != "$VENV_TARGET" ]; then
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path (requested: '$VENV_DIR', canonical target: '$VENV_TARGET')." >&2
    else
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: $VENV_TARGET" >&2
    fi
    exit 2
fi

# Enable monitor mode for process group isolation and signal tracking
set -m

PRIVATE_STAGE=""
COMMITTED=0
ACTIVE_PID=""
ACTIVE_PGID=""

cleanup_stage() {
    local primary_status="$1"
    if [ "$COMMITTED" -eq 1 ] || has_commit_witness; then
        return 0
    fi
    if [ -n "${PRIVATE_STAGE:-}" ] && [ -d "$PRIVATE_STAGE" ]; then
        if rm -rf "$PRIVATE_STAGE"; then
            :
        else
            local cleanup_status=$?
            echo "Warning: AgentReview toolrepo cleanup failed with status $cleanup_status for target '$PRIVATE_STAGE'; preserving original install failure $primary_status." >&2
        fi
        PRIVATE_STAGE=""
    fi
}

has_commit_witness() {
    if [ "$COMMITTED" -eq 1 ]; then
        return 0
    fi
    if read -t 0 -u 3 2>/dev/null; then
        local token=""
        read -u 3 token 2>/dev/null || true
        if [ "$token" = "COMMITTED" ]; then
            COMMITTED=1
            return 0
        fi
    fi
    return 1
}

close_commit_witness() {
    exec 3>&- 2>/dev/null || true
}

kill_active_group() {
    local sig="$1"
    if [ -n "${ACTIVE_PID:-}" ]; then
        if [ -n "${ACTIVE_PGID:-}" ]; then
            kill -"$sig" -"$ACTIVE_PGID" 2>/dev/null || true
        fi
        kill -"$sig" "$ACTIVE_PID" 2>/dev/null || true
        wait "$ACTIVE_PID" 2>/dev/null || true
        ACTIVE_PID=""
        ACTIVE_PGID=""
    fi
}

handle_exit() {
    local original_status=$?
    trap - EXIT INT TERM
    kill_active_group TERM
    if has_commit_witness; then
        COMMITTED=1
        original_status=0
    fi
    if [ "$original_status" -eq 0 ] && [ "$COMMITTED" -ne 1 ]; then
        original_status=1
    fi
    cleanup_stage "$original_status"
    close_commit_witness
    exit "$original_status"
}

handle_int() {
    trap - EXIT INT TERM
    kill_active_group INT
    if has_commit_witness; then
        COMMITTED=1
        cleanup_stage 0
        echo "Signal observed after transaction commit; installation already committed." >&2
        echo "AgentReview toolrepo venv ready at: $VENV_TARGET"
        echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
        if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
            echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
        fi
        close_commit_witness
        exit 0
    fi
    cleanup_stage 130
    close_commit_witness
    exit 130
}

handle_term() {
    trap - EXIT INT TERM
    kill_active_group TERM
    if has_commit_witness; then
        COMMITTED=1
        cleanup_stage 0
        echo "Signal observed after transaction commit; installation already committed." >&2
        echo "AgentReview toolrepo venv ready at: $VENV_TARGET"
        echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
        if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
            echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
        fi
        close_commit_witness
        exit 0
    fi
    cleanup_stage 143
    close_commit_witness
    exit 143
}

trap handle_exit EXIT
trap handle_int INT
trap handle_term TERM

run_tracked_step() {
    "$@" &
    ACTIVE_PID=$!
    ACTIVE_PGID="$(ps -o pgid= -p "$ACTIVE_PID" 2>/dev/null | tr -d ' ' || true)"
    if [ -z "$ACTIVE_PGID" ]; then
        ACTIVE_PGID="$ACTIVE_PID"
    fi
    local step_status=0
    wait "$ACTIVE_PID" || step_status=$?
    ACTIVE_PID=""
    ACTIVE_PGID=""
    if [ "$step_status" -eq 130 ]; then
        handle_int
    elif [ "$step_status" -eq 143 ]; then
        handle_term
    fi
    return "$step_status"
}

PRIVATE_STAGE="$(mktemp -d "$VENV_PARENT/.agent_review_stage.XXXXXX")" || {
    echo "Blocked: failed to create private staging directory in $VENV_PARENT" >&2
    exit 2
}
chmod 755 "$PRIVATE_STAGE" 2>/dev/null || true

COMMIT_WITNESS_FIFO="$PRIVATE_STAGE/.commit_witness.fifo"
mkfifo -m 600 "$COMMIT_WITNESS_FIFO" || {
    echo "Blocked: failed to create commit witness fifo in $PRIVATE_STAGE" >&2
    exit 2
}
exec 3<>"$COMMIT_WITNESS_FIFO" || {
    echo "Blocked: failed to open commit witness descriptor" >&2
    exit 2
}
if command -v unlink >/dev/null 2>&1; then
    unlink "$COMMIT_WITNESS_FIFO"
else
    /bin/rm -f "$COMMIT_WITNESS_FIFO" 2>/dev/null || rm -f "$COMMIT_WITNESS_FIFO"
fi

# Step 1: Create venv in private staging directory
run_tracked_step "$PYTHON_BIN" -I -S -m venv "$PRIVATE_STAGE"

# Step 2: Install pinned dependencies into private staging venv
run_tracked_step env PIP_CONFIG_FILE=/dev/null "$PRIVATE_STAGE/bin/python3" -I -m pip --isolated install --require-hashes --no-deps -r "$LOCK_FILE"

# Step 3: RelocationClosureV1 and atomic NO_REPLACE publication
PUBLISH_STATUS=0
run_tracked_step "$PYTHON_BIN" -I -S -c '
import os, sys, errno, ctypes, platform, shutil, signal

stage_dir = sys.argv[1]
final_dir = sys.argv[2]

def rename_noreplace(src, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    AT_FDCWD = -100
    RENAME_NOREPLACE = 1
    src_bytes = os.fsencode(src)
    dst_bytes = os.fsencode(dst)
    if hasattr(libc, "renameat2"):
        rc = libc.renameat2(
            ctypes.c_int(AT_FDCWD),
            src_bytes,
            ctypes.c_int(AT_FDCWD),
            dst_bytes,
            ctypes.c_uint(RENAME_NOREPLACE)
        )
    else:
        mach = platform.machine()
        sys_renameat2 = 316 if mach in ("x86_64", "AMD64") else 276
        rc = libc.syscall(
            ctypes.c_long(sys_renameat2),
            ctypes.c_int(AT_FDCWD),
            src_bytes,
            ctypes.c_int(AT_FDCWD),
            dst_bytes,
            ctypes.c_uint(RENAME_NOREPLACE)
        )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))

# R1: Remove disposable __pycache__ and *.pyc
for root, dirs, files in os.walk(stage_dir, topdown=False):
    for f in files:
        if f.endswith(".pyc"):
            try:
                os.unlink(os.path.join(root, f))
            except OSError:
                pass
    for d in dirs:
        if d == "__pycache__":
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)

# R2: Normalize textual files containing stage path
stage_bytes = os.fsencode(stage_dir)
final_bytes = os.fsencode(final_dir)

for root, dirs, files in os.walk(stage_dir):
    for f in files:
        p = os.path.join(root, f)
        if os.path.islink(p):
            continue
        try:
            with open(p, "rb") as fp:
                data = fp.read()
        except OSError:
            continue
        if stage_bytes in data:
            if b"\x00" in data:
                print(f"Blocked: opaque/binary file contains un-normalizable staging path: {p}", file=sys.stderr)
                sys.exit(2)
            new_data = data.replace(stage_bytes, final_bytes)
            try:
                with open(p, "wb") as fp:
                    fp.write(new_data)
            except OSError:
                sys.exit(2)

# R3: Fail closed on remaining staging references
for root, dirs, files in os.walk(stage_dir):
    for f in files:
        p = os.path.join(root, f)
        if os.path.islink(p):
            try:
                target_link = os.readlink(p)
                if stage_dir in target_link:
                    print(f"Blocked: symlink {p} targets staging path {target_link}", file=sys.stderr)
                    sys.exit(2)
            except OSError:
                pass
            continue
        try:
            with open(p, "rb") as fp:
                content = fp.read()
        except OSError:
            continue
        if stage_bytes in content:
            print(f"Blocked: staging path reference remains in {p}", file=sys.stderr)
            sys.exit(2)

# R4: Executable/shebang census
bin_dir = os.path.join(stage_dir, "bin")
if os.path.isdir(bin_dir):
    for f in os.listdir(bin_dir):
        p = os.path.join(bin_dir, f)
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as fp:
                first_line = fp.readline()
        except OSError:
            continue
        if first_line.startswith(b"#!"):
            if stage_bytes in first_line:
                print(f"Blocked: shebang in {p} refers to staging path", file=sys.stderr)
                sys.exit(2)

# Atomic publication: point of no return / commit linearization point
old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT, signal.SIGTERM])
try:
    rename_noreplace(stage_dir, final_dir)
    try:
        os.write(3, b"COMMITTED\n")
    except OSError:
        pass
except OSError as e:
    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    if e.errno == errno.EEXIST:
        sys.exit(10)
    sys.exit(3)
except Exception:
    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    sys.exit(3)

signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
sys.exit(0)
' "$PRIVATE_STAGE" "$VENV_TARGET" || PUBLISH_STATUS=$?

if [ "$PUBLISH_STATUS" -eq 10 ]; then
    if [ "$VENV_DIR" != "$VENV_TARGET" ]; then
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path (requested: '$VENV_DIR', canonical target: '$VENV_TARGET')." >&2
    else
        echo "Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: $VENV_TARGET" >&2
    fi
    exit 2
elif [ "$PUBLISH_STATUS" -ne 0 ]; then
    exit "$PUBLISH_STATUS"
fi

has_commit_witness
COMMITTED=1
PRIVATE_STAGE=""
trap - EXIT INT TERM
close_commit_witness

echo "AgentReview toolrepo venv ready at: $VENV_TARGET"


echo "Installed strictly from: $LOCK_FILE (--require-hashes --no-deps)"
if [ "$TOOLREPO_SHA_PROVIDED" = "1" ]; then
    echo "Toolrepo pinned at full SHA: $TOOLREPO_SHA"
fi
