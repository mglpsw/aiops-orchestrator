"""Focal AgentReview bootstrap transaction authority (stdlib, CPython 3.11).

Bash exec transfers the installer PID here. Only workers/owned children receive
internal escalation. Publication and its outcome belong to this same process.
"""
import ctypes
import errno
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time

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


PREPARATION_CODE = r"""
import os, sys, shutil
stage_dir, final_dir = sys.argv[1:]
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

# DiscardAtBoundary:
# Remove activation scripts (bin/activate*) and pip console scripts (bin/pip*)
bin_dir = os.path.join(stage_dir, "bin")
if os.path.isdir(bin_dir):
    for f in list(os.listdir(bin_dir)):
        p = os.path.join(bin_dir, f)
        if os.path.isfile(p) and not os.path.islink(p):
            f_lower = f.lower()
            if f_lower.startswith("activate") or f_lower.endswith(".ps1") or f_lower.startswith("pip"):
                try:
                    os.unlink(p)
                except OSError:
                    pass

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
                print(f"Blocked: opaque/binary file contains un-normalizable staging path: {ascii(p)}", file=sys.stderr)
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
                    print(f"Blocked: symlink {ascii(p)} targets staging path {ascii(target_link)}", file=sys.stderr)
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
            print(f"Blocked: staging path reference remains in {ascii(p)}", file=sys.stderr)
            sys.exit(2)

# R4: Executable/shebang census & kernel boundary validation (max 127 bytes)
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
                print(f"Blocked: shebang in {ascii(p)} refers to staging path", file=sys.stderr)
                sys.exit(2)
            shebang_len = len(first_line.rstrip(b"\r\n"))
            if shebang_len > 127:
                print(f"Blocked: shebang in {ascii(p)} exceeds AgentReviewShebangPolicyV1 limit ({shebang_len} bytes > 127 bytes)", file=sys.stderr)
                sys.exit(2)

"""

cancel_signal = 0


def request_cancel(sig, _frame):
    global cancel_signal
    if not cancel_signal:
        cancel_signal = sig


# Install handlers before qualifying the subreaper or admitting workers.
signal.signal(signal.SIGINT, request_cancel)
signal.signal(signal.SIGTERM, request_cancel)


def qualify_subreaper():
    PR_SET_CHILD_SUBREAPER = 36
    PR_GET_CHILD_SUBREAPER = 37

    libc = ctypes.CDLL(None, use_errno=True)

    # Ablation hooks for qualification testing
    ablate_set = os.environ.get("_AIOPS_TEST_ABLATE_SUBREAPER_SET") == "1"
    ablate_get = os.environ.get("_AIOPS_TEST_ABLATE_SUBREAPER_GET") == "1"
    ablate_val = os.environ.get("_AIOPS_TEST_ABLATE_SUBREAPER_VAL") == "0"

    if ablate_set:
        rc_set = -1
        err_set = 1
    else:
        rc_set = libc.prctl(
            ctypes.c_int(PR_SET_CHILD_SUBREAPER),
            ctypes.c_ulong(1),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        err_set = ctypes.get_errno()

    if rc_set != 0:
        sys.stderr.write(f"Blocked: STOP_UNQUALIFIED_SUBREAPER: PR_SET_CHILD_SUBREAPER failed (rc={rc_set}, errno={err_set})\n")
        sys.exit(2)

    val_get = ctypes.c_int(0)
    if ablate_get:
        rc_get = -1
        err_get = 1
    else:
        rc_get = libc.prctl(
            ctypes.c_int(PR_GET_CHILD_SUBREAPER),
            ctypes.byref(val_get),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        err_get = ctypes.get_errno()

    if rc_get != 0:
        sys.stderr.write(f"Blocked: STOP_UNQUALIFIED_SUBREAPER: PR_GET_CHILD_SUBREAPER failed (rc={rc_get}, errno={err_get})\n")
        sys.exit(2)

    if ablate_val:
        val_get.value = 0

    if val_get.value != 1:
        sys.stderr.write(f"Blocked: STOP_UNQUALIFIED_SUBREAPER: PR_GET_CHILD_SUBREAPER returned {val_get.value} (expected 1)\n")
        sys.exit(2)



def owned_children():
    # This single-thread authority owns the Linux subreaper child list. Failure
    # to observe it is a teardown failure, not an empty-tree observation.
    with open(f"/proc/self/task/{os.getpid()}/children") as stream:
        return [int(pid) for pid in stream.read().split()]


def signal_owned(sig):
    for pid in owned_children():
        # Direct children have not been reaped, so their identities cannot be
        # reused between this observation and signalling. Never signal self.
        if pid == os.getpid():
            raise RuntimeError("authority included in child recipient set")
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def drain_children(force=False):
    # Expiry permits escalation, never completion. Complete means the kernel
    # has no remaining waitable children after admission has closed.
    if force or cancel_signal:
        signal_owned(signal.SIGTERM)
    deadline = time.monotonic() + (1.0 if force or cancel_signal else 2.0)
    while True:
        if time.monotonic() >= deadline:
            signal_owned(signal.SIGKILL)
            try:
                os.waitpid(-1, 0)
            except ChildProcessError:
                return
        else:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                time.sleep(0.02)


def run_phase(argv, *, cleanup=False):
    if cancel_signal and not cleanup:
        return 128 + cancel_signal
    proc = subprocess.Popen(argv, start_new_session=True)
    # Cleanup is admitted even after cancellation, with a short opportunity to
    # finish its private removal. It remains an owned, supervised worker.
    deadline = time.monotonic() + 1.0 if cleanup and cancel_signal else None
    term_sent = False
    while proc.poll() is None:
        if cancel_signal:
            if not term_sent and (not cleanup or deadline is None or time.monotonic() >= deadline):
                signal_owned(signal.SIGTERM)
                term_sent = True
                deadline = time.monotonic() + 1.0
            elif term_sent and time.monotonic() >= deadline:
                signal_owned(signal.SIGKILL)
        time.sleep(0.02)
    # Popen owns the direct worker status; generic reaping starts only after
    # that actual status has been recorded, never manufacturing a phase PASS.
    rc = proc.returncode
    drain_children(force=bool(rc or cancel_signal))
    if cancel_signal and not cleanup:
        return 128 + cancel_signal
    return 128 - rc if rc < 0 else rc


def main():
    bootstrap, raw_target, final_dir, lock_file, pin = sys.argv[1:]
    qualify_subreaper()
    publication = "NOT_PUBLISHED"
    publication_reason = "not admitted"
    teardown = "COMPLETE"
    teardown_reason = "no workers admitted"
    primary = 2
    stage_dir = None
    try:
        if cancel_signal:
            primary = 128 + cancel_signal
            publication_reason = "cancellation before worker admission"
        else:
            stage_dir = tempfile.mkdtemp(prefix=".agent_review_stage.", dir=os.path.dirname(final_dir))
            os.chmod(stage_dir, 0o755)
            phases = (
                [bootstrap, "-I", "-S", "-m", "venv", stage_dir],
                ["env", "PIP_CONFIG_FILE=/dev/null", os.path.join(stage_dir, "bin/python3"),
                 "-I", "-m", "pip", "--isolated", "install", "--require-hashes", "--no-deps", "-r", lock_file],
                [bootstrap, "-I", "-S", "-c", PREPARATION_CODE, stage_dir, final_dir],
            )
            primary = 0
            for argv in phases:
                primary = run_phase(argv)
                if primary:
                    publication_reason = "preparation failed or cancelled"
                    break
            if not primary and not cancel_signal:
                # No cancellable publisher: the authority executes the actual
                # syscall and records its result before unmasking pending signals.
                old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT, signal.SIGTERM])
                try:
                    # Recheck requests recorded before admission after masking:
                    # cancellation crossing the admitted syscall is handled only
                    # once that same authority has established its actual result.
                    if cancel_signal:
                        primary = 128 + cancel_signal
                        publication_reason = "cancellation before publication admission"
                    else:
                        publication = "UNKNOWN"
                        publication_reason = "NO_REPLACE result not established"
                        rename_noreplace(stage_dir, final_dir)
                        publication = "COMMITTED"
                        publication_reason = "NO_REPLACE succeeded"
                except OSError as error:
                    publication = "NOT_PUBLISHED"
                    publication_reason = f"NO_REPLACE failed errno={error.errno}"
                    primary = 2 if error.errno == errno.EEXIST else 3
                    if error.errno == errno.EEXIST:
                        print("Blocked: AgentReview toolrepo venv target must be absent; refusing to reuse or mutate an existing path: " + ascii(final_dir), file=sys.stderr)
                    else:
                        print("Blocked: publication failed for " + ascii(final_dir) + "; " + publication_reason, file=sys.stderr)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
            elif not primary:
                primary = 128 + cancel_signal
                publication_reason = "cancellation before publication admission"
    except Exception as error:
        if not primary:
            primary = 3
        print("Blocked: installation failure: " + ascii(str(error)), file=sys.stderr)
    finally:
        try:
            drain_children(force=True)
            teardown_reason = "all admitted children reaped"
        except Exception as error:
            teardown = "FAILED"
            teardown_reason = ascii(str(error))
        # Never inspect/remove FINAL or clean an unknown publication outcome.
        if stage_dir and publication == "NOT_PUBLISHED":
            try:
                cleanup_status = run_phase(["rm", "-rf", "--", stage_dir], cleanup=True)
                if cleanup_status:
                    raise RuntimeError(f"cleanup status {cleanup_status}")
            except Exception as error:
                teardown = "FAILED"
                teardown_reason = ascii(str(error))
                print("Warning: AgentReview toolrepo cleanup failed for " + ascii(stage_dir) + "; " + teardown_reason + f"; preserving original install failure {primary}.", file=sys.stderr)

    if publication == "UNKNOWN":
        status = 5
    elif publication == "COMMITTED":
        status = 0 if teardown == "COMPLETE" else 4
    else:
        status = primary or (4 if teardown == "FAILED" else 3)
    print(f"AgentReview transaction: publication={publication} ({publication_reason}); teardown={teardown} ({teardown_reason}); exit_status={status}; automatic_retry=false.", file=sys.stderr)
    if publication == "COMMITTED" and teardown == "COMPLETE":
        if cancel_signal:
            print("Signal observed after transaction commit; installation already committed.", file=sys.stderr)
        print("AgentReview toolrepo venv ready at: " + ascii(final_dir))
        print("Installed strictly from: " + ascii(lock_file) + " (--require-hashes --no-deps)")
        if pin:
            print("Toolrepo pinned at full SHA: " + pin)
    elif publication == "COMMITTED":
        print("Operational failure after COMMITTED; final target preserved at " + ascii(final_dir) + "; do not automatically retry.", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
