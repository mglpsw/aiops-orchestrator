"""REDs for `#262` — the K runtime carrier must be disjoint from the target.

The property under test is

    RuntimeCarrierMutationDomain(K) ∩ TargetMutationDomain(D) = ∅

for every ACCEPTED target topology, with an unestablishable topology refused
rather than assumed disjoint.

These tests name the property, not the mechanism.  Every case asks one of two
questions: did acquisition materialize the pack's own carrier somewhere beneath
the directory it was handed as `target_root`, or did it refuse.  A case that
refuses for the wrong reason is not passing by accident: the two refusal reasons
are asserted apart, because "established to overlap" and "could not be
established" must never be read as each other.

`PR #259` demonstrated that `Path.resolve()` plus `st_dev`/`st_ino` cannot decide
this: a bind alias shares both with its source, and a bind mounted deeper inside
the target is invisible to both.  The bind-mount cases below are the ones that
falsified that strategy class, so they use real mounts and are skipped — never
silently passed — where the environment cannot provide one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import app.agent_review.target_pack_epoch_v2 as epoch_module
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2,
    TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2,
    TargetPackEpochError,
    acquire_target_pack_epoch_v2,
)


@pytest.fixture
def runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    return parent


def _can_bind_mount() -> bool:
    if os.geteuid() != 0:
        return False
    probe = Path("/tmp") / f"agentreview-bindprobe-{os.getpid()}"
    source = Path("/tmp") / f"agentreview-bindsrc-{os.getpid()}"
    probe.mkdir(exist_ok=True)
    source.mkdir(exist_ok=True)
    try:
        if subprocess.run(["mount", "--bind", str(source), str(probe)]).returncode != 0:
            return False
        subprocess.run(["umount", str(probe)], check=False)
        return True
    finally:
        probe.rmdir()
        source.rmdir()


requires_bind_mount = pytest.mark.skipif(
    not _can_bind_mount(),
    reason="BLOCKED_BY_ENVIRONMENT: this case needs a real bind mount; a skipped "
    "mount case is not evidence that the alias is refused",
)


def _carrier_material_beneath(target: Path) -> list[Path]:
    """Every carrier artifact reachable beneath *target*, by name."""

    if not target.exists():
        return []
    found: list[Path] = []
    for root, directories, files in os.walk(target, followlinks=False):
        for name in list(directories) + list(files):
            if name.startswith("agentreview-target-locks-v1-"):
                found.append(Path(root) / name)
    return found


def _acquire(target: Path) -> str:
    """`"acquired"` or the typed refusal reason -- never an exception."""

    try:
        lease = acquire_target_pack_epoch_v2(target_root=target, exclusive=True)
    except TargetPackEpochError as exc:
        return exc.reason_code
    lease.release()
    return "acquired"


# -- R9 / R12: the accepted topologies must keep working ---------------------


def test_r9_ordinary_unrelated_target_still_acquires(runtime_parent: Path, tmp_path: Path) -> None:
    """POSITIVE CONTROL.  A refusal-only corpus cannot detect over-rejection."""

    target = tmp_path / "ordinary-target"
    target.mkdir()

    assert _acquire(target) == "acquired"
    assert _carrier_material_beneath(target) == []


def test_r12_two_independent_targets_derive_distinct_k(runtime_parent: Path, tmp_path: Path) -> None:
    first = tmp_path / "target-a"
    second = tmp_path / "target-b"
    first.mkdir()
    second.mkdir()

    with acquire_target_pack_epoch_v2(target_root=first, exclusive=True) as left:
        with acquire_target_pack_epoch_v2(target_root=second, exclusive=True) as right:
            assert left.key != right.key


# -- R1/R2/R3: reachable by path --------------------------------------------


def _carrier_root(runtime_parent: Path) -> Path:
    """The carrier root proper -- the protocol directory, not its parent."""

    return runtime_parent / f"agentreview-target-locks-v1-{os.geteuid()}"


def test_r1_target_equal_to_the_carrier_root_is_refused(runtime_parent: Path) -> None:
    """`D1`: acquisition must not materialize the very directory it was asked to
    diagnose, then report on what it created itself.  The carrier is absent at
    entry, which is exactly the condition under which `D1` reproduced."""

    carrier_root = _carrier_root(runtime_parent)
    assert not carrier_root.exists()

    assert _acquire(carrier_root) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not carrier_root.exists()


@pytest.mark.parametrize("depth", [0, 1], ids=["immediate-parent", "higher-ancestor"])
def test_r2_target_containing_the_carrier_is_refused(
    runtime_parent: Path, tmp_path: Path, depth: int
) -> None:
    """The target is an ancestor of the carrier, so a carrier write lands inside
    the target subtree."""

    target = runtime_parent if depth == 0 else tmp_path

    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert _carrier_material_beneath(target) == []


def test_r3_target_beneath_the_carrier_is_refused(runtime_parent: Path) -> None:
    nested = _carrier_root(runtime_parent) / "nested-target"
    nested.mkdir(parents=True)

    assert _acquire(nested) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2


def test_a_sibling_of_the_carrier_root_is_accepted(runtime_parent: Path) -> None:
    """OVER-REJECTION CONTROL.  A target beside the carrier root, under the same
    runtime parent, is genuinely disjoint and must still be accepted -- the
    mutation domain is the carrier root, never the whole runtime parent."""

    sibling = runtime_parent / "sibling-target"
    sibling.mkdir()

    assert _acquire(sibling) == "acquired"
    assert _carrier_material_beneath(sibling) == []


# -- R4/R5/R6: reachable through a bind alias -------------------------------


@requires_bind_mount
def test_r4_bind_alias_of_the_carrier_parent_is_refused(runtime_parent: Path, tmp_path: Path) -> None:
    """`E1`: the alias and its source share `st_dev` AND `st_ino`, and the alias
    path passes every textual check."""

    alias = tmp_path / "alias-of-parent"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(alias)], check=True)
    try:
        assert _acquire(alias) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert _carrier_material_beneath(alias) == []
    finally:
        subprocess.run(["umount", str(alias)], check=False)


@requires_bind_mount
def test_r5_carrier_parent_bound_directly_below_the_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target-with-mount"
    inner = target / "vendor"
    inner.mkdir(parents=True)
    subprocess.run(["mount", "--bind", str(runtime_parent), str(inner)], check=True)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert _carrier_material_beneath(target) == []
    finally:
        subprocess.run(["umount", str(inner)], check=False)


@requires_bind_mount
def test_r6_deep_bind_alias_inside_the_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """The case `PR #259` explicitly declared it could NOT detect: the alias sits
    several levels inside an otherwise unrelated target."""

    target = tmp_path / "deep-target"
    deep = target / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    subprocess.run(["mount", "--bind", str(runtime_parent), str(deep)], check=True)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert _carrier_material_beneath(target) == []
    finally:
        subprocess.run(["umount", str(deep)], check=False)


# -- R7/R8: unobservable topology must be UNKNOWN, never disjoint -----------


@pytest.mark.parametrize("errno_code", [5, 13], ids=["EIO", "EACCES"])
def test_r7_r8_unreadable_mount_table_refuses_rather_than_assuming_disjoint(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, errno_code: int
) -> None:
    """`G2` structurally: the probe backing the decision must not fail OPEN.

    Asserted against the disjointness authority itself, not only end to end --
    `_open_runtime_parent_v2` independently refuses when the mount table is
    unreadable, so an end-to-end assertion alone would pass even if this
    function fell open.
    """

    def _raise(_path: Path, *args: object, **kwargs: object) -> str:
        raise OSError(errno_code, os.strerror(errno_code))

    monkeypatch.setattr(Path, "read_text", _raise)

    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._establish_carrier_disjoint_v2(
            canonical_target_subject=os.fsencode(str(tmp_path / "unrelated")),
            parent_fd=os.open(runtime_parent, os.O_RDONLY | os.O_DIRECTORY),
            euid=os.geteuid(),
        )
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


@pytest.mark.parametrize("errno_code", [5, 13], ids=["EIO", "EACCES"])
def test_unobservable_target_refuses_rather_than_assuming_disjoint(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, errno_code: int
) -> None:
    """The OTHER observation this decision rests on.

    Reading the mount table is not the only thing that can fail: the target's own
    identity must be observed to rule out a bind alias of the carrier.  If that
    `lstat` fails, the alias cannot be ruled out, so disjointness is UNKNOWN.
    Treating the failure as "not an alias" is the same fail-open as `G2`, one
    call site over.
    """

    target = tmp_path / "unobservable"
    target.mkdir()
    real_lstat = os.lstat

    def _raise(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if os.fspath(path) == str(target):
            raise OSError(errno_code, os.strerror(errno_code))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(epoch_module.os, "lstat", _raise)

    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_unparseable_mount_table_line_is_unknown_not_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A line this module cannot parse is a mount point it cannot rule out.

    The bad line is deliberately mixed WITH valid ones.  A table of nothing but
    garbage is caught by the empty-table guard whatever the parser does, so an
    all-garbage input cannot discriminate a parser that silently skips what it
    does not understand -- it would leave a partial table reported as complete.
    """

    table = (
        "36 35 98:0 / / rw,relatime - ext4 /dev/root rw\n"
        "37 36 0:22 / /tmp rw,nosuid - tmpfs tmpfs rw\n"
        "this line is not mountinfo\n"
    )
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: table)

    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._read_mount_table_v2()
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_well_formed_mount_table_is_parsed_completely(monkeypatch: pytest.MonkeyPatch) -> None:
    """OVER-REJECTION CONTROL for the parser: valid tables must not be refused,
    and every entry must survive -- a parser that dropped entries would under-
    refuse, which is the direction that matters."""

    table = (
        "36 35 98:0 / / rw,relatime - ext4 /dev/root rw\n"
        "37 36 0:22 / /tmp rw,nosuid - tmpfs tmpfs rw\n"
        "38 36 0:23 / /var/lib/x rw shared:9 - ext4 /dev/sdb rw\n"
    )
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: table)

    entries = epoch_module._read_mount_table_v2()

    assert [point for _device, point in entries] == ["/", "/tmp", "/var/lib/x"]
    assert entries[1][0] == os.makedev(0, 22)


def test_empty_mount_table_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: "")

    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._read_mount_table_v2()
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


# -- R13: nothing is written to a target that is about to be refused --------


@requires_bind_mount
def test_r13_refused_target_is_not_mutated_first(runtime_parent: Path, tmp_path: Path) -> None:
    target = tmp_path / "untouched-target"
    inner = target / "sub"
    inner.mkdir(parents=True)
    (target / "marker.txt").write_text("original", encoding="utf-8")
    subprocess.run(["mount", "--bind", str(runtime_parent), str(inner)], check=True)
    try:
        before = sorted(entry.name for entry in target.iterdir())
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert sorted(entry.name for entry in target.iterdir()) == before
        assert (target / "marker.txt").read_text(encoding="utf-8") == "original"
    finally:
        subprocess.run(["umount", str(inner)], check=False)


def test_refusal_precedes_protocol_directory_creation(runtime_parent: Path, tmp_path: Path) -> None:
    """The refusal must come before the carrier is materialized anywhere -- not
    merely before it is materialized inside the target."""

    protocol_directory = runtime_parent / f"agentreview-target-locks-v1-{os.geteuid()}"
    assert not protocol_directory.exists()

    assert _acquire(tmp_path) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not protocol_directory.exists()
