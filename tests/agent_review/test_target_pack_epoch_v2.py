"""REDs for #203's private external-K epoch carrier.

These tests intentionally name the runtime coordination property rather than
the eventual implementation details.  The carrier is outside the target
tree; target mutation is exercised by the authorized-apply tests.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import app.agent_review.target_pack_epoch_v2 as epoch_module
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_BUSY_REASON_V2,
    TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2,
    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
    TARGET_PACK_EPOCH_PROTOCOL_VERSION_V2,
    TargetPackEpochError,
    TargetPackObservationBindingErrorV2,
    acquire_target_pack_epoch_v2,
    compute_target_pack_epoch_key_from_components_v2,
    runtime_carrier_root_v2,
)


@pytest.fixture
def runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private, sticky test parent; production still hard-codes `/tmp`."""

    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    return parent


def _subprocess_acquire(*, parent: Path, target: Path, exclusive: bool) -> str:
    code = r'''
import os, sys
from pathlib import Path
import app.agent_review.target_pack_epoch_v2 as epoch
epoch._RUNTIME_PARENT_PATH_V2 = Path(sys.argv[1])
epoch._RUNTIME_PARENT_EXPECTED_OWNER_V2 = os.geteuid()
try:
    lease = epoch.acquire_target_pack_epoch_v2(target_root=Path(sys.argv[2]), exclusive=sys.argv[3] == "exclusive")
except epoch.TargetPackEpochError as exc:
    print(exc.reason_code)
else:
    print("acquired")
    lease.release()
'''
    return subprocess.check_output(
        [sys.executable, "-c", code, str(parent), str(target), "exclusive" if exclusive else "shared"], text=True
    ).strip()


def test_k_uses_length_framing_not_ambiguous_text_concatenation() -> None:
    """R1: component boundaries are part of K's semantic preimage."""

    left = compute_target_pack_epoch_key_from_components_v2(
        euid=12,
        mount_namespace_identity=(3, 4),
        canonical_target_subject=b"56",
    )
    right = compute_target_pack_epoch_key_from_components_v2(
        euid=1,
        mount_namespace_identity=(23, 4),
        canonical_target_subject=b"56",
    )

    assert left != right


def test_k_known_answer_vectors_are_domain_separated_and_deterministic() -> None:
    """R1/M_UNFRAMED_K: freeze the public-free, private K preimage exactly."""

    assert compute_target_pack_epoch_key_from_components_v2(
        euid=1000, mount_namespace_identity=(1, 2), canonical_target_subject=b"/srv/target"
    ) == "ffc5e19e522e4b7c7c0fdcba29048895f9aa001c6a4d19e78fed950c0fa5db6c"
    assert compute_target_pack_epoch_key_from_components_v2(
        euid=42, mount_namespace_identity=(123, 456), canonical_target_subject=b"/tmp/a/../target"
    ) == "4d3ba85e7ac85e6625bea4d0aacfe66ae75333c86d3a6f48ad696e9715cda09d"


def test_supported_path_spellings_and_stable_symlink_aliases_converge_on_one_k(runtime_parent: Path, tmp_path: Path) -> None:
    """R2/R5: K uses the canonical locator, not the caller spelling."""

    target = tmp_path / "real" / "target"
    target.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    spellings = (target, target / ".", target.parent / "target", alias)
    keys: set[str] = set()
    for spelling in spellings:
        with acquire_target_pack_epoch_v2(target_root=spelling, exclusive=False) as lease:
            keys.add(lease.key)
    assert len(keys) == 1


def test_runtime_carrier_root_is_only_protocol_version_and_effective_uid(monkeypatch) -> None:
    """R19/M_DYNAMIC_FALLBACK: session state never selects another root."""

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    without_xdg = runtime_carrier_root_v2(euid=1001)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1001")
    with_xdg = runtime_carrier_root_v2(euid=1001)

    assert TARGET_PACK_EPOCH_PROTOCOL_VERSION_V2 == "agentreview.target-epoch-k.v1"
    assert without_xdg == with_xdg == Path("/tmp/agentreview-target-locks-v1-1001")


def test_same_path_has_one_k_even_when_target_repository_identity_differs(runtime_parent: Path, tmp_path: Path) -> None:
    """R3: repository is not a K component; the local subject is the path."""

    target = tmp_path / "target"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as first:
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=False) == TARGET_PACK_EPOCH_BUSY_REASON_V2
        assert len(first.key) == 64


def test_distinct_target_roots_have_independent_k(runtime_parent: Path, tmp_path: Path) -> None:
    """R4/R10: unrelated targets never serialize on the namespace lease."""

    left = tmp_path / "left"
    right = tmp_path / "right"
    with acquire_target_pack_epoch_v2(target_root=left, exclusive=True) as left_lease:
        with acquire_target_pack_epoch_v2(target_root=right, exclusive=True) as right_lease:
            assert left_lease.key != right_lease.key


def test_namespace_and_k_sh_ex_semantics(runtime_parent: Path, tmp_path: Path) -> None:
    """R6--R9: namespace SH coexists, while one K has normal SH/EX rules."""

    target = tmp_path / "target"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=False):
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=False) == "acquired"
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == TARGET_PACK_EPOCH_BUSY_REASON_V2
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True):
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=False) == TARGET_PACK_EPOCH_BUSY_REASON_V2
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == TARGET_PACK_EPOCH_BUSY_REASON_V2


def test_protocol_directory_wrong_mode_is_unavailable(runtime_parent: Path, tmp_path: Path) -> None:
    name = f"agentreview-target-locks-v1-{os.geteuid()}"
    directory = runtime_parent / name
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)

    with pytest.raises(TargetPackEpochError) as exc_info:
        acquire_target_pack_epoch_v2(target_root=tmp_path / "target", exclusive=False)
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


def test_protocol_directory_wrong_owner_is_unavailable(runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    name = "agentreview-target-locks-v1-4242"
    directory = runtime_parent / name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    monkeypatch.setattr(epoch_module.os, "geteuid", lambda: 4242)

    with pytest.raises(TargetPackEpochError) as exc_info:
        acquire_target_pack_epoch_v2(target_root=tmp_path / "target", exclusive=False)
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


def test_protocol_directory_symlink_is_unavailable(runtime_parent: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (runtime_parent / f"agentreview-target-locks-v1-{os.geteuid()}").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TargetPackEpochError) as exc_info:
        acquire_target_pack_epoch_v2(target_root=tmp_path / "target", exclusive=False)
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


@pytest.mark.parametrize("unsafe_mode", [0o777, 0o700])
def test_unsafe_runtime_parent_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_mode: int) -> None:
    """R18: production never downgrades to a non-sticky/private substitute."""

    parent = tmp_path / "unsafe"
    parent.mkdir()
    parent.chmod(unsafe_mode)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())

    with pytest.raises(TargetPackEpochError) as exc_info:
        acquire_target_pack_epoch_v2(target_root=tmp_path / "target", exclusive=False)
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


def test_carrier_symlink_and_hardlink_are_unavailable(runtime_parent: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    canonical = os.fsencode(target.resolve(strict=False))
    key = compute_target_pack_epoch_key_from_components_v2(
        euid=os.geteuid(),
        mount_namespace_identity=epoch_module._mount_namespace_identity_v2(),
        canonical_target_subject=canonical,
    )
    namespace = runtime_parent / f"agentreview-target-locks-v1-{os.geteuid()}"
    namespace.mkdir(mode=0o700)
    namespace.chmod(0o700)
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"")
    (namespace / f"{key}.lock").symlink_to(outside)
    with pytest.raises(TargetPackEpochError) as symlink_error:
        acquire_target_pack_epoch_v2(target_root=target, exclusive=False)
    assert symlink_error.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2

    (namespace / f"{key}.lock").unlink()
    carrier = namespace / f"{key}.lock"
    carrier.write_bytes(b"")
    carrier.chmod(0o600)
    os.link(carrier, namespace / "second-name.lock")
    with pytest.raises(TargetPackEpochError) as hardlink_error:
        acquire_target_pack_epoch_v2(target_root=target, exclusive=False)
    assert hardlink_error.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


def test_crash_releases_carrier_and_descriptor_is_non_inheritable(runtime_parent: Path, tmp_path: Path) -> None:
    """R21/R22: kernel lifetime, not a stale marker, controls the epoch."""

    target = tmp_path / "target"
    code = r'''
import os, sys, time
from pathlib import Path
import app.agent_review.target_pack_epoch_v2 as epoch
epoch._RUNTIME_PARENT_PATH_V2 = Path(sys.argv[1])
epoch._RUNTIME_PARENT_EXPECTED_OWNER_V2 = os.geteuid()
lease = epoch.acquire_target_pack_epoch_v2(target_root=Path(sys.argv[2]), exclusive=True)
print(f"{lease._namespace_fd}:{lease._carrier_fd}", flush=True)
time.sleep(60)
'''
    holder = subprocess.Popen([sys.executable, "-c", code, str(runtime_parent), str(target)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None
    fd_line = holder.stdout.readline().strip()
    assert len(fd_line.split(":")) == 2
    assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == TARGET_PACK_EPOCH_BUSY_REASON_V2
    holder.kill()
    holder.wait(timeout=5)
    assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == "acquired"

    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        assert not os.get_inheritable(lease._namespace_fd)
        assert not os.get_inheritable(lease._carrier_fd)


def test_exec_child_cannot_prolong_the_parent_epoch(runtime_parent: Path, tmp_path: Path) -> None:
    """R22: close-on-exec/non-inheritable descriptors end with the holder."""

    target = tmp_path / "target"
    code = r'''
import os, subprocess, sys
from pathlib import Path
import app.agent_review.target_pack_epoch_v2 as epoch
epoch._RUNTIME_PARENT_PATH_V2 = Path(sys.argv[1])
epoch._RUNTIME_PARENT_EXPECTED_OWNER_V2 = os.geteuid()
lease = epoch.acquire_target_pack_epoch_v2(target_root=Path(sys.argv[2]), exclusive=True)
child = subprocess.Popen(["sleep", "2"])
lease.release()
print(child.pid, flush=True)
'''
    holder = subprocess.Popen([sys.executable, "-c", code, str(runtime_parent), str(target)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None
    child_pid = int(holder.stdout.readline().strip())
    holder.wait(timeout=5)
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pytest.skip("the platform reaped the short-lived child before the inherited-FD discriminator")
    assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == "acquired"


def test_fork_without_exec_cannot_retain_the_parent_epoch(runtime_parent: Path, tmp_path: Path) -> None:
    """Review RED: close child FD copies without LOCK_UN on the shared OFD."""

    target = tmp_path / "target"
    code = r'''
import os, sys, time
from pathlib import Path
import app.agent_review.target_pack_epoch_v2 as epoch
epoch._RUNTIME_PARENT_PATH_V2 = Path(sys.argv[1])
epoch._RUNTIME_PARENT_EXPECTED_OWNER_V2 = os.geteuid()
lease = epoch.acquire_target_pack_epoch_v2(target_root=Path(sys.argv[2]), exclusive=True)
pid = os.fork()
if pid == 0:
    time.sleep(2)
    os._exit(0)
os.write(1, b"forked\n")
os._exit(0)
'''
    holder = subprocess.Popen([sys.executable, "-c", code, str(runtime_parent), str(target)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "forked"
    holder.wait(timeout=5)
    assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == "acquired"


def test_release_never_unlinks_the_inert_carrier_or_protocol_directory(runtime_parent: Path, tmp_path: Path) -> None:
    """R20/M_UNLINK_K: lifecycle is kernel-lock lifetime, not unlinking."""

    target = tmp_path / "target"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        carrier = runtime_carrier_root_v2() / f"{lease.key}.lock"
        namespace = runtime_carrier_root_v2()
        assert carrier.is_file()
        assert namespace.is_dir()
    assert carrier.is_file()
    assert namespace.is_dir()


@pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("systemd-tmpfiles") is None,
    reason="qualified generic cleanup probe needs a local privileged systemd-tmpfiles environment",
)
def test_generic_ancestor_cleanup_respects_active_namespace_then_recreates_inactive_one(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """R11/R12: qualified generic cleanup sees the held namespace SH lease.

    The configuration deliberately names only the generic test ancestor, not
    the protocol namespace.  Direct privileged deletion of the application
    namespace is outside the supported cooperative threat domain.
    """

    config = tmp_path / "tmpfiles.conf"
    config.write_text(f"d {runtime_parent} 1777 - - 1s\n", encoding="utf-8")
    target = tmp_path / "target"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        namespace = runtime_carrier_root_v2()
        carrier = namespace / f"{lease.key}.lock"
        old = time.time() - 10
        os.utime(namespace, (old, old))
        os.utime(carrier, (old, old))
        # tmpfiles considers ctime as well as the timestamps we can set;
        # let the 1s policy age past creation before asking generic cleanup.
        time.sleep(1.1)
        completed = subprocess.run(["systemd-tmpfiles", "--clean", str(config)], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        assert namespace.is_dir()
        assert carrier.is_file()
        assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == TARGET_PACK_EPOCH_BUSY_REASON_V2

    old = time.time() - 10
    os.utime(namespace, (old, old))
    os.utime(carrier, (old, old))
    completed = subprocess.run(["systemd-tmpfiles", "--clean", str(config)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    # tmpfiles versions differ on whether an aged empty child directory is
    # pruned by this generic ``d`` rule.  Either result is safe once no lease
    # is active; model an allowed inactive cleanup removal explicitly and
    # prove the next acquisition recreates and revalidates it.
    if namespace.exists():
        shutil.rmtree(namespace)
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True):
        assert runtime_carrier_root_v2().is_dir()


def test_exception_releases_both_leases_and_released_capability_is_rejected(runtime_parent: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    lease = acquire_target_pack_epoch_v2(target_root=target, exclusive=True)
    try:
        raise RuntimeError("injected")
    except RuntimeError:
        lease.release()

    assert _subprocess_acquire(parent=runtime_parent, target=target, exclusive=True) == "acquired"
    with pytest.raises(TargetPackEpochError) as exc_info:
        lease.require_exclusive_v2(target_root=target)
    assert exc_info.value.reason_code == TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2


def test_shared_or_wrong_subject_capability_is_not_an_exclusive_writer_token(runtime_parent: Path, tmp_path: Path) -> None:
    """R33/R34/M_CAPABILITY_MARKER: capability validity includes mode and K."""

    target = tmp_path / "target"
    other = tmp_path / "other"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=False) as shared:
        with pytest.raises(TargetPackEpochError) as shared_error:
            shared.require_exclusive_v2(target_root=target)
        assert shared_error.value.reason_code == TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as exclusive:
        with pytest.raises(TargetPackEpochError) as subject_error:
            exclusive.require_exclusive_v2(target_root=other)
        assert subject_error.value.reason_code == TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2


def test_materialization_and_o_path_binding_preserve_missing_ancestors_and_mode_0300(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """R25/R27/R36: prefix creation is post-gate and binding needs no O_RDONLY."""

    missing = tmp_path / "a" / "b" / "target"
    with acquire_target_pack_epoch_v2(target_root=missing, exclusive=True) as lease:
        key_before_materialization = lease.key
        with lease.materialize_and_bind_target_root_v2(target_root=missing) as binding:
            assert missing.is_dir()
            assert stat.S_ISDIR(os.fstat(binding.fd).st_mode)
    with acquire_target_pack_epoch_v2(target_root=missing, exclusive=True) as lease:
        assert lease.key == key_before_materialization

    existing = tmp_path / "mode-0300"
    existing.mkdir()
    existing.chmod(0o300)
    try:
        with acquire_target_pack_epoch_v2(target_root=existing, exclusive=True) as lease:
            with lease.bind_target_root_v2(target_root=existing) as binding:
                assert stat.S_ISDIR(os.fstat(binding.fd).st_mode)
    finally:
        existing.chmod(0o700)


def test_additive_observation_binding_preserves_writer_binding_failures(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F16/M_BIND_EXTENSION_BREAKS_WRITER: old writer API is byte-semantic stable."""

    missing = tmp_path / "missing"
    with acquire_target_pack_epoch_v2(target_root=missing, exclusive=True) as lease:
        with pytest.raises(TargetPackEpochError) as missing_error:
            lease.bind_target_root_v2(target_root=missing)
        assert type(missing_error.value) is TargetPackEpochError
        assert missing_error.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2

    target = tmp_path / "existing"
    target.mkdir()
    target_real = os.fspath(target.resolve())
    real_open = epoch_module.os.open

    def emfile_on_target(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if os.fspath(path) == target_real and flags & os.O_PATH:
            raise OSError(errno.EMFILE, "injected")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(epoch_module.os, "open", emfile_on_target)
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        with pytest.raises(TargetPackEpochError) as writer_error:
            lease.bind_target_root_v2(target_root=target)
        assert type(writer_error.value) is TargetPackEpochError
        assert writer_error.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2

        with pytest.raises(TargetPackObservationBindingErrorV2) as observation_error:
            lease.bind_target_root_for_observation_v2(target_root=target)
        assert observation_error.value.operation_errno == errno.EMFILE
        assert observation_error.value.stage == "open"


def test_binding_keeps_the_original_directory_object_after_path_replacement(runtime_parent: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        binding = lease.bind_target_root_v2(target_root=target)
        original = os.fstat(binding.fd)
        moved = tmp_path / "target-original"
        target.rename(moved)
        target.mkdir()
        assert (os.fstat(binding.fd).st_dev, os.fstat(binding.fd).st_ino) == (original.st_dev, original.st_ino)
        assert (os.stat(moved).st_dev, os.stat(moved).st_ino) == (original.st_dev, original.st_ino)
        binding.close()
