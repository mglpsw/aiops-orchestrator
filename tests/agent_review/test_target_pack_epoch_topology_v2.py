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
from pathlib import Path

import pytest

import app.agent_review.target_pack_epoch_v2 as epoch_module
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2,
    TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2,
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
        "40 39 98:0 /x /deep rw - ext4 /dev/root rw\n"          # parent 39 absent
    )
    snapshot = MountTopologySnapshotV2.parse(table)
    # unreachable from the visible root: its parent is not represented, so the
    # walk never reaches it and it governs nothing
    assert snapshot.governing_mount_v2("/deep").mount_id == 36
    assert not snapshot.is_visible_v2(snapshot.by_id[40])


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


def test_cycle_in_the_relevant_chain_is_unknown() -> None:
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
    assert "governing_mount_v2" in body


# ================================ MUTANTS =================================
# Every mutant is judged against a REAL filesystem observation. None is ever
# allowed to supply the oracle used to judge it -- that exact circularity made
# a whole mutation run report false survival during the architecture spike, so
# it is asserted structurally below.


def _mutant_snapshot(cls_body):
    """Build a mutant subclass of the real snapshot from a governing override."""
    return type("Mutant", (MountTopologySnapshotV2,), {"governing_mount_v2": cls_body})


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
