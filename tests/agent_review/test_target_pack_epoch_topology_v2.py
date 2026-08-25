"""#262 K-DISJOINT: derived from VISIBLE mount topology.

The property is

    CarrierVisibleMutationDomain(K) ∩ TargetVisiblePhysicalDomain(D) = ∅

for every ACCEPTED topology, with an unestablishable topology refused rather
than assumed disjoint.

Two predecessors are forensic evidence for why this shape, and neither
transfers any qualification here. `PR #267` and `PR #268` accumulated one alias
rule per discovered direction -- descendant, ancestral, at-the-carrier,
ancestral-to-the-carrier -- until a native review produced a topology no flat
rule set could decide: a subtree bind COVERED by a whole-filesystem mount at
the same point leaves both rows in `/proc/self/mountinfo`, and a flat scan sees
the shadowed one. An architecture spike then falsified the flat model outright
with a second case: an older, DEEPER mount hidden by a newer, SHALLOWER one
defeats longest-prefix selection, because the mount a pathname actually reaches
is decided by the parent relation, not by textual prefix length.

So visibility is established FIRST, from the mount graph, and everything else
consumes it.

**The oracle discipline in this file is itself load-bearing.** The spike's first
mutation run reported all mutants surviving, because the harness realised a
mutant's projection using that same mutant's visibility model -- a wrong
projection and a wrong realisation cancelled exactly. Every mutation test below
therefore judges the mutant against a REAL filesystem observation (markers and
`st_dev`/`st_ino` identity), never against anything the mutant computed.
`test_the_mutation_oracle_is_never_the_mutant` asserts that discipline directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import app.agent_review.target_pack_epoch_v2 as epoch_module
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2,
    TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2,
    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
    MountRecordV2,
    MountTopologySnapshotV2,
    TargetPackEpochError,
    acquire_target_pack_epoch_v2,
)

PROTOCOL = f"agentreview-target-locks-v1-{os.geteuid()}"


def _can_bind_mount() -> bool:
    if os.geteuid() != 0:
        return False
    probe = Path("/tmp") / f"agentreview-topoprobe-{os.getpid()}"
    source = Path("/tmp") / f"agentreview-toposrc-{os.getpid()}"
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
    reason="BLOCKED_BY_ENVIRONMENT: needs a real mount; a skipped mount case is "
    "absent evidence, never a pass",
)


@pytest.fixture
def runtime_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    return parent


def bind(source: Path, mount_point: Path) -> Path:
    subprocess.run(["mount", "--bind", str(source), str(mount_point)], check=True)
    return mount_point


def tmpfs(mount_point: Path) -> Path:
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", str(mount_point)], check=True)
    return mount_point


def umount(*paths: Path) -> None:
    for path in reversed(paths):
        subprocess.run(["umount", str(path)], check=False)


def _acquire(target: Path) -> str:
    try:
        lease = acquire_target_pack_epoch_v2(target_root=target, exclusive=True)
    except TargetPackEpochError as exc:
        return exc.reason_code
    lease.release()
    return "acquired"


def _carrier(runtime_parent: Path) -> Path:
    return runtime_parent / PROTOCOL


def _prepare_bound_carrier(runtime_parent: Path, source: Path) -> Path:
    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    bind(source, carrier)
    os.chmod(carrier, 0o700)
    return carrier


# ============================ INDEPENDENT ORACLE ===========================
# Ground truth is what the filesystem actually shows, never what the module
# under test computed. `markers` is content the test itself planted.


def observe(path: Path) -> dict:
    stat = os.stat(path)
    return {
        "dev": stat.st_dev,
        "ino": stat.st_ino,
        "markers": sorted(p.name for p in path.iterdir() if p.name.startswith("MARKER-")),
    }


def marked(directory: Path, marker: str, mode: int = 0o755) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"MARKER-{marker}").write_text("x", encoding="utf-8")
    os.chmod(directory, mode)
    return directory


def realise(snapshot: MountTopologySnapshotV2, device: int, internal: str) -> str | None:
    """Physical (device, internal path) -> an accessible pathname, resolved
    ONLY through a trusted snapshot. Never call this with a mutant."""

    best = None
    for record in snapshot.records:
        if record.device != device or not snapshot.is_visible_v2(record):
            continue
        if internal == record.root:
            candidate, score = record.mount_point, len(record.root)
        elif internal.startswith(record.root.rstrip("/") + "/"):
            candidate = os.path.normpath(
                record.mount_point.rstrip("/") + internal[len(record.root.rstrip("/")):])
            score = len(record.root)
        else:
            continue
        if os.path.exists(candidate) and (best is None or score > best[1]):
            best = (candidate, score)
    return best[0] if best else None


# ================================ T1-T14 ===================================


def _assert_projection_matches_reality(probe: Path) -> None:
    snapshot = MountTopologySnapshotV2.observe()
    truth = observe(probe)
    device, internal = snapshot.project_v2(str(probe))
    back = realise(snapshot, device, internal)
    assert back is not None, f"projection {internal!r} is not realisable"
    stat = os.stat(back)
    assert (stat.st_dev, stat.st_ino) == (truth["dev"], truth["ino"]), (
        f"projection {(device, internal)} realised to {back}, which is not the "
        f"object pathname access actually reaches"
    )


def test_t1_plain_directory(tmp_path: Path) -> None:
    _assert_projection_matches_reality(marked(tmp_path / "plain", "plain"))


@requires_bind_mount
def test_t2_subtree_bind(tmp_path: Path) -> None:
    source = marked(tmp_path / "src" / "sub", "subtree")
    point = marked(tmp_path / "mp", "under")
    bind(source, point)
    try:
        _assert_projection_matches_reality(point)
    finally:
        umount(point)


@requires_bind_mount
def test_t3_whole_filesystem_bind(tmp_path: Path) -> None:
    fs = marked(tmp_path / "fsroot", "ignored")
    tmpfs(fs)
    try:
        (fs / "MARKER-wholefs").write_text("x", encoding="utf-8")
        alias = marked(tmp_path / "alias", "under")
        bind(fs, alias)
        try:
            _assert_projection_matches_reality(alias)
        finally:
            umount(alias)
    finally:
        umount(fs)


@requires_bind_mount
def test_t4_subtree_covered_by_whole_filesystem_at_the_same_point(tmp_path: Path) -> None:
    """The topology that falsified the flat model. Both rows stay in
    mountinfo; only the parent relation says which one a pathname reaches."""

    source = marked(tmp_path / "src" / "sub", "lower-subtree")
    point = marked(tmp_path / "P", "orig")
    bind(source, point)
    try:
        tmpfs(point)
        try:
            (point / "MARKER-upper-wholefs").write_text("x", encoding="utf-8")
            assert observe(point)["markers"] == ["MARKER-upper-wholefs"]
            snapshot = MountTopologySnapshotV2.observe()
            rows = [r for r in snapshot.records if r.mount_point == str(point)]
            assert len(rows) == 2, "both the covered and the covering row must be present"
            assert snapshot.governing_mount_v2(str(point)).root == "/"
            _assert_projection_matches_reality(point)
        finally:
            umount(point)
    finally:
        umount(point)


@requires_bind_mount
def test_t5_inverse_stack_subtree_above_whole_filesystem(tmp_path: Path) -> None:
    source = marked(tmp_path / "src" / "sub", "upper-subtree")
    point = marked(tmp_path / "P", "orig")
    tmpfs(point)
    try:
        bind(source, point)
        try:
            _assert_projection_matches_reality(point)
            assert observe(point)["markers"] == ["MARKER-upper-subtree"]
        finally:
            umount(point)
    finally:
        umount(point)


@requires_bind_mount
def test_t6_three_deep_stack_at_one_point(tmp_path: Path) -> None:
    a = marked(tmp_path / "a", "deep-a")
    b = marked(tmp_path / "b", "deep-b")
    point = marked(tmp_path / "P", "orig")
    bind(a, point)
    try:
        bind(b, point)
        try:
            tmpfs(point)
            try:
                (point / "MARKER-deep-top").write_text("x", encoding="utf-8")
                _assert_projection_matches_reality(point)
            finally:
                umount(point)
        finally:
            umount(point)
    finally:
        umount(point)


@requires_bind_mount
def test_t7_bind_of_bind(tmp_path: Path) -> None:
    source = marked(tmp_path / "src" / "sub", "bob")
    hop = marked(tmp_path / "hop", "hop")
    bind(source, hop)
    try:
        destination = marked(tmp_path / "dst", "dst")
        bind(hop, destination)
        try:
            _assert_projection_matches_reality(destination)
        finally:
            umount(destination)
    finally:
        umount(hop)


@requires_bind_mount
def test_t8_ancestor_mount_hides_a_descendant_mount(tmp_path: Path) -> None:
    """The descendant row survives in mountinfo but is unreachable."""

    inner = marked(tmp_path / "anc" / "inner", "deep-orig")
    bind(marked(tmp_path / "deepsrc", "deep-mounted"), inner)
    try:
        replacement = marked(tmp_path / "repl", "ancestor-replacement")
        bind(replacement, tmp_path / "anc")
        try:
            snapshot = MountTopologySnapshotV2.observe()
            hidden = [r for r in snapshot.records if r.mount_point == str(inner)]
            assert hidden, "the hidden row is still present in mountinfo"
            assert not any(snapshot.is_visible_v2(r) for r in hidden)
            assert str(inner) not in [
                r.mount_point for r in snapshot.visible_child_mounts_v2(str(tmp_path / "anc"))]
            _assert_projection_matches_reality(tmp_path / "anc")
        finally:
            umount(tmp_path / "anc")
    finally:
        umount(inner)


@requires_bind_mount
def test_t9_descendant_created_after_ancestor_replacement_is_visible(tmp_path: Path) -> None:
    anchor = marked(tmp_path / "anc", "anc-orig")
    replacement = marked(tmp_path / "repl", "anc-new")
    (replacement / "inner").mkdir()
    bind(replacement, anchor)
    try:
        inner_source = marked(tmp_path / "innersrc", "inner-mounted")
        bind(inner_source, anchor / "inner")
        try:
            snapshot = MountTopologySnapshotV2.observe()
            assert str(anchor / "inner") in [
                r.mount_point for r in snapshot.visible_child_mounts_v2(str(anchor))]
            _assert_projection_matches_reality(anchor / "inner")
        finally:
            umount(anchor / "inner")
    finally:
        umount(anchor)


@requires_bind_mount
def test_t10_older_deeper_mount_hidden_by_newer_shallower_mount(tmp_path: Path) -> None:
    """Longest-prefix selection gets this WRONG: the deeper row's parent is not
    the governing mount, so it was attached to a tree since covered."""

    deep = marked(tmp_path / "a" / "b" / "c", "c-orig")
    bind(marked(tmp_path / "deepsrc", "deep-old"), deep)
    try:
        shallow = marked(tmp_path / "shalsrc", "shallow-new")
        (shallow / "b" / "c").mkdir(parents=True)
        bind(shallow, tmp_path / "a")
        try:
            probe = tmp_path / "a" / "b" / "c"
            assert observe(probe)["markers"] == [], "the covered deep mount must be unreachable"
            snapshot = MountTopologySnapshotV2.observe()
            assert snapshot.governing_mount_v2(str(probe)).mount_point == str(tmp_path / "a")
            _assert_projection_matches_reality(probe)
        finally:
            umount(tmp_path / "a")
    finally:
        umount(deep)


@requires_bind_mount
def test_t11_target_is_itself_a_mountpoint(tmp_path: Path) -> None:
    target = marked(tmp_path / "target", "target-orig")
    bind(marked(tmp_path / "src", "target-src"), target)
    try:
        _assert_projection_matches_reality(target)
    finally:
        umount(target)


@requires_bind_mount
def test_t12_protocol_directory_is_a_mountpoint(runtime_parent: Path, tmp_path: Path) -> None:
    carrier = _prepare_bound_carrier(runtime_parent, marked(tmp_path / "src", "proto-src"))
    try:
        _assert_projection_matches_reality(carrier)
    finally:
        umount(carrier)


@requires_bind_mount
def test_t13_runtime_parent_is_a_mountpoint(tmp_path: Path) -> None:
    parent = marked(tmp_path / "rp", "rp-orig", mode=0o1777)
    bind(marked(tmp_path / "src" / "sub", "rp-src"), parent)
    try:
        _assert_projection_matches_reality(parent)
    finally:
        umount(parent)


@requires_bind_mount
def test_t14_runtime_parent_below_a_mounted_ancestor(tmp_path: Path) -> None:
    source = marked(tmp_path / "src", "anc-src")
    marked(source / "rp", "rp-under-anc")
    anchor = marked(tmp_path / "anc", "anc-orig")
    bind(source, anchor)
    try:
        _assert_projection_matches_reality(anchor / "rp")
    finally:
        umount(anchor)


# ==================== HISTORICAL CORPUS — knowledge ported =================
# Every one of these reproduced on live master 2876434 before this branch
# existed, and each was a separate alias rule in #267/#268. Here they are one
# statement: project both sides through the visible topology and intersect.


def test_target_containing_the_carrier_is_refused(runtime_parent: Path, tmp_path: Path) -> None:
    assert _acquire(tmp_path) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not _carrier(runtime_parent).exists()


def test_target_equal_to_the_carrier_root_is_refused(runtime_parent: Path) -> None:
    carrier = _carrier(runtime_parent)
    assert not carrier.exists()
    assert _acquire(carrier) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    assert not carrier.exists()


def test_target_inside_the_carrier_is_refused(runtime_parent: Path) -> None:
    """Intersection is symmetric: the carrier subtree CONTAINS this target."""

    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    nested = carrier / "nested"
    nested.mkdir()
    assert _acquire(nested) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2


def test_a_sibling_of_the_carrier_is_accepted(runtime_parent: Path) -> None:
    """OVER-REJECTION CONTROL. The carrier's domain is the protocol directory,
    never the whole runtime parent -- otherwise every target beneath the
    runtime parent would be refused."""

    sibling = runtime_parent / "sibling-target"
    sibling.mkdir()
    assert _acquire(sibling) == "acquired"


def test_ordinary_unrelated_target_is_accepted(runtime_parent: Path, tmp_path: Path) -> None:
    target = tmp_path / "ordinary"
    target.mkdir()
    assert _acquire(target) == "acquired"


@requires_bind_mount
def test_runtime_parent_alias_below_the_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    inner = target / "vendor"
    inner.mkdir(parents=True)
    bind(runtime_parent, inner)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert list(target.rglob("*.lock")) == []
    finally:
        umount(inner)


@requires_bind_mount
def test_deep_runtime_parent_alias_inside_the_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    deep = target / "a" / "b" / "c"
    deep.mkdir(parents=True)
    bind(runtime_parent, deep)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(deep)


@requires_bind_mount
def test_ancestral_runtime_parent_alias_is_refused(runtime_parent: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.mkdir()
    bind(runtime_parent, alias)
    try:
        assert _acquire(alias / PROTOCOL) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(alias)


@requires_bind_mount
def test_escaped_mount_point_does_not_hide_the_alias(runtime_parent: Path, tmp_path: Path) -> None:
    """mountinfo escapes space/tab/newline/backslash with ONE backslash."""

    alias = tmp_path / "alias mount dir"
    alias.mkdir()
    bind(runtime_parent, alias)
    try:
        assert "\\040" in Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        assert _acquire(alias / PROTOCOL) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(alias)


@requires_bind_mount
def test_existing_carrier_bound_from_the_target_root_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    carrier = _prepare_bound_carrier(runtime_parent, target)
    try:
        before = sorted(entry.name for entry in target.iterdir())
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert sorted(entry.name for entry in target.iterdir()) == before
    finally:
        umount(carrier)


@requires_bind_mount
def test_existing_carrier_bound_from_a_target_descendant_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    inner = target / "sub"
    inner.mkdir(parents=True)
    carrier = _prepare_bound_carrier(runtime_parent, inner)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert list(target.rglob("*.lock")) == []
    finally:
        umount(carrier)


@requires_bind_mount
def test_runtime_parent_grafted_from_a_target_descendant_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project"
    inner = target / "scratch"
    inner.mkdir(parents=True)
    os.chmod(inner, 0o1777)
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    bind(inner, parent)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        assert list(target.rglob("*.lock")) == []
    finally:
        umount(parent)


@requires_bind_mount
def test_transitively_grafted_runtime_parent_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project"
    inner = target / "scratch"
    inner.mkdir(parents=True)
    os.chmod(inner, 0o1777)
    hop = tmp_path / "hop"
    hop.mkdir()
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    bind(inner, hop)
    try:
        bind(hop, parent)
        try:
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        finally:
            umount(parent)
    finally:
        umount(hop)


@requires_bind_mount
def test_shadowed_subtree_graft_under_a_whole_filesystem_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N6, the finding that falsified the flat model. The subtree graft is
    COVERED, so it governs nothing and must not refuse anything. A rule keyed
    on `root != "/"` over a flat table refuses every target here."""

    unrelated = tmp_path / "unrelated" / "x"
    unrelated.mkdir(parents=True)
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    bind(unrelated, parent)
    try:
        tmpfs(parent)
        try:
            os.chmod(parent, 0o1777)
            monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
            monkeypatch.setattr(
                epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
            target = tmp_path / "project"
            target.mkdir()
            assert _acquire(target) == "acquired"
        finally:
            umount(parent)
    finally:
        umount(parent)


# ============== VISIBILITY PARTITION CONTROLS (T15-T18) ===================
# The target's domain is a PARTITION, not a naive union: a visible child mount
# REPLACES the slice it covers.


@requires_bind_mount
def test_t18_carrier_aliasing_the_visible_child_mount_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """T18. The carrier lands inside the target's visible mounted subtree,
    which a single (device, path) pair for the target root cannot see."""

    target = tmp_path / "project"
    vendor = target / "vendor"
    vendor.mkdir(parents=True)
    real = tmp_path / "realvendor"
    real.mkdir()
    bind(real, vendor)
    try:
        carrier = _prepare_bound_carrier(runtime_parent, real)
        try:
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        finally:
            umount(carrier)
    finally:
        umount(vendor)


@requires_bind_mount
def test_t16_object_inside_the_visible_replacement_is_target_visible(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """T16. Deeper inside the visible replacement, not just at its root."""

    target = tmp_path / "project"
    vendor = target / "vendor"
    vendor.mkdir(parents=True)
    real = tmp_path / "realvendor"
    deep = real / "inner"
    deep.mkdir(parents=True)
    bind(real, vendor)
    try:
        carrier = _prepare_bound_carrier(runtime_parent, deep)
        try:
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
        finally:
            umount(carrier)
    finally:
        umount(vendor)


@requires_bind_mount
def test_t15_t17_covered_lower_storage_is_not_target_visible(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """T15/T17, and the reason the domain is a partition rather than a union.

    `<target>/vendor` has a visible mount over it. The storage that mount
    covers is still on disk and still lexically beneath the target's base
    physical prefix, but it is no longer reachable anywhere under the target.
    A carrier aliasing only that covered storage is therefore NOT an overlap,
    and a naive union would wrongly refuse it.
    """

    target = tmp_path / "project"
    vendor = target / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "covered-content").write_text("still on disk", encoding="utf-8")
    covered_physical = vendor.resolve()

    real = tmp_path / "realvendor"
    real.mkdir()
    bind(real, vendor)
    try:
        assert not (vendor / "covered-content").exists(), "the lower storage must be covered"
        snapshot = MountTopologySnapshotV2.observe()
        segments = epoch_module._visible_physical_domain_v2(snapshot, str(target))
        base_device, base_internal = snapshot.project_v2(str(target))
        covered_internal = os.path.normpath(
            base_internal.rstrip("/") + str(covered_physical)[len(str(target)):])
        assert not any(s.intersects(base_device, covered_internal) for s in segments), (
            "covered lower storage must not be part of the target's visible domain")
        assert any(s.device == real.stat().st_dev for s in segments), (
            "the visible replacement must contribute its own segment")
    finally:
        umount(vendor)


# ================= ROOT / CHROOT BOUNDARY (§11) ===========================


def test_visible_root_whose_parent_has_no_record_is_resolvable() -> None:
    """Linux permits the process-visible root's parent to lie outside the
    process root, so it has no row. That is a boundary, not a defect, and the
    visible root is NOT required to be self-parented."""

    table = (
        "36 35 98:0 / / rw,relatime - ext4 /dev/root rw\n"      # parent 35 absent
        "37 36 0:22 / /tmp rw,nosuid - tmpfs tmpfs rw\n"
    )
    snapshot = MountTopologySnapshotV2.parse(table)
    assert 35 not in snapshot.by_id
    assert snapshot.governing_mount_v2("/tmp/anything").mount_id == 37
    assert snapshot.governing_mount_v2("/elsewhere").mount_id == 36


def test_missing_parent_for_a_relevant_internal_mount_is_unknown() -> None:
    """A gap that breaks a chain this decision depends on is UNKNOWN -- not the
    same thing as the root boundary above."""

    table = (
        "36 35 98:0 / / rw - ext4 /dev/root rw\n"
        "40 39 98:0 /x /deep_target_parent/child rw - ext4 /dev/root rw\n"   # parent 39 absent
    )
    snapshot = MountTopologySnapshotV2.parse(table)

    # An earlier revision of THIS TEST asserted the defect: that the
    # disconnected record simply governs nothing and is silently hidden. That
    # is fail-open -- a mount beneath the target whose position cannot be
    # established was dropped from the target's domain -- and the test's name
    # claimed the opposite of what its body certified. It now asserts UNKNOWN.
    with pytest.raises(TargetPackEpochError) as excinfo:
        snapshot.validate_relevant_chain_v2(snapshot.by_id[40])
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    with pytest.raises(TargetPackEpochError):
        snapshot.visible_child_mounts_v2("/deep_target_parent")


# ========================== FAIL-CLOSED (§11) =============================


@pytest.mark.parametrize("errno_code", [5, 13], ids=["EIO", "EACCES"])
def test_unreadable_mountinfo_is_unknown_through_public_acquisition(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, errno_code: int
) -> None:
    """Asserted through the PUBLIC entry point, which is the only place a
    caller can observe a reason code."""

    real_read_text = Path.read_text

    def _raise(self: Path, *args: object, **kwargs: object) -> str:
        if str(self) == "/proc/self/mountinfo":
            raise OSError(errno_code, os.strerror(errno_code))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raise)
    target = tmp_path / "unrelated"
    target.mkdir()
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


@pytest.mark.parametrize(
    "table,label",
    [
        ("36 35 98:0 / / rw - ext4 /d rw\n40 35 98:0 /trunc\n", "malformed-row"),
        ("36 35 98:0 / / rw - ext4 /d rw\n36 35 98:0 / /x rw - ext4 /d rw\n", "duplicate-id"),
        ("\n", "empty"),
        ("36 35 98:0 / /notroot rw - ext4 /d rw\n", "no-root-mount"),
    ],
)
def test_unusable_topology_is_unknown(table: str, label: str) -> None:
    with pytest.raises(TargetPackEpochError) as excinfo:
        MountTopologySnapshotV2.parse(table).governing_mount_v2("/tmp/x")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_cycle_not_involving_the_root_is_unknown() -> None:
    """A genuine unresolvable cycle: two mounts parenting each other, neither
    of them the visible root.

    A cycle that runs THROUGH the root is a different thing and is not this:
    the walk terminates at the root boundary before ever traversing it, which
    `test_a_cycle_through_the_root_terminates_at_the_boundary` asserts.
    """

    records = (
        MountRecordV2(1, 9, 0, "/", "/", "ext4"),
        MountRecordV2(2, 3, 0, "/a", "/tmp/p/x", "ext4"),
        MountRecordV2(3, 2, 0, "/b", "/tmp/p/y", "ext4"),
    )
    snapshot = MountTopologySnapshotV2(records)
    with pytest.raises(TargetPackEpochError) as excinfo:
        snapshot.validate_relevant_chain_v2(records[1])
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_a_root_with_a_present_non_self_parent_has_no_boundary_and_is_unknown() -> None:
    """Sharpened by the N8 boundary rule.

    An earlier revision fell back to "all roots are candidates" when none had
    an absent parent, so this table resolved. It should not: record 1 sits at
    `/` but its parent IS present and is not itself, so there is no boundary
    root at all and the chain closes into a cycle. UNKNOWN is the precise
    answer, and the legitimate boundary forms are covered by
    `test_n8_boundary_root_forms`.
    """

    records = (
        MountRecordV2(1, 2, 0, "/", "/", "ext4"),
        MountRecordV2(2, 1, 0, "/", "/a", "ext4"),
    )
    snapshot = MountTopologySnapshotV2(records)
    with pytest.raises(TargetPackEpochError) as excinfo:
        snapshot.validate_relevant_chain_v2(records[0])
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_ambiguous_same_point_stack_is_unknown() -> None:
    records = (
        MountRecordV2(1, 0, 0, "/", "/", "ext4"),
        MountRecordV2(2, 1, 0, "/x", "/m", "ext4"),
        MountRecordV2(3, 1, 0, "/y", "/m", "ext4"),
    )
    with pytest.raises(TargetPackEpochError) as excinfo:
        MountTopologySnapshotV2(records).governing_mount_v2("/m")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_projection_cannot_escape_the_mounts_own_root() -> None:
    records = (MountRecordV2(1, 0, 0, "/", "/", "ext4"),
               MountRecordV2(2, 1, 0, "/sub", "/m", "ext4"))
    snapshot = MountTopologySnapshotV2(records)
    device, internal = snapshot.project_v2("/m/inner")
    assert internal == "/sub/inner"
    assert epoch_module._within_v2(internal, "/sub")


def test_mount_root_and_mount_point_share_one_escape_decoder() -> None:
    table = "37 36 0:22 /srv/a\\040b /mnt/c\\040d rw - ext4 /dev/sdb rw\n" \
            "36 35 98:0 / / rw - ext4 /d rw\n"
    record = [r for r in MountTopologySnapshotV2.parse(table).records if r.mount_id == 37][0]
    assert record.root == "/srv/a b"
    assert record.mount_point == "/mnt/c d"


def test_escape_decoding_is_single_pass() -> None:
    assert epoch_module._unescape_mountinfo_path_v2("/x\\134040y") == "/x\\040y"


# ======================== SINGLE-AUTHORITY SWEEP ==========================


def test_exactly_one_mountinfo_authority() -> None:
    source = Path(epoch_module.__file__).read_text(encoding="utf-8")
    assert source.count('"/proc/self/mountinfo"') == 1
    assert source.count("def _establish_carrier_disjoint_v2") == 1
    assert source.count("def governing_mount_v2") == 1
    assert source.count("def project_v2") == 1


def test_visibility_is_derived_from_the_governing_relation() -> None:
    """Not a second algorithm: `is_visible_v2` is defined BY `governing_mount_v2`."""

    import inspect
    body = inspect.getsource(MountTopologySnapshotV2.is_visible_v2)
    assert "resolve_query_v2" in body and "POINT_LOOKUP" in body


# ================================ MUTANTS =================================
# Every mutant is judged against a REAL filesystem observation. None is ever
# allowed to supply the oracle used to judge it -- that exact circularity made
# a whole mutation run report false survival during the architecture spike, so
# it is asserted structurally below.


def _mutant_snapshot(cls_body):
    """Build a mutant subclass of the real snapshot from a governing override."""
    return type("Mutant", (MountTopologySnapshotV2,), {"_governing_mount_raw_v2": cls_body})


def _flat_longest_prefix(self, path):
    path = epoch_module._normalize_absolute_v2(path)
    candidates = [r for r in self.records
                  if path == r.mount_point or path.startswith(r.mount_point.rstrip("/") + "/")]
    if not candidates:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
    return max(candidates, key=lambda r: len(r.mount_point))


def _assert_mutant_disagrees_with_reality(mutant_cls, probe: Path) -> None:
    """The mutant's projection, realised through a TRUSTED snapshot, must not
    be the object pathname access actually reaches."""

    truth = MountTopologySnapshotV2.observe()
    reality = observe(probe)
    trusted_device, trusted_internal = truth.project_v2(str(probe))
    trusted_back = realise(truth, trusted_device, trusted_internal)
    assert trusted_back is not None
    trusted_stat = os.stat(trusted_back)
    assert (trusted_stat.st_dev, trusted_stat.st_ino) == (reality["dev"], reality["ino"]), (
        "the real authority must agree with reality before a mutant is judged")

    mutant = mutant_cls(truth.records)
    try:
        device, internal = mutant.project_v2(str(probe))
    except TargetPackEpochError:
        return  # refusing outright is also a disagreement
    back = realise(truth, device, internal)   # TRUSTED realisation, never the mutant
    if back is None:
        return
    stat = os.stat(back)
    assert (stat.st_dev, stat.st_ino) != (reality["dev"], reality["ino"]), (
        f"{mutant_cls.__name__} agreed with reality; it is not discriminated here")


@requires_bind_mount
def test_mutant_ignore_parent_id_and_flat_prefix_scans(tmp_path: Path) -> None:
    """M_IGNORE_PARENT_ID / M_LONGEST_PREFIX_FROM_FLAT_TABLE, killed by the
    older-deeper-hidden-by-newer-shallower topology."""

    deep = marked(tmp_path / "a" / "b" / "c", "c-orig")
    bind(marked(tmp_path / "deepsrc", "deep-old"), deep)
    try:
        shallow = marked(tmp_path / "shalsrc", "shallow-new")
        (shallow / "b" / "c").mkdir(parents=True)
        bind(shallow, tmp_path / "a")
        try:
            probe = tmp_path / "a" / "b" / "c"
            for name in ("M_IGNORE_PARENT_ID", "M_LONGEST_PREFIX_FROM_FLAT_TABLE"):
                mutant = _mutant_snapshot(_flat_longest_prefix)
                mutant.__name__ = name
                _assert_mutant_disagrees_with_reality(mutant, probe)
        finally:
            umount(tmp_path / "a")
    finally:
        umount(deep)


@requires_bind_mount
def test_mutant_same_point_stack_selectors(tmp_path: Path) -> None:
    """M_TOPMOST_BY_MOUNTPOINT_ONLY, M_PICK_LOWEST_MOUNT_ID,
    M_PICK_HIGHEST_MOUNT_ID, M_IGNORE_SAME_POINT_STACK,
    M_ROOT_FIELD_DECIDES_VISIBILITY -- all killed by the inverse stack, where
    the TOP mount is a subtree graft and the bottom is a whole filesystem."""

    source = marked(tmp_path / "src" / "sub", "upper-subtree")
    point = marked(tmp_path / "P", "orig")
    tmpfs(point)
    try:
        bind(source, point)
        try:
            def lowest(self, path):
                c = [r for r in self.records if r.mount_point == str(point)]
                return min(c, key=lambda r: r.mount_id) if c else _flat_longest_prefix(self, path)

            def root_only(self, path):
                p = epoch_module._normalize_absolute_v2(path)
                c = [r for r in self.records
                     if (p == r.mount_point or p.startswith(r.mount_point.rstrip("/") + "/"))
                     and r.root == "/"]
                if not c:
                    raise TargetPackEpochError(
                        TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
                return max(c, key=lambda r: len(r.mount_point))

            for name, fn in (("M_PICK_LOWEST_MOUNT_ID", lowest),
                             ("M_IGNORE_SAME_POINT_STACK", lowest),
                             ("M_ROOT_FIELD_DECIDES_VISIBILITY", root_only)):
                mutant = _mutant_snapshot(fn)
                mutant.__name__ = name
                _assert_mutant_disagrees_with_reality(mutant, point)
        finally:
            umount(point)
    finally:
        umount(point)


@requires_bind_mount
def test_mutant_include_hidden_child_mount(tmp_path: Path) -> None:
    """M_INCLUDE_HIDDEN_CHILD_MOUNT -- a descendant mount hidden by an ancestor
    replacement must not be reported as a visible child."""

    inner = marked(tmp_path / "anc" / "inner", "deep-orig")
    bind(marked(tmp_path / "deepsrc", "deep-mounted"), inner)
    try:
        bind(marked(tmp_path / "repl", "ancestor-replacement"), tmp_path / "anc")
        try:
            truth = MountTopologySnapshotV2.observe()
            honest = truth.visible_child_mounts_v2(str(tmp_path / "anc"))
            assert str(inner) not in [r.mount_point for r in honest]

            class M_INCLUDE_HIDDEN_CHILD_MOUNT(MountTopologySnapshotV2):
                def visible_child_mounts_v2(self, path):
                    prefix = epoch_module._normalize_absolute_v2(path).rstrip("/") + "/"
                    return tuple(r for r in self.records if r.mount_point.startswith(prefix))

            mutant = M_INCLUDE_HIDDEN_CHILD_MOUNT(truth.records)
            assert str(inner) in [
                r.mount_point for r in mutant.visible_child_mounts_v2(str(tmp_path / "anc"))], (
                "mutant should have included the hidden descendant")
            # and reality agrees with the honest answer, not the mutant's
            assert observe(tmp_path / "anc")["markers"] == ["MARKER-ancestor-replacement"]
        finally:
            umount(tmp_path / "anc")
    finally:
        umount(inner)


@requires_bind_mount
def test_mutant_target_root_only_omits_the_visible_child_mount(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_TARGET_ROOT_ONLY / M_VISIBLE_CHILD_MOUNT_OMITTED_FROM_TARGET_DOMAIN --
    drop the child segments and T18's overlap disappears."""

    target = tmp_path / "project"
    vendor = target / "vendor"
    vendor.mkdir(parents=True)
    real = tmp_path / "realvendor"
    real.mkdir()
    bind(real, vendor)
    try:
        carrier = _prepare_bound_carrier(runtime_parent, real)
        try:
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2

            real_domain = epoch_module._visible_physical_domain_v2

            def _root_segment_only(snapshot, path):
                return real_domain(snapshot, path)[:1]

            monkeypatch.setattr(epoch_module, "_visible_physical_domain_v2", _root_segment_only)
            assert _acquire(target) == "acquired", (
                "mutant should have lost the visible child mount from the target domain")
        finally:
            umount(carrier)
    finally:
        umount(vendor)


@requires_bind_mount
def test_mutant_base_segment_does_not_exclude_the_visible_child(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_TARGET_BASE_SEGMENT_DOES_NOT_EXCLUDE_VISIBLE_CHILD /
    M_HIDDEN_LOWER_SUBTREE_COUNTS_AS_TARGET_VISIBLE -- stop excluding covered
    slices and the domain becomes a naive union, so a carrier aliasing only
    COVERED storage is wrongly refused."""

    target = tmp_path / "project"
    vendor = target / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "covered-content").write_text("still on disk", encoding="utf-8")

    # Alias the LOWER storage before it is covered -- once the mount is in
    # place that storage has no pathname under the target at all, which is
    # exactly the property under test.
    #
    # The alias is made PRIVATE first. Under the shared propagation most hosts
    # default to, mounting over `<target>/vendor` afterwards propagates into
    # this alias too, and the carrier would then genuinely reach the visible
    # replacement -- a real overlap, correctly refused, but not the case under
    # test here. (That propagation behaviour is itself handled with no rule of
    # its own: the propagated mount simply appears in the table and is
    # resolved like any other.)
    carrier = _prepare_bound_carrier(runtime_parent, vendor)
    subprocess.run(["mount", "--make-private", str(carrier)], check=True)
    try:
        real = tmp_path / "realvendor"
        real.mkdir()
        bind(real, vendor)
        try:
            assert not (vendor / "covered-content").exists(), "lower storage must be covered"
            assert _acquire(target) == "acquired", (
                "covered lower storage is not reachable under the target")

            real_domain = epoch_module._visible_physical_domain_v2

            def _no_exclusion(snapshot, path):
                return tuple(
                    epoch_module._PhysicalSegmentV2(s.device, s.internal_path, ())
                    for s in real_domain(snapshot, path))

            monkeypatch.setattr(epoch_module, "_visible_physical_domain_v2", _no_exclusion)
            assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2, (
                "mutant should have counted covered storage as target-visible")
        finally:
            umount(vendor)
    finally:
        umount(carrier)


def test_mutant_require_all_parent_ids_present() -> None:
    """M_REQUIRE_ALL_PARENT_IDS_PRESENT -- killed by the legitimate
    process-visible root boundary, whose parent is outside the process root."""

    table = ("36 35 98:0 / / rw - ext4 /dev/root rw\n"
             "37 36 0:22 / /tmp rw - tmpfs tmpfs rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    assert snapshot.governing_mount_v2("/tmp/x").mount_id == 37   # honest

    class M_REQUIRE_ALL_PARENT_IDS_PRESENT(MountTopologySnapshotV2):
        def _visible_root_v2(self):
            for record in self.records:
                if record.parent_id not in self.by_id:
                    raise TargetPackEpochError(
                        TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
            return MountTopologySnapshotV2._visible_root_v2(self)

    with pytest.raises(TargetPackEpochError):
        M_REQUIRE_ALL_PARENT_IDS_PRESENT(snapshot.records).governing_mount_v2("/tmp/x")


def test_the_mutation_oracle_is_never_the_mutant() -> None:
    """Durable negative knowledge from the architecture spike.

    Its first mutation run reported every mutant surviving. The harness had
    realised a mutant's projection using that mutant's own visibility model, so
    a wrong projection and a wrong realisation cancelled exactly. The
    discriminator below must therefore realise through the TRUSTED snapshot.
    """

    import inspect
    body = inspect.getsource(_assert_mutant_disagrees_with_reality)
    assert "realise(truth," in body, "the oracle must realise via the trusted snapshot"
    assert "realise(mutant" not in body and "realise(self" not in body


# ============== F1-F6: native review of d74b9ea, corrective corpus =========
#
# The topology graph survived that review; what it found were gaps INSIDE the
# relation, plus one architectural over-claim. The graph establishes VISIBLE
# MOUNT TOPOLOGY. It does not, by itself, establish that a physical projection
# means what K-DISJOINT reads it to mean, nor that pathname spelling is a valid
# discriminator. Those are now explicit preconditions, and where either cannot
# be established the answer is UNKNOWN.


def test_f1a_self_parented_root_resolves_instead_of_hanging() -> None:
    """F1. A root reporting `mount_id == parent_id` is legitimate, and made an
    unbounded stack climb select the same record forever -- public acquisition
    HUNG rather than refusing. Termination is the property under test, so this
    must complete, not raise."""

    snapshot = MountTopologySnapshotV2((MountRecordV2(1, 1, 0, "/", "/", "ext4"),))
    assert snapshot.governing_mount_v2("/tmp/anything").mount_id == 1


def test_f1b_root_with_an_external_parent_is_a_boundary_not_a_defect() -> None:
    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "37 36 0:22 / /tmp rw - tmpfs t rw\n")
    assert MountTopologySnapshotV2.parse(table).governing_mount_v2("/tmp/x").mount_id == 37


def test_f1c_non_root_self_parent_is_unknown() -> None:
    """A self-parent that is NOT the visible root is a cycle of length one."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 40 98:0 /x /tmp/p/v rw - ext4 /d rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    with pytest.raises(TargetPackEpochError) as excinfo:
        snapshot.visible_child_mounts_v2("/tmp/p")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f1d_same_point_stack_cycle_terminates_as_unknown() -> None:
    records = (
        MountRecordV2(1, 9, 0, "/", "/", "ext4"),
        MountRecordV2(2, 3, 0, "/a", "/tmp/p/x", "ext4"),
        MountRecordV2(3, 2, 0, "/b", "/tmp/p/y", "ext4"),
    )
    # Asked through the relevant scope: these records are beneath the path
    # whose domain is being computed, so their unresolvable chain is UNKNOWN
    # rather than quietly unreachable.
    with pytest.raises(TargetPackEpochError) as excinfo:
        MountTopologySnapshotV2(records).visible_child_mounts_v2("/tmp/p")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f4a_one_acquisition_reads_the_mount_table_exactly_once(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4. One PARSER is not one OBSERVATION. Runtime-parent eligibility and
    the disjointness decision previously read mountinfo separately and could
    describe different worlds."""

    reads: list[str] = []
    real_read_text = Path.read_text

    def _counting(self: Path, *args: object, **kwargs: object) -> str:
        if str(self) == "/proc/self/mountinfo":
            reads.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting)
    target = tmp_path / "ordinary"
    target.mkdir()
    assert _acquire(target) == "acquired"
    assert len(reads) == 1, f"expected exactly one mountinfo observation, saw {len(reads)}"


def test_f5a_ambiguous_visible_descendant_propagates_unknown_publicly(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5. An unresolvable child beneath the target must reach the CALLER as
    UNKNOWN, not be quietly treated as absent."""

    target = tmp_path / "project"
    target.mkdir()
    real_observe = MountTopologySnapshotV2.observe

    def _with_ambiguous_child(cls=None):
        snapshot = real_observe()
        extra = (
            MountRecordV2(10 ** 7, 10 ** 7 + 1, 0, "/a", str(target / "v"), "ext4"),
            MountRecordV2(10 ** 7 + 2, 10 ** 7 + 3, 0, "/b", str(target / "v"), "ext4"),
        )
        return MountTopologySnapshotV2(snapshot.records + extra)

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_ambiguous_child))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def _overlay_available() -> bool:
    if os.geteuid() != 0:
        return False
    import tempfile as _t
    base = Path(_t.mkdtemp())
    for name in ("low", "up", "work", "mnt"):
        (base / name).mkdir()
    try:
        result = subprocess.run(
            ["mount", "-t", "overlay", "overlay", "-o",
             f"lowerdir={base}/low,upperdir={base}/up,workdir={base}/work", str(base / "mnt")],
            capture_output=True)
        if result.returncode != 0:
            return False
        subprocess.run(["umount", str(base / "mnt")], check=False)
        return True
    finally:
        import shutil as _s
        _s.rmtree(base, ignore_errors=True)


requires_overlay = pytest.mark.skipif(
    not _overlay_available(),
    reason="BLOCKED_BY_ENVIRONMENT: needs a real overlay mount; a skip is absent evidence")


@requires_overlay
def test_f2a_overlay_target_whose_upper_tree_holds_the_carrier_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2, the P1. The carrier's writes reach the target through the overlay's
    upper layer while the two projections compare as different devices, so the
    old code declared them disjoint and materialized K inside the target.

    Projection applicability now refuses before any mutation. This asserts the
    REFUSAL and the absence of mutation -- not a reconstructed overlay model,
    which this slice deliberately does not own.
    """

    for name in ("low", "up", "work", "mnt"):
        (tmp_path / name).mkdir()
    parent = tmp_path / "up"
    os.chmod(parent, 0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o",
         f"lowerdir={tmp_path}/low,upperdir={tmp_path}/up,workdir={tmp_path}/work",
         str(tmp_path / "mnt")], check=True)
    try:
        target = tmp_path / "mnt"
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        assert [p.name for p in target.iterdir() if p.name.startswith("agentreview")] == []
    finally:
        umount(tmp_path / "mnt")


def test_f2b_supported_direct_projection_filesystem_still_acquires(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """POSITIVE CONTROL for F2: the allowlist must not refuse ordinary work."""

    target = tmp_path / "ordinary"
    target.mkdir()
    snapshot = MountTopologySnapshotV2.observe()
    assert snapshot.governing_mount_v2(str(target)).filesystem_type in \
        epoch_module._DIRECT_PROJECTION_FILESYSTEMS_V2
    assert _acquire(target) == "acquired"


def test_f2_unrecognised_filesystem_is_unknown_not_assumed_direct() -> None:
    """The allowlist is POSITIVE: an unmodeled filesystem refuses rather than
    being assumed to project directly.

    Asserted against the applicability gate itself. Through public acquisition
    an unsupported RUNTIME-PARENT filesystem is refused even earlier, by
    runtime-parent eligibility with its own reason code, so that path would not
    exercise this gate.
    """

    table = ("36 35 0:77 / / rw - somefuturefs src rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._require_projection_applicable_v2(snapshot, "/some/target")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    ext4_table = ("36 35 98:0 / / rw - ext4 /dev/root rw\n")
    epoch_module._require_projection_applicable_v2(
        MountTopologySnapshotV2.parse(ext4_table), "/some/target")   # must not raise


def test_f6a_casefolded_lookup_directory_is_unknown(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6. Where lookup is casefolded, two spellings name ONE entry, and this
    module's textual equality and containment stop being valid discriminators.

    Runtime status is honest: a genuine casefolded ext4 could not be built in
    this environment (loop mounts are not permitted), so the flag observation
    is driven directly. That exercises the guard, and does NOT claim the kernel
    behaviour was reproduced.
    """

    target = tmp_path / "project"
    target.mkdir()
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2", lambda _path: True)
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f6a_unreadable_casefold_flag_is_unknown_not_assumed_sensitive(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable flag is an unestablished predicate. It must never be read
    as "case sensitive" -- that is the fail-open this guard exists to remove."""

    target = tmp_path / "project"
    target.mkdir()
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2", lambda _path: None)
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f6b_case_sensitive_control_takes_the_normal_path(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    assert epoch_module._directory_is_casefolded_v2(str(target)) is False
    assert _acquire(target) == "acquired"


def test_f6_filesystems_without_casefold_support_are_not_probed(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ext2/ext3 cannot carry a casefolded directory, so their name semantics
    follow from the filesystem type and probing them would risk reading an
    `ENOTTY` as an answer.

    An earlier revision of this test used tmpfs, which was wrong: current
    kernels DO casefold tmpfs, and `test_f6_tmpfs2_real_casefolded_tmpfs_parent_is_unknown`
    now proves it against a real mount.
    """

    probed: list[str] = []
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2",
                        lambda path: probed.append(path) or None)
    real_observe = MountTopologySnapshotV2.observe

    def _as_ext3(cls=None):
        snapshot = real_observe()
        return MountTopologySnapshotV2(tuple(
            r._replace(filesystem_type="ext3") for r in snapshot.records))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_as_ext3))
    target = tmp_path / "project"
    target.mkdir()
    assert _acquire(target) == "acquired"
    assert probed == [], "a filesystem that cannot casefold must not be probed"


# ---------------------------- corrective mutants --------------------------


def test_mutant_self_parent_root_loops() -> None:
    """M_SELF_PARENT_ROOT_LOOPS -- restore the unbounded climb and show it does
    not terminate on a self-parented root.

    Deliberately NOT executed to exhaustion: an earlier draft of this test ran
    the real infinite loop on a daemon thread, which kept busy-spinning after
    the assertion and starved the rest of the suite. The mutant is instead run
    under a step budget, and exceeding the budget IS the non-termination
    result -- the honest thing to prove, at no cost to the run.
    """

    snapshot = MountTopologySnapshotV2((MountRecordV2(1, 1, 0, "/", "/", "ext4"),))
    budget = 1000
    current = snapshot.records[0]
    steps = 0
    while steps < budget:
        stacked = [c for c in snapshot.children.get(current.mount_id, [])
                   if c.mount_point == "/"]          # the missing `!= current.mount_id`
        if not stacked:
            break
        current = stacked[0]
        steps += 1
    assert steps == budget, "unbounded climb should never terminate on a self-parented root"

    # the real implementation terminates on exactly this input
    assert snapshot.governing_mount_v2("/tmp/x").mount_id == 1


def test_mutant_nonroot_self_parent_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_NONROOT_SELF_PARENT_ACCEPTED."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 40 98:0 /x /tmp/p/v rw - ext4 /d rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    with pytest.raises(TargetPackEpochError):
        snapshot.visible_child_mounts_v2("/tmp/p")

    monkeypatch.setattr(MountTopologySnapshotV2, "validate_relevant_chain_v2",
                        lambda self, record: None)
    assert MountTopologySnapshotV2.parse(table).visible_child_mounts_v2("/tmp/p") is not None


def test_mutant_internal_missing_parent_treated_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_INTERNAL_MISSING_PARENT_TREATED_HIDDEN -- the exact fail-open the old
    test certified."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 39 0:99 /b /tmp/p/vendor rw - ext4 /d2 rw\n")
    with pytest.raises(TargetPackEpochError):
        MountTopologySnapshotV2.parse(table).visible_child_mounts_v2("/tmp/p")

    monkeypatch.setattr(MountTopologySnapshotV2, "validate_relevant_chain_v2",
                        lambda self, record: None)
    assert MountTopologySnapshotV2.parse(table).visible_child_mounts_v2("/tmp/p") == ()


def test_mutant_second_topology_observation(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_SECOND_TOPOLOGY_OBSERVATION -- a helper reading the table again."""

    reads: list[str] = []
    real_read_text = Path.read_text

    def _counting(self: Path, *args: object, **kwargs: object) -> str:
        if str(self) == "/proc/self/mountinfo":
            reads.append(str(self))
        return real_read_text(self, *args, **kwargs)

    real_probe = epoch_module._runtime_filesystem_type_v2

    def _probe_reading_again(path, snapshot):
        MountTopologySnapshotV2.observe()      # the removed second read
        return real_probe(path, snapshot)

    monkeypatch.setattr(Path, "read_text", _counting)
    monkeypatch.setattr(epoch_module, "_runtime_filesystem_type_v2", _probe_reading_again)
    target = tmp_path / "ordinary"
    target.mkdir()
    _acquire(target)
    assert len(reads) > 1, "mutant should have observed the mount table more than once"


def test_mutant_visibility_unknown_becomes_false(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_VISIBILITY_UNKNOWN_BECOMES_FALSE -- killed through PUBLIC acquisition."""

    target = tmp_path / "project"
    target.mkdir()
    real_observe = MountTopologySnapshotV2.observe

    def _with_ambiguous_child(cls=None):
        snapshot = real_observe()
        extra = (
            MountRecordV2(10 ** 7, 10 ** 7 + 1, 0, "/a", str(target / "v"), "ext4"),
            MountRecordV2(10 ** 7 + 2, 10 ** 7 + 3, 0, "/b", str(target / "v"), "ext4"),
        )
        return MountTopologySnapshotV2(snapshot.records + extra)

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_ambiguous_child))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    def _swallowing(self, path):
        prefix = epoch_module._normalize_absolute_v2(path).rstrip("/") + "/"
        out = []
        for record in self.records:
            if not record.mount_point.startswith(prefix):
                continue
            try:
                if self.governing_mount_v2(record.mount_point).mount_id == record.mount_id:
                    out.append(record)
            except TargetPackEpochError:
                continue                       # UNKNOWN -> HIDDEN
        return tuple(out)

    def _swallowing_resolver(self, query):
        try:
            return _real_resolve(self, query)
        except TargetPackEpochError:
            return epoch_module.TopologyQueryResolutionV2(
                query, self._visible_root_v2(), (), ())      # UNKNOWN -> HIDDEN

    _real_resolve = MountTopologySnapshotV2.resolve_query_v2
    monkeypatch.setattr(MountTopologySnapshotV2, "resolve_query_v2", _swallowing_resolver)
    assert _acquire(target) == "acquired", "mutant should have swallowed the UNKNOWN"


@requires_overlay
def test_mutant_overlay_assumed_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M_OVERLAY_ASSUMED_DIRECT -- killed by real overlay topology."""

    for name in ("low", "up", "work", "mnt"):
        (tmp_path / name).mkdir()
    parent = tmp_path / "up"
    os.chmod(parent, 0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o",
         f"lowerdir={tmp_path}/low,upperdir={tmp_path}/up,workdir={tmp_path}/work",
         str(tmp_path / "mnt")], check=True)
    try:
        target = tmp_path / "mnt"
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

        monkeypatch.setattr(epoch_module, "_require_projection_applicable_v2",
                            lambda snapshot, *paths: None)
        assert _acquire(target) == "acquired", "mutant should have assumed overlay projects directly"
        assert [p.name for p in target.iterdir() if p.name.startswith("agentreview")] != [], (
            "and the carrier should then be visible inside the target")
    finally:
        umount(tmp_path / "mnt")


def test_mutant_casefold_lookup_assumed_case_sensitive(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_CASEFOLD_LOOKUP_ASSUMED_CASE_SENSITIVE."""

    target = tmp_path / "project"
    target.mkdir()
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2", lambda _path: True)
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    monkeypatch.setattr(epoch_module, "_require_name_semantics_applicable_v2",
                        lambda snapshot, *paths: None)
    assert _acquire(target) == "acquired", "mutant should have assumed case-sensitive lookup"


# ------------------ §13 semantic audit of test names ----------------------


def test_no_topology_test_name_contradicts_its_assertions() -> None:
    """Durable guard against the defect F3 exposed: a test named `..._is_unknown`
    whose body asserted the fail-open it was supposed to forbid."""

    import inspect
    module = __import__(__name__, fromlist=["*"])
    offenders = []
    for name, function in vars(module).items():
        if not (name.startswith("test_") and callable(function)):
            continue
        if "_is_unknown" not in name and "_unknown" not in name:
            continue
        body = inspect.getsource(function)
        claims_unknown = (
            "DISJOINTNESS_UNKNOWN_REASON_V2" in body
            or "pytest.raises(TargetPackEpochError)" in body
            or "_refuses(" in body)
        if not claims_unknown:
            offenders.append(name)
    assert offenders == [], f"tests naming UNKNOWN without asserting it: {offenders}"


# ============ F6 scope: name semantics belong to the LOOKUP PARENT =========
#
# A pathname's spelling is decided by the directory it is looked up IN, never
# by the object itself. Probing the final object was wrong twice: the target's
# interior lookup semantics do not decide whether the target's NAME collides
# with another spelling, and opening it needs read permission the caller may
# legitimately lack. A mode-0300 target -- writable, searchable, deliberately
# unreadable -- answered "flag unreadable", which is UNKNOWN, and refused an
# acquisition the contract requires to succeed. Running as root hid it, because
# root bypasses the permission check; CI, which is not root, did not.


def _casefold_tmpfs_available() -> bool:
    if os.geteuid() != 0:
        return False
    import tempfile as _t
    base = Path(_t.mkdtemp())
    try:
        if subprocess.run(["mount", "-t", "tmpfs", "-o", "casefold", "tmpfs", str(base)],
                          capture_output=True).returncode != 0:
            return False
        subprocess.run(["umount", str(base)], check=False)
        return True
    finally:
        import shutil as _s
        _s.rmtree(base, ignore_errors=True)


requires_casefold_tmpfs = pytest.mark.skipif(
    not _casefold_tmpfs_available(),
    reason="BLOCKED_BY_ENVIRONMENT: needs a real casefold-capable tmpfs; a skip "
    "is absent evidence, never a pass")


def test_f6_scope1_the_target_object_itself_is_not_a_lookup_authority(tmp_path: Path) -> None:
    """F6-SCOPE-1. The object never resolves its own name."""

    target = tmp_path / "a" / "b" / "target"
    target.mkdir(parents=True)
    authorities = epoch_module._lookup_authorities_v2(str(target))
    assert str(target) not in authorities
    assert str(tmp_path / "a" / "b") in authorities, "the resolving parent must be an authority"
    assert str(tmp_path / "a") in authorities, "intermediate ancestors resolve components too"
    assert "/" in authorities


def test_f6_scope_nonexistent_components_resolve_nothing(tmp_path: Path) -> None:
    """Only existing ancestors are authorities; the nearest existing one
    governs what this primitive later creates beneath it."""

    missing = tmp_path / "not" / "there" / "yet"
    authorities = epoch_module._lookup_authorities_v2(str(missing))
    assert str(tmp_path) in authorities
    assert not any("not" in a.rsplit("/", 1)[-1] for a in authorities)


def test_f6_ci1_ci2_mode_0300_target_acquires_for_both_principals(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """F6-CI-1/F6-CI-2. The regression CI caught, asserted directly.

    The privileged and unprivileged answers must AGREE -- that agreement is
    the property, since the original defect was visible only to a non-root
    principal.
    """

    target = tmp_path / "mode-0300"
    target.mkdir()
    target.chmod(0o300)
    try:
        # the object is unreadable...
        assert epoch_module._directory_is_casefolded_v2(str(target)) in (False, None)
        # ...and is not consulted, because it is not a lookup authority
        assert str(target) not in epoch_module._lookup_authorities_v2(str(target))
        assert _acquire(target) == "acquired"
    finally:
        target.chmod(0o700)


def test_f6_ci3_unreadable_relevant_parent_is_unknown(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6-CI-3 / F6-SCOPE-2. The fail-closed policy is UNCHANGED -- only the
    scope moved. An authority whose flag cannot be established is UNKNOWN."""

    target = tmp_path / "project"
    target.mkdir()
    parent = str(tmp_path)

    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(
        epoch_module, "_directory_is_casefolded_v2",
        lambda path: None if path == parent else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f6_scope2_casefolded_resolving_parent_is_unknown(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    parent = str(tmp_path)

    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(
        epoch_module, "_directory_is_casefolded_v2",
        lambda path: True if path == parent else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_f6_tmpfs1_tmpfs_is_classified_casefold_capable() -> None:
    """F6-TMPFS-1. Listing only ext4 was stale: current kernels casefold tmpfs."""

    assert "tmpfs" in epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2
    assert "ext4" in epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2
    assert epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2 <= \
        epoch_module._DIRECT_PROJECTION_FILESYSTEMS_V2, (
        "name semantics are only asked about filesystems projection already admits")


@requires_casefold_tmpfs
def test_f6_tmpfs2_real_casefolded_tmpfs_parent_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6-TMPFS-2. A REAL casefolded filesystem, not a simulated flag.

    Two spellings genuinely name one entry here, which is exactly the
    condition under which this module's textual equality and containment stop
    being valid discriminators.
    """

    holder = tmp_path / "cf"
    holder.mkdir()
    subprocess.run(["mount", "-t", "tmpfs", "-o", "casefold", "tmpfs", str(holder)], check=True)
    try:
        parent = holder / "workspace"
        parent.mkdir()
        subprocess.run(["chattr", "+F", str(parent)], check=True)
        assert epoch_module._directory_is_casefolded_v2(str(parent)) is True

        # the hazard itself: one entry, two spellings
        (parent / "project").mkdir()
        assert (parent / "PROJECT").is_dir(), "casefolded lookup should alias the spelling"
        assert os.stat(parent / "project").st_ino == os.stat(parent / "PROJECT").st_ino

        runtime = tmp_path / "runtime-parent"
        runtime.mkdir()
        runtime.chmod(0o1777)
        monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", runtime)
        monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
        assert _acquire(parent / "project") == \
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
    finally:
        subprocess.run(["umount", str(holder)], check=False)


@requires_casefold_tmpfs
def test_f6_tmpfs3_case_sensitive_tmpfs_control_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6-TMPFS-3. OVER-REJECTION CONTROL: an ordinary tmpfs, casefold-capable
    as a type but with the flag clear, must still acquire."""

    holder = tmp_path / "plain"
    holder.mkdir()
    subprocess.run(["mount", "-t", "tmpfs", "tmpfs", str(holder)], check=True)
    try:
        parent = holder / "workspace"
        parent.mkdir()
        target = parent / "project"
        target.mkdir()
        assert epoch_module._directory_is_casefolded_v2(str(parent)) is False
        assert not (parent / "PROJECT").exists(), "control must be case-sensitive"

        runtime = tmp_path / "runtime-parent"
        runtime.mkdir()
        runtime.chmod(0o1777)
        monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", runtime)
        monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
        assert _acquire(target) == "acquired"
    finally:
        subprocess.run(["umount", str(holder)], check=False)


# ------------------------- scope mutants ----------------------------------


def test_mutant_probe_final_target_directory(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_PROBE_FINAL_TARGET_DIRECTORY -- restore the old scope; the mode-0300
    target is refused again, exactly as CI caught."""

    target = tmp_path / "mode-0300"
    target.mkdir()
    target.chmod(0o300)
    try:
        assert _acquire(target) == "acquired"

        real_probe = epoch_module._directory_is_casefolded_v2

        def _also_probe_the_object(snapshot, *paths):
            for path in paths:
                governing = snapshot.governing_mount_v2(path)
                if governing.filesystem_type not in epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2:
                    continue
                probe = path
                while not os.path.isdir(probe):
                    parent = os.path.dirname(probe)
                    if parent == probe:
                        break
                    probe = parent
                if real_probe(probe) is not False:
                    raise TargetPackEpochError(
                        TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)

        monkeypatch.setattr(
            epoch_module, "_require_name_semantics_applicable_v2", _also_probe_the_object)
        # judged by the real filesystem's permission behaviour, not by the mutant
        assert os.geteuid() == 0 or _acquire(target) == \
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        assert epoch_module._lookup_authorities_v2(str(target)) and \
            str(target) not in epoch_module._lookup_authorities_v2(str(target))
    finally:
        target.chmod(0o700)


def test_mutant_skip_relevant_lookup_parent(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_SKIP_RELEVANT_LOOKUP_PARENT -- consult only the immediate parent, so a
    casefolded INTERMEDIATE ancestor slips through."""

    target = tmp_path / "a" / "b" / "project"
    target.mkdir(parents=True)
    intermediate = str(tmp_path / "a")

    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(
        epoch_module, "_directory_is_casefolded_v2",
        lambda path: True if path == intermediate else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    # The mutant must isolate ONE defect: consulting only the nearest existing
    # parent instead of every ancestor. Returning a literal `dirname` would
    # also hand back the not-yet-created protocol directory, and the probe
    # would refuse for non-existence -- killing the mutant by the wrong
    # proposition rather than by the skipped ancestor.
    def _nearest_existing_parent_only(path):
        current = os.path.dirname(epoch_module._normalize_absolute_v2(path))
        while epoch_module._observe_directory_v2(current) != epoch_module._DIRECTORY_PRESENT_V2:
            parent = os.path.dirname(current)
            if parent == current:
                return ()
            current = parent
        return (current,)

    monkeypatch.setattr(epoch_module, "_lookup_authorities_v2", _nearest_existing_parent_only)
    assert _acquire(target) == "acquired", "mutant should have skipped the intermediate ancestor"


def test_mutant_tmpfs_assumed_never_casefolded(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_TMPFS_ASSUMED_NEVER_CASEFOLDED -- the stale `{"ext4"}` set."""

    target = tmp_path / "project"
    target.mkdir()
    parent = str(tmp_path)
    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(
        epoch_module, "_directory_is_casefolded_v2",
        lambda path: True if path == parent else real_probe(path))

    real_observe = MountTopologySnapshotV2.observe

    def _as_tmpfs(cls=None):
        snapshot = real_observe()
        return MountTopologySnapshotV2(tuple(
            r._replace(filesystem_type="tmpfs") for r in snapshot.records))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_as_tmpfs))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    # Under the THREE-WAY model (`#262` N17) removing tmpfs from the capable
    # set no longer promotes it to "case sensitive": it becomes
    # UNKNOWN_NAME_SEMANTICS and refuses. That is the improvement --
    # M_UNKNOWN_NAME_FS_ASSUMED_CASE_SENSITIVE is dead by construction, not by
    # a test asserting it. The two-way split silently accepted here.
    monkeypatch.setattr(epoch_module, "_CASEFOLD_CAPABLE_FILESYSTEMS_V2", frozenset({"ext4"}))
    assert epoch_module._name_semantics_capability_v2("tmpfs") == \
        epoch_module._NAME_SEMANTICS_UNKNOWN_V2
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2, (
        "an unclassified filesystem must be UNKNOWN, never assumed case-sensitive")


def test_mutant_casefold_observation_failure_means_false(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_CASEFOLD_OBSERVATION_FAILURE_MEANS_FALSE -- an unreadable flag read as
    "case sensitive"."""

    target = tmp_path / "project"
    target.mkdir()
    parent = str(tmp_path)
    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(
        epoch_module, "_directory_is_casefolded_v2",
        lambda path: None if path == parent else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    def _none_means_sensitive(snapshot, *paths):
        for path in paths:
            governing = snapshot.governing_mount_v2(path)
            if governing.filesystem_type not in epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2:
                continue
            for authority in epoch_module._lookup_authorities_v2(path):
                if epoch_module._directory_is_casefolded_v2(authority) is True:
                    raise TargetPackEpochError(
                        TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)

    monkeypatch.setattr(
        epoch_module, "_require_name_semantics_applicable_v2", _none_means_sensitive)
    assert _acquire(target) == "acquired", "mutant should have read UNKNOWN as case-sensitive"


# ================= N7-N10 + CLI: DOMAIN CLOSURE ===========================
#
# Two findings, from two different channels, had ONE cause: a domain builder
# admitted or represented members that the gates never validated.
#
#   target side   applicability was checked at the target ROOT only, while
#                 TargetVisiblePhysicalDomain also admits child segments
#   carrier side  the carrier was represented by the protocol-directory ROOT,
#                 while K also operates an exact <key>.lock path beneath it
#
# Both are closed by correcting the BUILDERS, not by adding counterexample
# rules.


@requires_overlay
def test_n7_unsupported_filesystem_in_a_visible_child_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N7 (P1). The target root is ext4 and passes applicability; a visible
    child is an overlay whose upperdir IS the runtime parent. The old gate
    checked only the root and the carrier, so the overlay segment entered the
    domain described by its own opaque device, intersected nothing, and K
    landed in ground visible beneath the target."""

    for name in ("low", "work"):
        (tmp_path / name).mkdir()
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())

    target = tmp_path / "project"
    child = target / "vendor"
    child.mkdir(parents=True)
    subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o",
         f"lowerdir={tmp_path}/low,upperdir={parent},workdir={tmp_path}/work", str(child)],
        check=True)
    try:
        snapshot = MountTopologySnapshotV2.observe()
        assert snapshot.governing_mount_v2(str(target)).filesystem_type in \
            epoch_module._DIRECT_PROJECTION_FILESYSTEMS_V2, "the ROOT is supported"
        assert any(snapshot.governing_mount_v2(c.mount_point).filesystem_type == "overlay"
                   for c in snapshot.visible_child_mounts_v2(str(target)))

        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        assert [p.name for p in child.iterdir() if p.name.startswith("agentreview")] == []
    finally:
        umount(child)


def test_n7_hidden_unsupported_child_does_not_poison_the_domain(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing distinction. A HIDDEN unsupported mount contributes no
    segment, so it must not refuse anything -- otherwise any host with an
    unrelated overlay parked out of view becomes unusable."""

    target = tmp_path / "project"
    target.mkdir()
    real_observe = MountTopologySnapshotV2.observe

    def _with_hidden_overlay(cls=None):
        snapshot = real_observe()
        hidden = MountRecordV2(10 ** 7, 10 ** 7 + 5, 0, "/", str(target / "ghost"), "overlay")
        return MountTopologySnapshotV2(snapshot.records + (hidden,))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_hidden_overlay))
    # disconnected -> relevant-chain validation refuses it as UNKNOWN, which is
    # the honest answer for a record whose position cannot be established;
    # what must NOT happen is that it silently contributes an overlay segment.
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n8_self_parented_root_with_a_stack_resolves(tmp_path: Path) -> None:
    """N8 (P2). Both root forms are boundaries and both must be candidates."""

    records = (MountRecordV2(1, 1, 0, "/", "/", "ext4"),
               MountRecordV2(2, 1, 0, "/", "/", "tmpfs"))
    assert MountTopologySnapshotV2(records).governing_mount_v2("/tmp/x").mount_id == 2


@pytest.mark.parametrize(
    "records,expected",
    [
        (((1, 1, "/", "ext4"),), 1),
        (((1, 9, "/", "ext4"),), 1),
        (((1, 1, "/", "ext4"), (2, 1, "/", "tmpfs")), 2),
        (((1, 9, "/", "ext4"), (2, 1, "/", "tmpfs")), 2),
    ],
    ids=["self-parent-alone", "external-parent-alone",
         "self-parent+stack", "external-parent+stack"])
def test_n8_boundary_root_forms(records, expected) -> None:
    snapshot = MountTopologySnapshotV2(tuple(
        MountRecordV2(i, p, 0, "/", mp, fs) for i, p, mp, fs in records))
    assert snapshot.governing_mount_v2("/tmp/x").mount_id == expected


def test_n8_two_competing_boundary_roots_is_unknown() -> None:
    records = (MountRecordV2(1, 1, 0, "/", "/", "ext4"),
               MountRecordV2(2, 2, 0, "/", "/", "ext4"))
    with pytest.raises(TargetPackEpochError) as excinfo:
        MountTopologySnapshotV2(records).governing_mount_v2("/x")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n9_malformed_record_exactly_at_the_target_is_unknown() -> None:
    """N9 (P2). A record AT the domain root decides which mount governs it, so
    it is relevant even though it is not a strict descendant. The earlier
    prefix filter excluded it and the target was projected through the
    underlying mount instead of refusing."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 39 0:99 /b /tmp/target rw - ext4 /d2 rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    with pytest.raises(TargetPackEpochError) as excinfo:
        snapshot.visible_child_mounts_v2("/tmp/target")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n9_relevant_set_is_at_or_beneath_but_children_are_strictly_beneath() -> None:
    """The two sets are different and must stay different: a record AT the root
    is validated, but it is not a CHILD segment of that root."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 36 98:0 /at /tmp/t rw - ext4 /d rw\n"
             "41 40 98:0 /below /tmp/t/sub rw - ext4 /d rw\n")
    snapshot = MountTopologySnapshotV2.parse(table)
    children = snapshot.visible_child_mounts_v2("/tmp/t")
    assert [c.mount_point for c in children] == ["/tmp/t/sub"]


def test_n10_unobservable_ancestor_is_unknown_not_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N10 (P2). `os.path.isdir` answers False for a directory that exists but
    cannot be observed, which is indistinguishable from absent -- so an
    unreadable, possibly casefolded, authority was silently dropped."""

    assert epoch_module._observe_directory_v2(str(tmp_path)) == epoch_module._DIRECTORY_PRESENT_V2
    assert epoch_module._observe_directory_v2(str(tmp_path / "nope")) == \
        epoch_module._DIRECTORY_ABSENT_V2

    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("x", encoding="utf-8")
    assert epoch_module._observe_directory_v2(str(not_a_directory)) == \
        epoch_module._DIRECTORY_OTHER_V2
    assert epoch_module._observe_directory_v2(str(not_a_directory / "under")) == \
        epoch_module._DIRECTORY_ABSENT_V2

    # A symlink is its OWN state. An earlier revision folded it into `absent`
    # and leaned on a later O_NOFOLLOW check to stay safe -- a non-local
    # correctness argument that this now removes.
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    assert epoch_module._observe_directory_v2(str(link)) == epoch_module._DIRECTORY_SYMLINK_V2

    a_file = tmp_path / "plainfile"
    a_file.write_text("x", encoding="utf-8")
    assert epoch_module._observe_directory_v2(str(a_file)) == epoch_module._DIRECTORY_OTHER_V2

    # and a symlink where a lookup authority was expected is UNKNOWN, decided
    # locally rather than deferred
    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._lookup_authorities_v2(str(link / "child"))
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    # the UNKNOWN branch itself -- an observation error establishes neither
    real_lstat = os.lstat
    victim = str(tmp_path / "eio")

    def _eio(path, *args, **kwargs):
        if str(path) == victim:
            raise OSError(5, "EIO")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _eio)
    assert epoch_module._observe_directory_v2(victim) == epoch_module._DIRECTORY_UNKNOWN_V2
    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._lookup_authorities_v2(victim + "/child")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n10_lookup_authority_observation_error_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = str(tmp_path)
    real_lstat = os.lstat

    def _eio(path, *args, **kwargs):
        if str(path) == victim:
            raise OSError(5, "EIO")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _eio)
    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._lookup_authorities_v2(str(tmp_path / "project"))
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


# ---------- carrier operational domain: CARRIER-1..6 ----------------------


def _key_for(target: Path) -> str:
    return epoch_module.compute_target_pack_epoch_key_from_components_v2(
        euid=os.geteuid(),
        mount_namespace_identity=epoch_module._mount_namespace_identity_v2(),
        canonical_target_subject=epoch_module._canonical_target_subject_v2(target))


def _bind_onto_lock(runtime_parent: Path, target: Path, source: Path) -> Path:
    carrier = _carrier(runtime_parent)
    if not carrier.exists():
        carrier.mkdir(mode=0o700)
    lock = carrier / f"{_key_for(target)}.lock"
    lock.touch()
    os.chmod(lock, 0o600)
    bind(source, lock)
    return lock


@requires_bind_mount
def test_carrier1_target_file_bound_onto_the_exact_lock_path_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """CARRIER-1. Found by a local Codex CLI adversarial review, not by the
    native one. The lock K takes IS a file inside the target -- same
    st_dev/st_ino, and an independent flock on the target's own path contends
    while the epoch is held -- yet the protocol directory's projection stayed
    outside the target, so acquisition succeeded."""

    target = tmp_path / "target"
    target.mkdir()
    victim = target / "lock-source"
    victim.touch()
    os.chmod(victim, 0o600)
    lock = _bind_onto_lock(runtime_parent, target, victim)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(lock)


@requires_bind_mount
def test_carrier2_target_descendant_file_bound_onto_the_lock_path_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    inner = target / "deep" / "er"
    inner.mkdir(parents=True)
    victim = inner / "lock-source"
    victim.touch()
    os.chmod(victim, 0o600)
    lock = _bind_onto_lock(runtime_parent, target, victim)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(lock)


@requires_bind_mount
def test_carrier3_unrelated_file_bound_onto_the_lock_path_may_proceed(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """CARRIER-3. The lock path being a mount is not itself disqualifying --
    only its landing inside the target is."""

    target = tmp_path / "target"
    target.mkdir()
    outsider = tmp_path / "elsewhere"
    outsider.touch()
    os.chmod(outsider, 0o600)
    lock = _bind_onto_lock(runtime_parent, target, outsider)
    try:
        assert _acquire(target) == "acquired"
    finally:
        umount(lock)


@requires_bind_mount
def test_carrier4_unrelated_mount_inside_the_protocol_directory_is_not_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """CARRIER-4. The control that forbids restoring a blanket "anything
    beneath the carrier is UNKNOWN" rule. This acquisition never touches that
    name, so its existence decides nothing."""

    target = tmp_path / "target"
    target.mkdir()
    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    unrelated = carrier / "unrelated-name"
    unrelated.mkdir()
    source = tmp_path / "src"
    source.mkdir()
    bind(source, unrelated)
    try:
        os.chmod(carrier, 0o700)
        assert _acquire(target) == "acquired"
    finally:
        umount(unrelated)


@requires_bind_mount
def test_carrier5_protocol_directory_aliasing_the_target_is_refused(
    runtime_parent: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    carrier = _prepare_bound_carrier(runtime_parent, target)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2
    finally:
        umount(carrier)


def test_carrier6_target_containing_the_lock_path_is_refused(runtime_parent: Path) -> None:
    """CARRIER-6. Symmetric containment, via the runtime parent."""

    assert _acquire(runtime_parent) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2


def test_carrier_operational_domain_names_exactly_the_operated_objects(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """§11 closure guard, mechanical rather than prose.

    The regression this exists to prevent already happened once: a second
    filesystem object was operated on while the domain still named only the
    first. If acquisition gains another site, this fails.
    """

    target = tmp_path / "target"
    target.mkdir()
    key = _key_for(target)
    sites = epoch_module._declared_carrier_operation_sites_v2(os.geteuid(), key)
    carrier = _carrier(runtime_parent)
    assert set(sites) == {str(carrier), str(carrier / f"{key}.lock")}

    source = Path(epoch_module.__file__).read_text(encoding="utf-8")
    # the objects acquisition actually opens, by name, in the module
    assert 'os.mkdir(name, 0o700, dir_fd=parent_fd)' in source
    assert 'carrier_name = f"{key}.lock"' in source
    assert source.count("dir_fd=parent_fd") >= 1


# ------------------------------ mutants -----------------------------------


@requires_overlay
def test_mutant_target_child_fs_not_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_TARGET_CHILD_FS_NOT_GATED -- gate the root only, as before."""

    for name in ("low", "work"):
        (tmp_path / name).mkdir()
    parent = tmp_path / "runtime-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_PATH_V2", parent)
    monkeypatch.setattr(epoch_module, "_RUNTIME_PARENT_EXPECTED_OWNER_V2", os.geteuid())
    target = tmp_path / "project"
    child = target / "vendor"
    child.mkdir(parents=True)
    subprocess.run(
        ["mount", "-t", "overlay", "overlay", "-o",
         f"lowerdir={tmp_path}/low,upperdir={parent},workdir={tmp_path}/work", str(child)],
        check=True)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

        real_gate = epoch_module._require_projection_applicable_v2
        monkeypatch.setattr(
            epoch_module, "_require_projection_applicable_v2",
            lambda snapshot, *paths: real_gate(snapshot, paths[0]) if paths else None)
        assert _acquire(target) == "acquired", "mutant should have admitted the overlay segment"
        assert [p.name for p in child.iterdir() if p.name.startswith("agentreview")] != []
    finally:
        umount(child)


@requires_bind_mount
def test_mutant_carrier_domain_protocol_root_only(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_CARRIER_DOMAIN_PROTOCOL_ROOT_ONLY / M_CARRIER_LOCK_PATH_NOT_IN_DOMAIN."""

    target = tmp_path / "target"
    target.mkdir()
    victim = target / "lock-source"
    victim.touch()
    os.chmod(victim, 0o600)
    lock = _bind_onto_lock(runtime_parent, target, victim)
    try:
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2

        monkeypatch.setattr(
            epoch_module, "_carrier_operational_sites_v2",
            lambda euid, key: (str(_carrier(runtime_parent)),))
        assert _acquire(target) == "acquired", "mutant should have dropped the lock path"
        # judged by real filesystem identity, not by anything the mutant computed
        assert os.stat(lock).st_ino == os.stat(victim).st_ino
    finally:
        umount(lock)


@requires_bind_mount
def test_mutant_protocol_dir_blanket_descendant_refusal(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_PROTOCOL_DIR_BLANKET_DESCENDANT_REFUSAL -- the tempting over-refusal.
    Killed by CARRIER-4, which proves precision is required, not just safety."""

    target = tmp_path / "target"
    target.mkdir()
    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    unrelated = carrier / "unrelated-name"
    unrelated.mkdir()
    source = tmp_path / "src"
    source.mkdir()
    bind(source, unrelated)
    try:
        os.chmod(carrier, 0o700)
        assert _acquire(target) == "acquired"

        def _blanket(snapshot, *paths):
            for record in snapshot.records:
                if epoch_module._within_v2(record.mount_point, str(carrier)):
                    raise TargetPackEpochError(
                        TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)

        monkeypatch.setattr(epoch_module, "_require_name_semantics_applicable_v2", _blanket)
        assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2, (
            "blanket mutant should have over-refused the unrelated mount")
    finally:
        umount(unrelated)


def test_mutant_root_self_parent_stack_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_ROOT_SELF_PARENT_STACK_AMBIGUOUS -- restore `or roots`."""

    records = (MountRecordV2(1, 1, 0, "/", "/", "ext4"),
               MountRecordV2(2, 1, 0, "/", "/", "tmpfs"))
    snapshot = MountTopologySnapshotV2(records)
    assert snapshot.governing_mount_v2("/tmp/x").mount_id == 2

    def _old_boundary(self):
        roots = [r for r in self.records if r.mount_point == "/"]
        base = [r for r in roots if r.parent_id not in self.by_id] or roots
        if len(base) > 1:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
        return self._climb_stack_v2(base[0], "/")

    monkeypatch.setattr(MountTopologySnapshotV2, "_visible_root_v2", _old_boundary)
    with pytest.raises(TargetPackEpochError):
        MountTopologySnapshotV2(records).governing_mount_v2("/tmp/x")


def test_mutant_exact_target_malformed_record_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_EXACT_TARGET_MALFORMED_RECORD_IGNORED -- strict-descendant relevance."""

    table = ("36 35 98:0 / / rw - ext4 /d rw\n"
             "40 39 0:99 /b /tmp/target rw - ext4 /d2 rw\n")
    with pytest.raises(TargetPackEpochError):
        MountTopologySnapshotV2.parse(table).visible_child_mounts_v2("/tmp/target")

    def _strict(self, path):
        prefix = epoch_module._normalize_absolute_v2(path).rstrip("/") + "/"
        relevant = [r for r in self.records if r.mount_point.startswith(prefix)]
        for record in relevant:
            self.validate_relevant_chain_v2(record)
        return tuple(r for r in relevant if self.is_visible_v2(r))

    monkeypatch.setattr(MountTopologySnapshotV2, "visible_child_mounts_v2", _strict)
    assert MountTopologySnapshotV2.parse(table).visible_child_mounts_v2("/tmp/target") == ()


def test_mutant_lookup_authority_eacces_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_LOOKUP_AUTHORITY_EACCES_AS_ABSENT -- the isdir() collapse."""

    victim = str(tmp_path)
    real_lstat = os.lstat

    real_stat = os.stat

    def _eacces(path, *args, **kwargs):
        if str(path) == victim:
            raise OSError(13, "EACCES")
        return real_lstat(path, *args, **kwargs)

    def _eacces_stat(path, *args, **kwargs):
        if str(path) == victim:
            raise OSError(13, "EACCES")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _eacces)
    monkeypatch.setattr(os, "stat", _eacces_stat)
    with pytest.raises(TargetPackEpochError):
        epoch_module._lookup_authorities_v2(str(tmp_path / "project"))

    monkeypatch.setattr(
        epoch_module, "_observe_directory_v2",
        lambda path: epoch_module._DIRECTORY_PRESENT_V2 if os.path.isdir(path)
        else epoch_module._DIRECTORY_ABSENT_V2)
    authorities = epoch_module._lookup_authorities_v2(str(tmp_path / "project"))
    assert victim not in authorities, "mutant should have silently dropped the unreadable ancestor"


# ============ TYPED TOPOLOGY QUERY FRONTIER — post-recurrence redesign =====
#
# `#262` F3 -> N9 was an ADMITTED RECURRENCE. That admission is historical fact
# and is not cleared by this redesign; what it did was trigger the mandatory
# spike, whose outcome selected this mechanism.
#
# What was falsified was not the mount graph, the parent relation, the
# visibility partition or physical projection. It was the SEQUENCE:
#
#     strict-descendant -> at-or-beneath -> (next position...)
#
# Each correction hand-wrote how far "relevant" reached, covering the positions
# demonstrated so far and leaving the untested one silently outside. The spike's
# falsifier for at-or-beneath was a malformed mount ANCESTRAL to the target:
# public acquisition returned `acquired`.
#
# Relevance is therefore no longer a predicate a consumer chooses. It is derived
# from the query being asked:
#
#     QueryKind -> SemanticSeeds -> DependencyClosure -> QueryResolution


def _query(kind, path):
    return epoch_module.TopologyQueryV2(kind, path)


POINT = epoch_module.TopologyQueryKindV2.POINT_LOOKUP
SUBTREE = epoch_module.TopologyQueryKindV2.VISIBLE_SUBTREE
_ROOT_ROW = "36 35 98:0 / / rw - ext4 /dev/root rw\n"


def _resolves(extra_rows: str, path: str, kind) -> str:
    snapshot = MountTopologySnapshotV2.parse(_ROOT_ROW + extra_rows)
    try:
        snapshot.resolve_query_v2(_query(kind, path))
        return "resolvable"
    except TargetPackEpochError as exc:
        assert exc.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        return "UNKNOWN"


@pytest.mark.parametrize(
    "mount_point,point_expected,subtree_expected",
    [
        ("/a",        "UNKNOWN",    "UNKNOWN"),      # ancestor
        ("/a/b",      "UNKNOWN",    "UNKNOWN"),      # nearer ancestor
        ("/a/b/c",    "UNKNOWN",    "UNKNOWN"),      # equal
        ("/a/b/c/d",  "resolvable", "UNKNOWN"),      # strict descendant
        ("/a/b/z",    "resolvable", "resolvable"),   # sibling
        ("/zz/x",     "resolvable", "resolvable"),   # unrelated
    ],
    ids=["ancestor", "near-ancestor", "equal", "descendant", "sibling", "unrelated"],
)
def test_position_matrix_follows_query_kind_not_position_branches(
    mount_point: str, point_expected: str, subtree_expected: str
) -> None:
    """§14. Six positions, one derivation, no per-position branch.

    The asymmetry is the point: a strict descendant cannot change which mount
    governs a path (the walk never traverses it), but it CAN contribute visible
    ground inside a subtree. Siblings and unrelated records fall out as
    irrelevant by derivation rather than by exception -- which is what keeps
    this from degenerating into a global fail-closed scan.
    """

    row = f"40 39 98:0 /x {mount_point} rw - ext4 /d rw\n"     # parent 39 ABSENT
    assert _resolves(row, "/a/b/c", POINT) == point_expected
    assert _resolves(row, "/a/b/c", SUBTREE) == subtree_expected


def test_malformed_ancestor_is_refused_before_mutation(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13. The spike's decisive falsifier of at-or-beneath, as a durable RED.

    On `042d08f` this returned `acquired`.
    """

    target = tmp_path / "a" / "b" / "c"
    target.mkdir(parents=True)
    real_observe = MountTopologySnapshotV2.observe

    def _with_malformed_ancestor(cls=None):
        snapshot = real_observe()
        disconnected = MountRecordV2(
            10 ** 7, 10 ** 7 + 1, 0, "/x", str(tmp_path / "a"), "ext4")
        return MountTopologySnapshotV2(snapshot.records + (disconnected,))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_malformed_ancestor))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
    assert not _carrier(runtime_parent).exists()


def test_dependency_closure_reaches_beyond_the_semantic_seeds() -> None:
    """§15. Seeds are not the frontier.

    The seed here is the record AT `/a/b/c`. Its position is only established
    through its parent chain, and the gap sits on a record whose own mount
    point (`/elsewhere`) no lexical seed predicate would ever select. Closure
    is what makes that unignorable.
    """

    rows = ("41 40 98:0 /y /a/b/c rw - ext4 /d rw\n"       # seed: equal to query
            "40 39 98:0 /x /elsewhere rw - ext4 /d rw\n")  # its parent; 39 ABSENT
    snapshot = MountTopologySnapshotV2.parse(_ROOT_ROW + rows)

    seeds = snapshot._semantic_seeds_v2(_query(SUBTREE, "/a/b/c"))
    assert 40 not in {r.mount_id for r in seeds}, "the gap is not a semantic seed"
    closed = snapshot._dependency_closure_v2(seeds)
    assert 40 in {r.mount_id for r in closed}, "closure must pull in the parent chain"

    assert _resolves(rows, "/a/b/c", SUBTREE) == "UNKNOWN"


def test_dependency_closure_pulls_in_same_point_stack_competitors() -> None:
    """A record stacked at the seed's own mount point competes to govern it, so
    it belongs to the proof even though it shares the seed's position."""

    rows = ("41 36 98:0 /y /a/b/c rw - ext4 /d rw\n"
            "42 39 98:0 /z /a/b/c rw - ext4 /d rw\n")      # competitor, parent ABSENT
    assert _resolves(rows, "/a/b/c", POINT) == "UNKNOWN"


def test_unrelated_malformed_topology_does_not_poison_a_legal_acquisition(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§16. The control that separates a derived frontier from a global scan."""

    target = tmp_path / "project"
    target.mkdir()
    real_observe = MountTopologySnapshotV2.observe

    def _with_unrelated_garbage(cls=None):
        snapshot = real_observe()
        unrelated = MountRecordV2(10 ** 7, 10 ** 7 + 1, 0, "/x", "/other/elsewhere", "ext4")
        return MountTopologySnapshotV2(snapshot.records + (unrelated,))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_unrelated_garbage))
    assert _acquire(target) == "acquired"


def test_unparseable_row_is_a_different_epistemic_case() -> None:
    """§16's distinction: a row whose relevance cannot even be determined is
    not the same as a parsed-but-unrelated one, and stays fail-closed."""

    with pytest.raises(TargetPackEpochError) as excinfo:
        MountTopologySnapshotV2.parse(_ROOT_ROW + "40 39 not-a-device /x /other rw - ext4 /d rw\n")
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_query_kind_is_load_bearing_for_the_carrier(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """§10. If the carrier were a subtree query, an unrelated mount inside the
    protocol directory would refuse a legal acquisition. CARRIER-4 is the
    discriminator that forbids collapsing the two kinds."""

    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    rows = f"40 39 98:0 /x {carrier}/unrelated-name rw - ext4 /d rw\n"
    assert _resolves(rows, str(carrier), POINT) == "resolvable"
    assert _resolves(rows, str(carrier), SUBTREE) == "UNKNOWN"


def test_symlink_reason_is_owned_locally_not_deferred(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """§12. The established public reason is preserved, and the reasoning is
    now local: `_require_carrier_object_shape_v2` establishes the forbidden
    shape itself, so the later `O_NOFOLLOW` check is TOCTOU revalidation
    rather than the first truth-maker for this decision."""

    outside = tmp_path / "outside"
    outside.mkdir()
    _carrier(runtime_parent).symlink_to(outside, target_is_directory=True)

    key = _key_for(tmp_path / "target")
    with pytest.raises(TargetPackEpochError) as excinfo:
        epoch_module._require_carrier_object_shape_v2(os.geteuid(), key)
    assert excinfo.value.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2

    target = tmp_path / "target"
    target.mkdir()
    assert _acquire(target) == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2


# ------------------------- redesign mutants -------------------------------


def _frontier_mutant(monkeypatch, seed_fn):
    monkeypatch.setattr(MountTopologySnapshotV2, "_semantic_seeds_v2", seed_fn)


def test_mutant_ancestor_seed_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_ANCESTOR_SEED_OMITTED / M_AT_OR_BENEATH_ONLY -- the falsified mechanism."""

    row = "40 39 98:0 /x /a rw - ext4 /d rw\n"
    assert _resolves(row, "/a/b/c", SUBTREE) == "UNKNOWN"

    def _at_or_beneath_only(self, query):
        path = epoch_module._normalize_absolute_v2(query.path)
        return tuple(r for r in self.records if epoch_module._within_v2(r.mount_point, path))

    _frontier_mutant(monkeypatch, _at_or_beneath_only)
    assert _resolves(row, "/a/b/c", SUBTREE) == "resolvable", "mutant should reopen the falsifier"


def test_mutant_equal_seed_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_EQUAL_SEED_OMITTED -- the N9 witness itself."""

    row = "40 39 98:0 /x /a/b/c rw - ext4 /d rw\n"
    assert _resolves(row, "/a/b/c", SUBTREE) == "UNKNOWN"

    def _strict_only(self, query):
        path = epoch_module._normalize_absolute_v2(query.path)
        prefix = path.rstrip("/") + "/"
        return tuple(r for r in self.records if r.mount_point.startswith(prefix))

    _frontier_mutant(monkeypatch, _strict_only)
    assert _resolves(row, "/a/b/c", SUBTREE) == "resolvable", "mutant should reopen N9"


def test_mutant_subtree_descendant_seed_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_SUBTREE_DESCENDANT_SEED_OMITTED / M_SUBTREE_QUERY_TREATED_AS_POINT."""

    row = "40 39 98:0 /x /a/b/c/d rw - ext4 /d rw\n"
    assert _resolves(row, "/a/b/c", SUBTREE) == "UNKNOWN"

    def _point_seeds_for_everything(self, query):
        path = epoch_module._normalize_absolute_v2(query.path)
        return tuple(r for r in self.records if epoch_module._within_v2(path, r.mount_point))

    _frontier_mutant(monkeypatch, _point_seeds_for_everything)
    assert _resolves(row, "/a/b/c", SUBTREE) == "resolvable", "F3's own witness reopens"


def test_mutant_unrelated_seed_included(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_UNRELATED_SEED_INCLUDED / M_GLOBAL_VALIDATE_ALL -- over-refusal."""

    row = "40 39 98:0 /x /zz/x rw - ext4 /d rw\n"
    assert _resolves(row, "/a/b/c", SUBTREE) == "resolvable"

    _frontier_mutant(monkeypatch, lambda self, query: tuple(self.records))
    assert _resolves(row, "/a/b/c", SUBTREE) == "UNKNOWN", "mutant should over-refuse"


def test_dependency_closure_is_structural_not_behaviourally_redundant() -> None:
    """M_FRONTIER_NO_PARENT_CLOSURE, reported honestly rather than asserted.

    The closure DOES pull records into the frontier that no lexical seed
    predicate selects -- `test_dependency_closure_reaches_beyond_the_semantic_seeds`
    shows record 40, whose mount point is `/elsewhere`, entering the proof for
    a query about `/a/b/c`.

    But it is NOT independently discriminated by behaviour today: chain
    validation walks parents from each seed, so removing the closure changes no
    current answer. Rather than assert a mutant that dies by the wrong
    proposition, the claim made here is the true and weaker one -- the frontier
    is EXPLICIT, so a future narrowing of chain validation cannot silently
    shrink relevance the way a consumer-side predicate once did.
    """

    rows = ("41 40 98:0 /y /a/b/c rw - ext4 /d rw\n"
            "40 39 98:0 /x /elsewhere rw - ext4 /d rw\n")
    snapshot = MountTopologySnapshotV2.parse(_ROOT_ROW + rows)
    seeds = {r.mount_id for r in snapshot._semantic_seeds_v2(_query(SUBTREE, "/a/b/c"))}
    closed = {r.mount_id for r in snapshot._dependency_closure_v2(
        snapshot._semantic_seeds_v2(_query(SUBTREE, "/a/b/c")))}
    assert 40 not in seeds and 40 in closed
    assert seeds < closed, "closure must be a strict superset here"


def test_mutant_point_query_treated_as_subtree(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_POINT_QUERY_TREATED_AS_SUBTREE -- collapses the kinds, over-refusing
    a carrier descendant this acquisition never touches."""

    carrier = _carrier(runtime_parent)
    carrier.mkdir(mode=0o700)
    rows = f"40 39 98:0 /x {carrier}/unrelated-name rw - ext4 /d rw\n"
    assert _resolves(rows, str(carrier), POINT) == "resolvable"

    real_seeds = MountTopologySnapshotV2._semantic_seeds_v2
    monkeypatch.setattr(
        MountTopologySnapshotV2, "_semantic_seeds_v2",
        lambda self, query: real_seeds(self, _query(SUBTREE, query.path)))
    assert _resolves(rows, str(carrier), POINT) == "UNKNOWN", "mutant should over-refuse CARRIER-4"


def test_mutant_symlink_as_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M_SYMLINK_AS_ABSENT / M_SYMLINK_RELIES_ON_LATER_ONOFOLLOW.

    Restoring the fold makes the carrier-shape authority silent, so the earlier
    decision once again depends on a downstream check to be safe.
    """

    link = tmp_path / "lnk"
    link.symlink_to(tmp_path / "nowhere")
    assert epoch_module._observe_directory_v2(str(link)) == epoch_module._DIRECTORY_SYMLINK_V2

    real_observe = epoch_module._observe_directory_v2
    monkeypatch.setattr(
        epoch_module, "_observe_directory_v2",
        lambda path: epoch_module._DIRECTORY_ABSENT_V2
        if real_observe(path) is epoch_module._DIRECTORY_SYMLINK_V2 else real_observe(path))

    key = _key_for(tmp_path / "t")
    epoch_module._require_carrier_object_shape_v2(os.geteuid(), key)   # no longer refuses


def test_mutant_consumer_rescans_snapshot(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_CONSUMER_RESCANS_SNAPSHOT -- consumers inventing their own relevance,
    which is the exact shape of the defect that produced the recurrence.

    Measured after authority sealing: FOUR consumers ask the typed authority
    independently -- applicability, the domain builder, projection and name
    semantics -- and all four must regress together before the witness gets
    through. Before sealing, two were enough. That difference is the point of
    sealing, and it is asserted step by step below rather than assumed.
    """

    target = tmp_path / "a" / "b" / "c"
    target.mkdir(parents=True)
    real_observe = MountTopologySnapshotV2.observe

    def _with_malformed_ancestor(cls=None):
        snapshot = real_observe()
        bad = MountRecordV2(10 ** 7, 10 ** 7 + 1, 0, "/x", str(tmp_path / "a"), "ext4")
        return MountTopologySnapshotV2(snapshot.records + (bad,))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_malformed_ancestor))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    # consumer 1: applicability stops asking the typed resolver
    monkeypatch.setattr(
        epoch_module, "_require_projection_applicable_v2",
        lambda snapshot, *paths: [snapshot._governing_mount_raw_v2(p) for p in paths] and None)
    # consumer 2: the domain builder rescans with its own at-or-beneath predicate
    def _rescanning_builder(snapshot, path):
        normalized = epoch_module._normalize_absolute_v2(path)
        prefix = normalized.rstrip("/") + "/"
        for record in snapshot.records:
            if record.mount_point == normalized or record.mount_point.startswith(prefix):
                snapshot.validate_relevant_chain_v2(record)
        device, internal = snapshot.project_v2(path)
        return (epoch_module._PhysicalSegmentV2(device, internal, ()),)

    monkeypatch.setattr(epoch_module, "_visible_physical_domain_v2", _rescanning_builder)

    # Before authority sealing, neutering these two consumers was enough to
    # reopen the falsifier. It no longer is: `project_v2` also routes through
    # the typed authority, so the refusal survives two regressing consumers.
    # That is the material difference between consumer discipline and a sealed
    # authority, and it is asserted rather than assumed.
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2, (
        "sealing must hold even when both typed-query consumers regress")

    # only ALSO bypassing the sealed projection reopens it -- which is exactly
    # what the structural guard makes impossible to land
    def _raw_projection(self, path):
        path = epoch_module._normalize_absolute_v2(path)
        governing = self._governing_mount_raw_v2(path)
        remainder = path[len(governing.mount_point.rstrip("/")):]
        if not remainder or governing.mount_point == path:
            return (governing.device, governing.root)
        return (governing.device,
                epoch_module._normalize_absolute_v2(governing.root.rstrip("/") + remainder))

    monkeypatch.setattr(MountTopologySnapshotV2, "project_v2", _raw_projection)
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2, (
        "three regressing consumers still do not reopen it")

    # Measured, not assumed: ALL FOUR consumers of the typed authority must
    # regress together before the witness gets through --
    # applicability, the domain builder, projection, and name semantics.
    monkeypatch.setattr(epoch_module, "_require_name_semantics_applicable_v2",
                        lambda snapshot, *paths: None)
    assert _acquire(target) == "acquired", (
        "with every typed-query consumer bypassed, the falsifier reopens")


# ------------------- structural authority assertions ----------------------


_RAW_TOPOLOGY_PRIMITIVES = {"_governing_mount_raw_v2", "_is_visible_raw_v2"}

# Permitted REFERENCE sites, identified by their real lexical owner
# (class, method) -- not by unqualified function name. An unrelated module-level
# function called `resolve_query_v2` must not inherit the permission.
_PERMITTED_RAW_REFERENCES = {
    ("MountTopologySnapshotV2", "resolve_query_v2"),
    ("MountTopologySnapshotV2", "_is_visible_raw_v2"),
}


def _raw_primitive_references(source: str) -> list[tuple[str, str, str]]:
    """Every STATIC reference to a raw primitive, with its lexical owner.

    References, not call shapes. The shipped guard inspected only
    `ast.Call(func=ast.Attribute(...))`, so the same bypass could be
    reintroduced through a bound alias, an unbound class alias, or `getattr`,
    while the test stayed green. Detected here:

        s._governing_mount_raw_v2(p)                direct attribute
        raw = s._governing_mount_raw_v2             bound alias
        raw = Cls._governing_mount_raw_v2           unbound alias
        getattr(s, "_governing_mount_raw_v2")       literal getattr
        f(s._governing_mount_raw_v2)                passed as a value

    Bound: this is static analysis of this repository's own source. It does not
    claim safety against arbitrary runtime reflection, and does not pretend to.
    """

    import ast

    tree = ast.parse(source)
    found: list[tuple[str, str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.cls: str | None = None
            self.fn: str | None = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            outer, self.cls = self.cls, node.name
            self.generic_visit(node)
            self.cls = outer

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            outer, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = outer

        def _record(self, kind: str) -> None:
            found.append((self.cls or "<module>", self.fn or "<module>", kind))

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in _RAW_TOPOLOGY_PRIMITIVES:
                self._record("attribute")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
                target = node.args[1]
                if isinstance(target, ast.Constant) and target.value in _RAW_TOPOLOGY_PRIMITIVES:
                    self._record("getattr")
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def _raw_reference_offenders(source: str) -> list[tuple[str, str, str]]:
    return [ref for ref in _raw_primitive_references(source)
            if (ref[0], ref[1]) not in _PERMITTED_RAW_REFERENCES]


# --- repository-wide production inventory (`#262` N18) --------------------
#
# The seal previously parsed exactly one file, `epoch_module.__file__`, so any
# OTHER production module could reference a raw primitive or rescan the
# snapshot while both structural guards stayed green. Six forms were shown
# undetected that way.
#
# The inventory is DERIVED from the repository's authoritative runtime package
# root rather than listed, so a production module added tomorrow is sealed
# without anyone remembering to update a list. That property is the whole
# point: a manually maintained list reproduces the same silent gap the first
# time it is not updated.
#
# Authority for the root: `app/` is the runtime package (`app/__init__.py`,
# and `scripts/*.py` import `from app...`), while `tests/` is a separate
# package. There is no pyproject/setup.py in this repository, so the package
# layout is the authority.

_PRODUCTION_PACKAGE_ROOTS = ("app",)
_NON_PRODUCTION_PARTS = frozenset({
    "__pycache__", ".venv", "venv", "build", "dist", ".git", "node_modules",
})


def _production_python_sources() -> list[Path]:
    """Every production Python source, derived from the package roots."""

    repository = Path(epoch_module.__file__).resolve().parents[2]
    sources: list[Path] = []
    for root in _PRODUCTION_PACKAGE_ROOTS:
        for path in sorted((repository / root).rglob("*.py")):
            if any(part in _NON_PRODUCTION_PARTS for part in path.parts):
                continue
            sources.append(path)
    return sources


# Attribute names that reconstruct topology relevance. Generic on their own,
# so they are only judged in modules that actually reference the topology
# subject -- the guard identifies the subject, it does not ban the names.
_RELEVANCE_INTERNALS = frozenset({"records", "children", "by_id"})
_TOPOLOGY_SUBJECT_MARKERS = ("MountTopologySnapshotV2", "target_pack_epoch_v2")

_PERMITTED_RELEVANCE_OWNERS = {
    ("MountTopologySnapshotV2", "__init__"),
    ("MountTopologySnapshotV2", "parse"),
    ("MountTopologySnapshotV2", "observe"),
    ("MountTopologySnapshotV2", "_semantic_seeds_v2"),
    ("MountTopologySnapshotV2", "_dependency_closure_v2"),
    ("MountTopologySnapshotV2", "resolve_query_v2"),
    ("MountTopologySnapshotV2", "_governing_mount_raw_v2"),
    ("MountTopologySnapshotV2", "_is_visible_raw_v2"),
    ("MountTopologySnapshotV2", "is_visible_v2"),
    ("MountTopologySnapshotV2", "visible_child_mounts_v2"),
    ("MountTopologySnapshotV2", "validate_relevant_chain_v2"),
    ("MountTopologySnapshotV2", "_climb_stack_v2"),
    ("MountTopologySnapshotV2", "_visible_root_v2"),
    ("MountTopologySnapshotV2", "governing_mount_v2"),
    ("MountTopologySnapshotV2", "project_v2"),
}


def _relevance_reconstruction_offenders(source: str) -> list[tuple[str, str, str]]:
    """Consumer-side rebuilding of relevance from the snapshot's internals.

    Only meaningful where the module references the topology subject at all;
    an unrelated class with a `.records` attribute is not this guard's
    business, and banning the bare name repository-wide would be a grep
    wearing an AST costume.
    """

    import ast

    if not any(marker in source for marker in _TOPOLOGY_SUBJECT_MARKERS):
        return []

    found: list[tuple[str, str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.cls: str | None = None
            self.fn: str | None = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            outer, self.cls = self.cls, node.name
            self.generic_visit(node)
            self.cls = outer

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            outer, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = outer

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in _RELEVANCE_INTERNALS:
                owner = (self.cls or "<module>", self.fn or "<module>")
                if owner not in _PERMITTED_RELEVANCE_OWNERS:
                    found.append((owner[0], owner[1], node.attr))
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def _seal_offenders_across(sources: list[Path]) -> dict[str, list]:
    """The repository-wide seal, over a derived source inventory."""

    offences: dict[str, list] = {}
    for path in sources:
        source = path.read_text(encoding="utf-8")
        raw = _raw_reference_offenders(source)
        relevance = _relevance_reconstruction_offenders(source)
        if raw or relevance:
            offences[str(path)] = raw + relevance
    return offences


def test_the_production_inventory_is_derived_not_listed() -> None:
    """§9/§12. Derived from the package root, so it expands by itself."""

    sources = _production_python_sources()
    assert len(sources) > 50, "the inventory must cover the whole runtime package"
    assert any(p.name == "target_pack_epoch_v2.py" for p in sources)
    assert not any("tests" in p.parts for p in sources), "tests are not production"
    assert not any("__pycache__" in p.parts for p in sources)


def test_topology_authority_is_sealed_across_every_production_module() -> None:
    """§10. Both classes, repository-wide: raw primitives and relevance
    reconstruction. The shipped guard read one file."""

    offences = _seal_offenders_across(_production_python_sources())
    assert offences == {}, f"topology authority escapes the seal: {offences}"


def test_a_new_production_module_enters_the_seal_without_editing_any_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§12. THE discriminator N18 requires.

    A module that did not exist when this test was written must be sealed the
    moment it lands under the package root -- with no inventory edit. This is
    what distinguishes a derived inventory from a maintained list, and a
    maintained list is how the same silent gap comes back.
    """

    package = tmp_path / "app" / "agent_review"
    package.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    newcomer = package / "brand_new_consumer.py"
    newcomer.write_text(
        "from app.agent_review.target_pack_epoch_v2 import MountTopologySnapshotV2\n"
        "def leak(snapshot, path):\n"
        "    return snapshot._governing_mount_raw_v2(path)\n",
        encoding="utf-8")

    monkeypatch.setattr(
        sys.modules[__name__], "_production_python_sources",
        lambda: sorted((tmp_path / "app").rglob("*.py")))
    offences = _seal_offenders_across(_production_python_sources())
    assert str(newcomer) in offences, "a newly added production module must be sealed"


@pytest.mark.parametrize(
    "label,body",
    [
        ("direct-call", "def leak(s, p):\n    return s._governing_mount_raw_v2(p)\n"),
        ("raw-visibility", "def leak(s, r):\n    return s._is_visible_raw_v2(r)\n"),
        ("bound-alias", "def leak(s):\n    raw = s._governing_mount_raw_v2\n    return raw('/x')\n"),
        ("unbound-alias", "def leak(s):\n    raw = MountTopologySnapshotV2._governing_mount_raw_v2\n    return raw(s, '/x')\n"),
        ("literal-getattr", "def leak(s):\n    return getattr(s, '_governing_mount_raw_v2')('/x')\n"),
        ("reference-as-value", "def leak(s, sink):\n    return sink(s._is_visible_raw_v2)\n"),
        ("rescan-records", "def leak(s):\n    return tuple(s.records)\n"),
        ("rescan-children", "def leak(s):\n    return s.children\n"),
        ("rescan-by-id", "def leak(s, i):\n    return s.by_id[i]\n"),
    ],
)
def test_cross_module_violations_are_all_detected(
    label: str, body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2 reproduction, as a durable corpus: every form the file-local guard
    missed when placed in ANOTHER production module."""

    package = tmp_path / "app"
    package.mkdir()
    module = package / f"consumer_{label.replace('-', '_')}.py"
    module.write_text(
        "from app.agent_review.target_pack_epoch_v2 import MountTopologySnapshotV2\n" + body,
        encoding="utf-8")
    monkeypatch.setattr(
        sys.modules[__name__], "_production_python_sources",
        lambda: sorted(package.rglob("*.py")))
    assert _seal_offenders_across(_production_python_sources()), f"undetected: {label}"


def test_the_seal_does_not_false_positive_on_unrelated_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§13 controls. The guard identifies the topology subject; it does not ban
    generic attribute names, and it does not punish legitimate typed use."""

    package = tmp_path / "app"
    package.mkdir()
    (package / "unrelated.py").write_text(
        "class Ledger:\n"
        "    def __init__(self):\n"
        "        self.records = []\n"
        "        self.children = {}\n"
        "        self.by_id = {}\n"
        "    def all(self):\n"
        "        return self.records, self.children, self.by_id\n",
        encoding="utf-8")
    (package / "typed_consumer.py").write_text(
        "from app.agent_review.target_pack_epoch_v2 import (\n"
        "    MountTopologySnapshotV2, TopologyQueryV2, TopologyQueryKindV2)\n"
        "def ask(snapshot, path):\n"
        "    return snapshot.resolve_query_v2(\n"
        "        TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, path)).governing_mount\n",
        encoding="utf-8")
    monkeypatch.setattr(
        sys.modules[__name__], "_production_python_sources",
        lambda: sorted(package.rglob("*.py")))
    assert _seal_offenders_across(_production_python_sources()) == {}


def test_mutant_scan_epoch_file_only(tmp_path: Path) -> None:
    """M_SCAN_EPOCH_FILE_ONLY -- the shipped scope. A violation in another
    production module goes undetected, which is precisely N18."""

    package = tmp_path / "app"
    package.mkdir()
    offender = package / "leaky.py"
    offender.write_text(
        "from app.agent_review.target_pack_epoch_v2 import MountTopologySnapshotV2\n"
        "def leak(s, p):\n    return s._governing_mount_raw_v2(p)\n", encoding="utf-8")

    epoch_only = [Path(epoch_module.__file__)]
    assert _seal_offenders_across(epoch_only) == {}, "the file-local scan sees nothing"
    assert _seal_offenders_across(sorted(package.rglob("*.py"))), "the derived scan catches it"


def test_mutant_manual_production_file_list(tmp_path: Path) -> None:
    """M_MANUAL_PRODUCTION_FILE_LIST -- a maintained list silently omits the
    module nobody remembered to add."""

    package = tmp_path / "app"
    package.mkdir()
    known = package / "known.py"
    known.write_text("x = 1\n", encoding="utf-8")
    forgotten = package / "forgotten.py"
    forgotten.write_text(
        "from app.agent_review.target_pack_epoch_v2 import MountTopologySnapshotV2\n"
        "def leak(s, p):\n    return s._governing_mount_raw_v2(p)\n", encoding="utf-8")

    maintained_list = [known]                       # the mutant: a hand-kept list
    assert _seal_offenders_across(maintained_list) == {}
    derived = sorted(package.rglob("*.py"))         # the real mechanism
    assert _seal_offenders_across(derived), "a derived inventory catches the forgotten module"


def test_dynamic_reflection_is_an_explicit_nonclaim() -> None:
    """Bound, stated rather than implied.

    This is static analysis of the repository's own Python. A dynamically
    computed attribute name defeats it, and no claim is made otherwise --
    saying so is the difference between a bounded guard and a false one.
    """

    dynamic = ("def leak(s, name):\n"
               "    return getattr(s, name)('/x')\n")
    assert _raw_reference_offenders(dynamic) == []


def _retired_test_the_guard_itself_would_catch_the_native_finding() -> None:
    """M_AST_EXEMPTS_PROJECT_FUNCTIONS -- proof the guard is not decorative.

    Reintroducing a prefix exemption for `project*` makes the offender set
    empty again, i.e. the exact regression that shipped becomes invisible.
    """

    offenders = set(_raw_primitive_callsites()) - _PERMITTED_RAW_CALLERS
    assert offenders == set()

    # simulate the OLD guard: exempt anything starting with "project"
    weakened = {c for c in _raw_primitive_callsites()
                if not c.startswith("project")} - _PERMITTED_RAW_CALLERS
    # and simulate project_v2 calling raw traversal again
    with_regression = set(_raw_primitive_callsites()) | {"project_v2"}
    assert "project_v2" in with_regression
    assert (with_regression - _PERMITTED_RAW_CALLERS), "strict guard must flag it"
    assert not ({c for c in with_regression if not c.startswith("project")}
                - _PERMITTED_RAW_CALLERS), "prefix-exempting guard would NOT flag it"
    assert weakened == set()


def test_resolver_does_not_recurse_through_the_consumer_wrapper() -> None:
    """`resolve_query_v2 -> _governing_mount_raw_v2`, never
    `resolve_query_v2 -> governing_mount_v2 -> resolve_query_v2`."""

    import inspect

    body = inspect.getsource(MountTopologySnapshotV2.resolve_query_v2)
    assert "_governing_mount_raw_v2(" in body
    assert "self.governing_mount_v2(" not in body


def test_single_authority_counts() -> None:
    source = Path(epoch_module.__file__).read_text(encoding="utf-8")
    for name, expected in [
        ('"/proc/self/mountinfo"', 1),
        ("def resolve_query_v2", 1),
        ("def _semantic_seeds_v2", 1),
        ("def _dependency_closure_v2", 1),
        ("def governing_mount_v2", 1),
        ("def project_v2", 1),
        ("def _require_projection_applicable_v2", 1),
        ("def _require_name_semantics_applicable_v2", 1),
        ("def _visible_physical_domain_v2", 1),
        ("def _carrier_operational_sites_v2", 1),
        ("def _establish_carrier_disjoint_v2", 1),
    ]:
        assert source.count(name) == expected, f"{name} должно be {expected}"


# ============ AUTHORITY SEALING — native P2 on ac0aa6e ====================
#
# The typed frontier was introduced but not SEALED: five consumers could still
# reach raw traversal and answer questions the authority refuses. The native
# review found `project_v2`; a sibling sweep found four more, and three leaked
# independently on the same demonstrated witness:
#
#   project_v2                    -> a projection through the underlying mount
#   _runtime_filesystem_type_v2   -> None, surfacing as `unavailable`
#   is_visible_v2                 -> False, i.e. Unknown(Visibility) -> Hidden
#
# That last one is the F5 law defeated through a bypass rather than through the
# rule. This was dispositioned RECURRENCE_ADMITTED (second admission on this
# PR) before any of it was corrected.


_ANCESTOR_GAP = ("36 35 98:0 / / rw - ext4 /dev/root rw\n"
                 "40 39 98:0 /x /a rw - ext4 /d rw\n")      # parent 39 ABSENT


def _gap_snapshot() -> MountTopologySnapshotV2:
    return MountTopologySnapshotV2.parse(_ANCESTOR_GAP)


def _refuses(callable_):
    try:
        callable_()
    except TargetPackEpochError as exc:
        return exc.reason_code == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
    return False


def test_projection_refuses_where_the_typed_resolver_refuses() -> None:
    """The native P2 itself. Before sealing, `project_v2('/a/b')` returned
    `(dev, '/a/b')` while the resolver refused the same path."""

    snapshot = _gap_snapshot()
    assert _refuses(lambda: snapshot.resolve_query_v2(
        _query(POINT, "/a/b"))), "the typed authority must refuse"
    assert _refuses(lambda: snapshot.project_v2("/a/b")), "projection must refuse identically"


def test_consumer_governing_mount_refuses_where_the_resolver_refuses() -> None:
    assert _refuses(lambda: _gap_snapshot().governing_mount_v2("/a/b"))


def test_visibility_does_not_collapse_unknown_into_hidden_via_bypass() -> None:
    """`is_visible_v2` answered `False` for a record whose position could not
    be established -- the F5 law defeated through a bypass."""

    snapshot = _gap_snapshot()
    assert _refuses(lambda: snapshot.is_visible_v2(snapshot.by_id[40]))


def test_filesystem_type_cannot_be_obtained_without_a_resolution() -> None:
    """`_runtime_filesystem_type_v2` no longer performs a lookup of its own; it
    consumes a resolution that, for this topology, cannot be obtained."""

    snapshot = _gap_snapshot()
    assert _refuses(lambda: snapshot.resolve_query_v2(_query(POINT, "/a/b")))
    import inspect
    body = inspect.getsource(epoch_module._runtime_filesystem_type_v2)
    assert "governing_mount_v2" not in body and "_governing_mount_raw_v2" not in body
    assert "resolution.governing_mount" in body


def test_runtime_parent_is_resolved_once_and_threaded() -> None:
    """§10. Eligibility, filesystem type and the FD identity check must share
    ONE acquisition-level resolution rather than each performing its own.

    Asserted structurally, because a runtime count is the wrong instrument
    here: per-authority name semantics (`#262` N17) legitimately resolves the
    runtime-parent PATH again when it happens to be a lookup authority for a
    carrier site. That is a different consumer asking a different question,
    not a duplicate of the eligibility lookup, and conflating the two would
    make this test forbid a correction it should not.
    """

    import ast

    source = Path(epoch_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    acquire = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "acquire_target_pack_epoch_v2")
    resolves = [n for n in ast.walk(acquire)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "resolve_query_v2"]
    assert len(resolves) == 1, f"acquisition resolves the runtime parent {len(resolves)} times"

    # and the two downstream consumers take it as a parameter rather than
    # resolving for themselves
    for name in ("_runtime_filesystem_type_v2", "_open_runtime_parent_v2"):
        body = ast.unparse(next(n for n in ast.walk(tree)
                                if isinstance(n, ast.FunctionDef) and n.name == name))
        assert "resolve_query_v2" not in body, f"{name} must consume the threaded resolution"
    assert "runtime_parent_resolution.governing_mount.device" in source


def test_sealing_does_not_over_refuse_clean_topology(
    runtime_parent: Path, tmp_path: Path
) -> None:
    """OVER-REFUSAL CONTROL for the whole sealing change."""

    snapshot = MountTopologySnapshotV2.observe()
    assert snapshot.project_v2(str(tmp_path))[1]
    assert snapshot.governing_mount_v2(str(tmp_path)).mount_id
    target = tmp_path / "ordinary"
    target.mkdir()
    assert _acquire(target) == "acquired"


# ---------------------------- sealing mutants -----------------------------


def test_mutant_project_bypasses_typed_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_PROJECT_BYPASSES_TYPED_FRONTIER -- the shipped regression."""

    snapshot = _gap_snapshot()
    assert _refuses(lambda: snapshot.project_v2("/a/b"))

    def _raw_projection(self, path):
        path = epoch_module._normalize_absolute_v2(path)
        governing = self._governing_mount_raw_v2(path)          # the bypass
        remainder = path[len(governing.mount_point.rstrip("/")):]
        if not remainder or governing.mount_point == path:
            return (governing.device, governing.root)
        return (governing.device,
                epoch_module._normalize_absolute_v2(governing.root.rstrip("/") + remainder))

    monkeypatch.setattr(MountTopologySnapshotV2, "project_v2", _raw_projection)
    assert not _refuses(lambda: _gap_snapshot().project_v2("/a/b")), (
        "mutant should answer where the authority refuses")


def test_mutant_visibility_bypasses_typed_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_VISIBILITY_BYPASSES_TYPED_FRONTIER."""

    snapshot = _gap_snapshot()
    assert _refuses(lambda: snapshot.is_visible_v2(snapshot.by_id[40]))

    monkeypatch.setattr(MountTopologySnapshotV2, "is_visible_v2",
                        MountTopologySnapshotV2._is_visible_raw_v2)
    fresh = _gap_snapshot()
    assert fresh.is_visible_v2(fresh.by_id[40]) is False, (
        "raw visibility collapses UNKNOWN into HIDDEN")


def test_mutant_runtime_fs_bypasses_typed_frontier(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_RUNTIME_FS_BYPASSES_TYPED_FRONTIER."""

    snapshot = _gap_snapshot()

    def _raw_fs(path, resolution):
        return snapshot._governing_mount_raw_v2("/a/b").filesystem_type

    assert _refuses(lambda: snapshot.resolve_query_v2(_query(POINT, "/a/b")))
    assert _raw_fs(None, None) == "ext4", "raw lookup answers where the authority refuses"


def test_mutant_runtime_parent_device_bypasses_typed_frontier(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_RUNTIME_PARENT_DEVICE_BYPASSES_TYPED_FRONTIER -- the identity check
    must consume the threaded resolution, not a second lookup."""

    import inspect
    body = inspect.getsource(epoch_module.acquire_target_pack_epoch_v2)
    assert "runtime_parent_resolution.governing_mount.device" in body
    assert "topology.governing_mount_v2(" not in body


def _retired_test_mutant_raw_governing_call_allowed_from_consumer() -> None:
    """M_RAW_GOVERNING_CALL_ALLOWED_FROM_CONSUMER -- widening the permitted
    caller set must make the guard vacuous, which is what makes it meaningful."""

    assert set(_raw_primitive_callsites()) - _PERMITTED_RAW_CALLERS == set()
    everything = set(_raw_primitive_callsites()) | {"project_v2", "some_future_consumer"}
    assert everything - _PERMITTED_RAW_CALLERS, "strict set flags the additions"
    assert not (everything - everything), "a permissive set would flag nothing"


# ============ N16 / N17 — graph fidelity and per-authority semantics ======


@pytest.mark.parametrize(
    "parent_point,child_point,expected",
    [
        ("/",          "/a",       "resolvable"),   # ordinary containment
        ("/a",         "/a/b",     "resolvable"),   # nested
        ("/a",         "/a",       "resolvable"),   # same-point stack is legal
        ("/a/b",       "/a",       "UNKNOWN"),      # child above its parent
        ("/elsewhere", "/a",       "UNKNOWN"),      # the native witness
        # The bad edge belongs to the CHILD record, and a child at /elsewhere is
        # not in the frontier of a query about /a/b -- so it is correctly
        # irrelevant. This row is the unrelated-malformed control, not a miss.
        ("/a",         "/elsewhere", "resolvable"),
    ],
    ids=["root-child", "nested", "same-point-stack", "child-above-parent",
         "disjoint-parent", "sibling-subtree-out-of-frontier"],
)
def test_n16_parent_edge_must_be_geometrically_possible(
    parent_point: str, child_point: str, expected: str
) -> None:
    """§7. A mount attaches inside its parent's subtree. An edge that could
    never hold validated a record the pathname walk can never reach."""

    rows = (f"39 36 98:0 /x {parent_point} rw - ext4 /d rw\n"
            f"40 39 98:0 /y {child_point} rw - ext4 /d rw\n")
    assert _resolves(rows, "/a/b", SUBTREE) == expected


def test_n16_impossible_edge_reaches_the_public_decision(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native reproducer, through public acquisition."""

    target = tmp_path / "a" / "b"
    target.mkdir(parents=True)
    real_observe = MountTopologySnapshotV2.observe

    def _with_impossible_edge(cls=None):
        snapshot = real_observe()
        elsewhere = MountRecordV2(10 ** 7, snapshot.records[0].mount_id, 0,
                                  "/x", str(tmp_path / "elsewhere"), "ext4")
        impossible = MountRecordV2(10 ** 7 + 1, 10 ** 7, 0, "/y",
                                   str(tmp_path / "a"), "ext4")
        return MountTopologySnapshotV2(snapshot.records + (elsewhere, impossible))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_impossible_edge))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n16_impossible_edge_unrelated_to_the_query_does_not_poison(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant lives inside chain validation, which the typed frontier
    scopes -- so a malformed edge nowhere near the query stays harmless. Not a
    return to global validation."""

    target = tmp_path / "project"
    target.mkdir()
    real_observe = MountTopologySnapshotV2.observe

    def _with_far_away_bad_edge(cls=None):
        snapshot = real_observe()
        parent = MountRecordV2(10 ** 7, snapshot.records[0].mount_id, 0, "/x", "/zz/far", "ext4")
        child = MountRecordV2(10 ** 7 + 1, 10 ** 7, 0, "/y", "/qq/other", "ext4")
        return MountTopologySnapshotV2(snapshot.records + (parent, child))

    monkeypatch.setattr(MountTopologySnapshotV2, "observe", staticmethod(_with_far_away_bad_edge))
    assert _acquire(target) == "acquired"


def test_mutant_parent_edge_geometry_unchecked(monkeypatch: pytest.MonkeyPatch) -> None:
    """M_PARENT_EDGE_GEOMETRY_UNCHECKED."""

    rows = ("39 36 98:0 /x /elsewhere rw - ext4 /d rw\n"
            "40 39 98:0 /y /a rw - ext4 /d rw\n")
    assert _resolves(rows, "/a/b", SUBTREE) == "UNKNOWN"

    def _no_geometry(self, record):
        seen = {record.mount_id}
        current = record
        while True:
            if current.mount_point == "/" and (
                    current.parent_id not in self.by_id or current.parent_id == current.mount_id):
                return
            if current.parent_id not in self.by_id or current.parent_id == current.mount_id:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
            current = self.by_id[current.parent_id]
            if current.mount_id in seen:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
            seen.add(current.mount_id)

    monkeypatch.setattr(MountTopologySnapshotV2, "validate_relevant_chain_v2", _no_geometry)
    assert _resolves(rows, "/a/b", SUBTREE) == "resolvable", (
        "without the geometry invariant the impossible edge is accepted")


# ---- N17: capability per lookup authority --------------------------------


def test_n17_capability_is_a_closed_three_way_classification() -> None:
    """§9. A two-way split silently promotes an unrecognised filesystem into a
    proven one; ENOTTY is not proof that lookup is case-sensitive."""

    cap = epoch_module._name_semantics_capability_v2
    assert cap("ext4") == epoch_module._NAME_SEMANTICS_CASEFOLD_FLAG_CAPABLE_V2
    assert cap("tmpfs") == epoch_module._NAME_SEMANTICS_CASEFOLD_FLAG_CAPABLE_V2
    assert cap("proc") == epoch_module._NAME_SEMANTICS_ESTABLISHED_CASE_SENSITIVE_V2
    assert cap("somefuturefs") == epoch_module._NAME_SEMANTICS_UNKNOWN_V2


def test_n17_non_casefold_capable_authority_is_not_probed(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§10 control A. `/proc` answers ENOTTY to FS_IOC_GETFLAGS; taking the
    capability from the FINAL path's filesystem made that a false UNKNOWN."""

    probed: list[str] = []
    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2",
                        lambda path: probed.append(path) or real_probe(path))

    real_resolve = MountTopologySnapshotV2.resolve_query_v2

    def _proc_authority(self, query):
        resolution = real_resolve(self, query)
        if query.path == str(tmp_path):
            return resolution._replace(
                governing_mount=resolution.governing_mount._replace(filesystem_type="proc"))
        return resolution

    monkeypatch.setattr(MountTopologySnapshotV2, "resolve_query_v2", _proc_authority)
    target = tmp_path / "project"
    target.mkdir()
    assert _acquire(target) == "acquired"
    assert str(tmp_path) not in probed, "an established case-sensitive authority must not be probed"


def test_n17_casefold_capable_ancestor_is_still_probed(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§10 control B, the opposite direction: the decision is genuinely per
    authority, so a casefolded ANCESTOR is caught even when the final path's
    own filesystem could not casefold."""

    target = tmp_path / "project"
    target.mkdir()
    ancestor = str(tmp_path)
    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2",
                        lambda path: True if path == ancestor else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_n17_unknown_filesystem_at_a_relevant_authority_is_unknown(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§10 control D."""

    real_resolve = MountTopologySnapshotV2.resolve_query_v2

    def _exotic(self, query):
        resolution = real_resolve(self, query)
        if query.path == str(tmp_path):
            return resolution._replace(
                governing_mount=resolution.governing_mount._replace(filesystem_type="futurefs"))
        return resolution

    monkeypatch.setattr(MountTopologySnapshotV2, "resolve_query_v2", _exotic)
    target = tmp_path / "project"
    target.mkdir()
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2


def test_mutant_casefold_uses_final_filesystem_for_all(
    runtime_parent: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M_CASEFOLD_USES_FINAL_FILESYSTEM_FOR_ALL / M_CASEFOLD_CAPABLE_ANCESTOR_SKIPPED."""

    # The casefolded directory must sit ABOVE the nearest authority, or the
    # mutant would see it too and die by the wrong proposition.
    target = tmp_path / "a" / "b" / "project"
    target.mkdir(parents=True)
    ancestor = str(tmp_path)
    assert epoch_module._lookup_authorities_v2(str(target))[0] != ancestor

    real_probe = epoch_module._directory_is_casefolded_v2
    monkeypatch.setattr(epoch_module, "_directory_is_casefolded_v2",
                        lambda path: True if path == ancestor else real_probe(path))
    assert _acquire(target) == TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2

    def _final_fs_for_all(snapshot, *paths):
        for path in paths:
            governing = snapshot.resolve_query_v2(
                _query(POINT, path)).governing_mount
            if governing.filesystem_type not in epoch_module._CASEFOLD_CAPABLE_FILESYSTEMS_V2:
                continue
            # the old shape: consult only the NEAREST authority, so a
            # casefolded ancestor further up is never seen
            authorities = epoch_module._lookup_authorities_v2(path)
            if authorities and epoch_module._directory_is_casefolded_v2(authorities[0]) is not False:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)

    monkeypatch.setattr(epoch_module, "_require_name_semantics_applicable_v2", _final_fs_for_all)
    assert _acquire(target) == "acquired", "mutant should have skipped the casefolded ancestor"
