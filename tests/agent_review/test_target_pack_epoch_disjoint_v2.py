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


# -- R15-R20: #262 F1 -- reachable through a distinct ANCESTRAL alias mount --
#
# `PR #267`'s mount rule checked only "some mount M is at/beneath the target"
# -- a carrier-relevant mount NESTED INSIDE the target.  It never checked the
# reverse: the target itself reached only through a distinct same-device mount
# that is an ANCESTOR of the target and does not itself reach the runtime
# parent.  `mount --bind <runtime_parent> <A>` then handing out
# `<A>/<carrier-name>` (or anything beneath it) as `target_root` passed every
# check #267 shipped with -- the carrier materialized beneath the accepted
# target because writing through `A` IS writing through the runtime parent,
# by construction of the bind mount -- and is refused only from this
# correction onward.


def _can_tmpfs_mount() -> bool:
    if os.geteuid() != 0:
        return False
    probe = Path("/tmp") / f"agentreview-tmpfsprobe-{os.getpid()}"
    probe.mkdir(exist_ok=True)
    try:
        if subprocess.run(["mount", "-t", "tmpfs", "tmpfs", str(probe)]).returncode != 0:
            return False
        subprocess.run(["umount", str(probe)], check=False)
        return True
    finally:
        probe.rmdir()


requires_tmpfs_mount = pytest.mark.skipif(
    not _can_tmpfs_mount(),
    reason="BLOCKED_BY_ENVIRONMENT: this case needs a real tmpfs mount; a skipped "
    "mount case is not evidence the different-device target is governed normally",
)


@requires_bind_mount
def test_r15_ancestral_bind_alias_target_equals_carrier_root_carrier_absent(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """`#262` F1-A.  The runtime parent is bind-mounted onto an unrelated
    mountpoint `A`; the target handed to acquisition is `A`'s own view of the
    carrier root.  Neither the carrier nor the target exists yet -- this is
    the FIRST acquisition against this runtime parent, not a repeat."""

    alias = tmp_path / "ancestral-alias"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(alias)], check=True)
    try:
        target = alias / f"agentreview-target-locks-v1-{os.geteuid()}"
        carrier_root = _carrier_root(runtime_parent)
        assert not carrier_root.exists()
        assert not target.exists()

        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert not carrier_root.exists()
        assert not target.exists()
    finally:
        subprocess.run(["umount", str(alias)], check=False)


@requires_bind_mount
def test_r16_ancestral_bind_alias_target_equals_carrier_root_carrier_present(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """`#262` F1-B.  Same topology as R15, except a prior ordinary acquisition
    against this runtime parent has already materialized the carrier before
    the alias is ever observed."""

    ordinary_target = tmp_path / "ordinary-prior-target"
    ordinary_target.mkdir()
    with acquire_target_pack_epoch_v2(target_root=ordinary_target, exclusive=False) as lease:
        assert lease.key

    carrier_root = _carrier_root(runtime_parent)
    assert carrier_root.exists()

    alias = tmp_path / "ancestral-alias-precreated"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(alias)], check=True)
    try:
        target = alias / f"agentreview-target-locks-v1-{os.geteuid()}"
        assert target.exists()  # visible through the alias -- same directory as carrier_root

        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        subprocess.run(["umount", str(alias)], check=False)


@requires_bind_mount
def test_r17_target_below_a_distinct_same_device_alias_mount_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """A same-device alias mount that is NOT itself an alias of the runtime
    parent -- some unrelated directory happens to be bind-mounted onto an
    ancestor of the target.  Conservative by design: no carrier material is
    actually reachable through this specific alias, and the target is refused
    anyway, because the mount does not establish that it reaches back to the
    runtime parent either."""

    unrelated_source = tmp_path / "unrelated-same-device-source"
    unrelated_source.mkdir()
    alias = tmp_path / "distinct-alias-mount"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(unrelated_source), str(alias)], check=True)
    try:
        target = alias / "project"
        target.mkdir()

        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        subprocess.run(["umount", str(alias)], check=False)


def test_r18_ordinary_target_under_the_shared_root_filesystem_is_accepted(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """POSITIVE CONTROL for the F1 correction specifically.  The runtime
    parent and an ordinary target share nothing but the root filesystem's own
    mount -- same device, no alias anywhere.  The ancestral-alias rule must
    not fire merely because a mount (the shared root) happens to be an
    ancestor of the target and shares the runtime parent's device; the root
    mount trivially contains the runtime parent too, and only a mount that
    does NOT is in scope."""

    target = tmp_path / "ordinary-shared-root-target"
    target.mkdir()
    assert os.stat(runtime_parent).st_dev == os.stat(target).st_dev

    assert _acquire(target) == "acquired"
    assert _carrier_material_beneath(target) == []


@requires_bind_mount
def test_r19_bind_of_bind_ancestral_alias_is_refused(runtime_parent: Path, tmp_path: Path) -> None:
    """Transitive aliasing: the runtime parent is bind-mounted onto `A`, and
    `A` is itself bind-mounted onto `A2`.  The target reached through `A2` is
    two hops removed from the runtime parent by name, but the same device
    throughout -- the correction must not be defeated by mount-stacking
    depth."""

    first_hop = tmp_path / "bind-hop-1"
    second_hop = tmp_path / "bind-hop-2"
    first_hop.mkdir()
    second_hop.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(first_hop)], check=True)
    try:
        subprocess.run(["mount", "--bind", str(first_hop), str(second_hop)], check=True)
        try:
            target = second_hop / f"agentreview-target-locks-v1-{os.geteuid()}"
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        finally:
            subprocess.run(["umount", str(second_hop)], check=False)
    finally:
        subprocess.run(["umount", str(first_hop)], check=False)


@requires_tmpfs_mount
def test_r20_target_under_an_unrelated_different_device_mount_is_governed_normally(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """A target reached through a mount on a genuinely DIFFERENT device from
    the runtime parent's is outside every device-gated rule; it is governed
    normally (accepted here, since it is otherwise ordinary), not swept into
    refusal just for living beneath *some* mount."""

    different_device_mount = tmp_path / "different-device-mount"
    different_device_mount.mkdir()
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", str(different_device_mount)], check=True)
    try:
        target = different_device_mount / "project"
        target.mkdir()
        assert os.stat(runtime_parent).st_dev != os.stat(target).st_dev

        assert _acquire(target) == "acquired"
        assert _carrier_material_beneath(target) == []
    finally:
        subprocess.run(["umount", str(different_device_mount)], check=False)


# -- attack-15 topologies: previously exercised informally, now committed ----


def test_relative_target_root_is_resolved_before_the_carrier_root_check(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carrier_root = _carrier_root(runtime_parent)
    assert not carrier_root.exists()

    monkeypatch.chdir(runtime_parent)
    relative = Path(f"./agentreview-target-locks-v1-{os.geteuid()}")
    assert not relative.is_absolute()

    assert _acquire(relative) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not carrier_root.exists()


def test_target_root_containing_dotdot_is_resolved_before_the_check(
    runtime_parent: Path, tmp_path: Path
) -> None:
    carrier_root = _carrier_root(runtime_parent)
    assert not carrier_root.exists()

    sibling = tmp_path / "sibling-for-dotdot"
    sibling.mkdir()
    via_dotdot = sibling / ".." / runtime_parent.name / f"agentreview-target-locks-v1-{os.geteuid()}"

    assert _acquire(via_dotdot) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not carrier_root.exists()


def test_symlinked_target_resolving_to_the_carrier_root_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    carrier_root = _carrier_root(runtime_parent)
    assert not carrier_root.exists()

    symlink_target = tmp_path / "symlink-to-carrier"
    symlink_target.symlink_to(carrier_root)

    assert _acquire(symlink_target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not carrier_root.exists()


def test_target_root_the_literal_filesystem_root_is_refused() -> None:
    """Not gated on the `runtime_parent` fixture: this must refuse regardless
    of where the runtime parent happens to live, because every path is
    at-or-beneath `/`."""

    assert _acquire(Path("/")) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2


@requires_bind_mount
def test_bind_alias_of_carrier_root_deep_inside_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """The descendant-direction case, re-affirmed alongside the new ancestral
    one: a bind alias of the carrier root itself (not just its parent),
    several levels inside an otherwise unrelated target."""

    carrier_root = _carrier_root(runtime_parent)
    carrier_root.mkdir(parents=True)
    target = tmp_path / "deep-target-carrier-alias"
    deep = target / "a" / "b" / "c"
    deep.mkdir(parents=True)
    subprocess.run(["mount", "--bind", str(carrier_root), str(deep)], check=True)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        subprocess.run(["umount", str(deep)], check=False)


# -- Mutants: #262 F1 correction must be load-bearing, and not over-refuse --


def test_mutant_ignore_ancestral_alias_mount_lets_f1_reproduce(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_IGNORE_ANCESTRAL_ALIAS_MOUNT.  The predicate this correction adds is
    forced to always report "not an alias".  Proves the R15/R16 corpus
    actually depends on `_is_ancestral_alias_mount_v2`'s truth value, not on
    some other, coincidentally-overlapping check."""

    real_predicate = epoch_module._is_ancestral_alias_mount_v2
    # 1. the real (unmutated) authority has the intended disposition.
    assert real_predicate(
        mount_point="/mnt/alias", target_path="/mnt/alias/carrier", runtime_parent_path="/tmp/rp"
    )

    monkeypatch.setattr(epoch_module, "_is_ancestral_alias_mount_v2", lambda **_kwargs: False)
    # 2. the monkeypatch actually replaced the production symbol.
    assert epoch_module._is_ancestral_alias_mount_v2(
        mount_point="/mnt/alias", target_path="/mnt/alias/carrier", runtime_parent_path="/tmp/rp"
    ) is False

    alias = tmp_path / "mutant-1-alias"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(alias)], check=True)
    try:
        target = alias / f"agentreview-target-locks-v1-{os.geteuid()}"
        # 3. the mutated production path behaves differently: F1 reproduces.
        assert _acquire(target) == "acquired", "mutant should have let F1 reproduce"
        # `target` IS the (aliased) carrier directory here, so its own
        # materialized contents -- not a nested entry named like it -- are
        # the evidence; `_carrier_material_beneath` looks for the latter.
        assert any(target.iterdir()), "carrier should have materialized beneath the accepted target"
    finally:
        subprocess.run(["umount", str(alias)], check=False)


def test_mutant_mount_scan_descendants_only_lets_f1_reproduce(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_MOUNT_SCAN_DESCENDANTS_ONLY.  `_establish_carrier_disjoint_v2` is
    replaced wholesale by the exact `PR #267` implementation -- the historical
    pre-correction production function, which only ever scanned for a mount
    at/beneath the target.  A regression that reverts the whole function to
    that shape must be caught by this corpus, not just a narrower predicate
    tweak."""

    def _pre_f1_establish_carrier_disjoint_v2(*, canonical_target_subject, parent_fd, euid):
        target_path = os.fsdecode(canonical_target_subject)
        parent_stat = os.fstat(parent_fd)
        protocol_directory = str(
            epoch_module._RUNTIME_PARENT_PATH_V2 / epoch_module._protocol_directory_name_v2(euid)
        )
        if epoch_module._is_at_or_beneath_v2(
            protocol_directory, target_path
        ) or epoch_module._is_at_or_beneath_v2(target_path, protocol_directory):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)
        try:
            target_stat = os.lstat(target_path)
        except FileNotFoundError:
            target_stat = None
        except OSError as exc:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2) from exc
        if target_stat is not None and epoch_module._same_identity_v2(target_stat, parent_stat):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)
        for device, mount_point in epoch_module._read_mount_table_v2():
            if device == parent_stat.st_dev and epoch_module._is_at_or_beneath_v2(mount_point, target_path):
                raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)

    monkeypatch.setattr(
        epoch_module, "_establish_carrier_disjoint_v2", _pre_f1_establish_carrier_disjoint_v2
    )

    alias = tmp_path / "mutant-2-alias"
    alias.mkdir()
    subprocess.run(["mount", "--bind", str(runtime_parent), str(alias)], check=True)
    try:
        target = alias / f"agentreview-target-locks-v1-{os.geteuid()}"
        assert _acquire(target) == "acquired", "mutant should reproduce the #267 pre-fix defect"
        assert any(target.iterdir()), "carrier should have materialized beneath the accepted target"
    finally:
        subprocess.run(["umount", str(alias)], check=False)


def test_mutant_treat_common_root_mount_as_alias_breaks_the_positive_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_TREAT_COMMON_ROOT_MOUNT_AS_ALIAS.  Drop the "does not also contain
    the runtime parent" carve-out, so every same-device ancestor mount --
    including the ordinary shared root filesystem -- counts as an alias.
    This must break R18, the positive control: it demonstrates that carve-out
    is load-bearing, not decorative, and that without it every normal
    same-filesystem target becomes unusable."""

    def _every_same_device_ancestor_is_an_alias(*, mount_point, target_path, runtime_parent_path):
        return epoch_module._is_at_or_beneath_v2(target_path, mount_point)

    monkeypatch.setattr(
        epoch_module, "_is_ancestral_alias_mount_v2", _every_same_device_ancestor_is_an_alias
    )

    runtime_parent = tmp_path / "runtime-parent-for-mutant-3"
    runtime_parent.mkdir()
    runtime_parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", runtime_parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())

    target = tmp_path / "ordinary-shared-root-target-for-mutant-3"
    target.mkdir()
    assert os.stat(runtime_parent).st_dev == os.stat(target).st_dev

    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2, (
        "mutant should have broken the R18 positive control by over-refusing "
        "an ordinary same-filesystem target"
    )
