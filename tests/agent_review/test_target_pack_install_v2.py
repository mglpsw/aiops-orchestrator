from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.agent_review.target_pack_install_v2 import (
    INSTALL_DRIFT_UNRESOLVED_REASON_V2,
    INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2,
    TargetPackInstallError,
    apply_install_plan_v2 as _apply_install_plan_v2,
    write_receipt_v2 as _write_receipt_v2,
)
from app.agent_review.target_pack_epoch_v2 import acquire_target_pack_epoch_v2
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_plan_v2 import (
    PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    PlanError,
    compute_install_plan_v2,
)
from app.agent_review.target_pack_receipt_v2 import TargetInstallReceiptV2, compute_target_install_receipt_hash_v2


@contextmanager
def _bound_exclusive_target_v2(target_root: Path):
    """Exercise the low-level writer only through its required capability."""

    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True) as lease:
        with lease.bind_target_root_v2(target_root=target_root) as binding:
            yield lease, binding


def apply_install_plan_v2(*, plan, manifest, target_root: Path, seed_content_by_path, force_overwrite_paths=frozenset()):
    with _bound_exclusive_target_v2(target_root) as (lease, binding):
        return _apply_install_plan_v2(
            plan=plan,
            manifest=manifest,
            seed_content_by_path=seed_content_by_path,
            lease=lease,
            target_binding=binding,
            force_overwrite_paths=force_overwrite_paths,
        )


def write_receipt_v2(*, target_root: Path, receipt, expected_target_root_real: str) -> None:
    with _bound_exclusive_target_v2(target_root) as (lease, binding):
        _write_receipt_v2(
            receipt=receipt,
            expected_target_root_real=expected_target_root_real,
            lease=lease,
            target_binding=binding,
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(*entries: GeneratedFileEntryV2) -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=entries,
        schema_digests={"x.json": "a" * 64},
        required_capabilities=(),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


def test_apply_writes_a_missing_upstream_generated_file(tmp_path: Path) -> None:
    content = b"seed content"
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(content)
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    written = apply_install_plan_v2(
        plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={"a.yaml": content}
    )

    assert written == ("a.yaml",)
    assert (tmp_path / "a.yaml").read_bytes() == content


def test_apply_refuses_and_writes_nothing_when_drift_is_unresolved(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"new")
    )
    entry2 = GeneratedFileEntryV2(
        path="b.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"new2")
    )
    (tmp_path / "a.yaml").write_bytes(b"target-hand-edited")
    manifest = _manifest(entry, entry2)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)
    # No previous receipt at all -> on-disk "a.yaml" has no recorded hash to
    # match, and its content differs from the seed -> REFUSE_DRIFT.
    assert plan.has_drift

    with pytest.raises(TargetPackInstallError) as exc_info:
        apply_install_plan_v2(
            plan=plan,
            manifest=manifest,
            target_root=tmp_path,
            seed_content_by_path={"a.yaml": b"new", "b.yaml": b"new2"},
        )
    assert exc_info.value.reason_code == INSTALL_DRIFT_UNRESOLVED_REASON_V2
    # Nothing written at all -- not even the non-drifted "b.yaml".
    assert not (tmp_path / "b.yaml").exists()
    assert (tmp_path / "a.yaml").read_bytes() == b"target-hand-edited"


def test_apply_writes_a_drifted_path_only_when_explicitly_forced(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"new")
    )
    (tmp_path / "a.yaml").write_bytes(b"target-hand-edited")
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    written = apply_install_plan_v2(
        plan=plan,
        manifest=manifest,
        target_root=tmp_path,
        seed_content_by_path={"a.yaml": b"new"},
        force_overwrite_paths=frozenset({"a.yaml"}),
    )

    assert written == ("a.yaml",)
    assert (tmp_path / "a.yaml").read_bytes() == b"new"


def test_apply_never_touches_a_target_owned_file(tmp_path: Path) -> None:
    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(b"heavily customized by target")
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    written = apply_install_plan_v2(
        plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={entry.path: b"seed"}
    )

    assert written == ()
    assert (tmp_path / ".aiops" / "target-profile.v2.yaml").read_bytes() == b"heavily customized by target"


def test_apply_is_atomic_no_partial_file_left_on_interrupted_write(tmp_path: Path, monkeypatch) -> None:
    """P-T10: a write interrupted mid-flight must never leave a partial
    file at the real path -- only ever the old content or the fully new
    content."""

    import app.agent_review.target_pack_install_v2 as install_module

    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"new")
    )
    (tmp_path / "a.yaml").write_bytes(b"original")
    manifest = _manifest(entry)
    receipt_hashes = {"a.yaml": _sha256(b"original")}

    class _FakeReceipt:
        generated_file_hashes = receipt_hashes

    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=_FakeReceipt())

    real_replace = install_module.os.replace

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(install_module.os, "replace", _boom)
    with pytest.raises(OSError):
        apply_install_plan_v2(
            plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={"a.yaml": b"new"}
        )
    monkeypatch.setattr(install_module.os, "replace", real_replace)

    # Original content survives untouched; no stray .tmp files remain.
    assert (tmp_path / "a.yaml").read_bytes() == b"original"
    leftover_tmp_files = list(tmp_path.glob("*.tmp"))
    assert leftover_tmp_files == []


def test_apply_refuses_to_write_through_a_directory_symlink_escaping_target_root(tmp_path: Path) -> None:
    """P-T2/P-T3 (spec `§10`), confirmed and fixed. If `.aiops` inside
    `target_root` is a symlink pointing OUTSIDE `target_root`, the write
    must be refused and NOTHING written -- not silently followed to write
    pack-controlled content to an arbitrary filesystem location. Reproduced
    before the fix: the file landed inside the symlink target, outside
    `target_root` entirely.

    Exercises the genuine TOCTOU window this write-side check uniquely
    covers: the symlink is introduced AFTER planning. Since
    aiops-orchestrator#205/H1A-R1 added read-side containment,
    `compute_install_plan_v2` refuses a symlink already present at plan
    time -- so planning first against a clean tree is the only way to reach
    (and therefore keep testing) `_atomic_write_v2`'s own check, which is
    exactly the "swapped in during the window between plan and apply" case
    the module docstring documents. The two checks are complementary, not
    redundant: neither subsumes the other."""

    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )
    manifest = _manifest(entry)
    # Plan against a clean tree -- no symlink yet.
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    # Attacker swaps `.aiops` for an escaping symlink AFTER the plan exists.
    outside = tmp_path.parent / f"{tmp_path.name}-outside-escape-target"
    outside.mkdir()
    (tmp_path / ".aiops").symlink_to(outside)

    with pytest.raises(TargetPackInstallError) as exc_info:
        apply_install_plan_v2(
            plan=plan,
            manifest=manifest,
            target_root=tmp_path,
            seed_content_by_path={".aiops/target-profile.v2.yaml": b"seed"},
        )

    assert exc_info.value.reason_code == INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert list(outside.iterdir()) == []


def test_plan_time_read_containment_refuses_a_symlink_present_before_planning(tmp_path: Path) -> None:
    """The companion to the test above (aiops-orchestrator#205, H1A-R1):
    when the escaping symlink is ALREADY present at plan time, the refusal
    now happens at plan time, before any write is even contemplated --
    strictly earlier than the write-side check, and covering the read that
    the write-side check never could."""

    outside = tmp_path.parent / f"{tmp_path.name}-outside-planning-escape"
    outside.mkdir()
    (outside / "target-profile.v2.yaml").write_bytes(b"seed")
    (tmp_path / ".aiops").symlink_to(outside)

    entry = GeneratedFileEntryV2(
        path=".aiops/target-profile.v2.yaml",
        ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
        content_sha256=_sha256(b"seed"),
    )
    with pytest.raises(PlanError) as exc_info:
        compute_install_plan_v2(manifest=_manifest(entry), target_root=tmp_path, previous_receipt=None)

    assert exc_info.value.reason_code == PLAN_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_apply_refuses_when_the_final_path_component_itself_is_a_preexisting_symlink(tmp_path: Path) -> None:
    """Distinct from the intermediate-directory-symlink case above: here
    the GENERATED FILE'S OWN path (not a parent directory) is a
    pre-existing symlink pointing outside `target_root`. Adversarial round
    probe, confirmed already safe by two independent mechanisms and locked
    in here as a regression test: (1) `_verify_write_target_within_root_v2`
    resolves the full candidate path -- including a symlinked final
    component -- and already refuses before any write is attempted; (2)
    even if it did not, `os.replace` never follows a final-component
    symlink for its destination argument (POSIX `rename(2)` semantics) --
    it replaces the symlink's own directory entry, never writes through it
    to the link's target. Verified directly: `os.replace` onto an existing
    symlink replaces the link itself and leaves the link's target file
    completely untouched."""

    outside = tmp_path.parent / f"{tmp_path.name}-outside-final-component-symlink"
    outside.mkdir()
    escape_target = outside / "secret.txt"
    escape_target.write_text("ORIGINAL-OUTSIDE-CONTENT", encoding="utf-8")

    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"seed")
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    # The generated file's OWN path is a preexisting symlink, not a parent.
    (tmp_path / "a.yaml").symlink_to(escape_target)

    with pytest.raises(TargetPackInstallError) as exc_info:
        apply_install_plan_v2(plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={"a.yaml": b"seed"})

    assert exc_info.value.reason_code == INSTALL_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert escape_target.read_text(encoding="utf-8") == "ORIGINAL-OUTSIDE-CONTENT"


def test_apply_refuses_when_target_root_itself_was_swapped_for_a_symlink_after_planning(
    tmp_path: Path,
) -> None:
    """Round 5 adversarial finding, confirmed and fixed: the per-file
    symlink check (round 4) resolves `target_root` fresh at the start of
    `apply_install_plan_v2` -- but if `target_root` ITSELF was already
    replaced by a symlink pointing elsewhere between `compute_install_
    plan_v2` and `apply_install_plan_v2`, that "fresh" resolution just
    faithfully reports the attacker's redirected location, and every
    per-file check passes trivially against it. Reproduced before the fix:
    the file landed inside the symlink's target with no refusal at all.
    """

    import shutil

    outside = tmp_path.parent / f"{tmp_path.name}-outside-root-swap"
    outside.mkdir()

    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(b"new")
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    # target_root ITSELF replaced by a symlink AFTER planning.
    shutil.rmtree(tmp_path)
    tmp_path.symlink_to(outside)

    with pytest.raises(TargetPackInstallError) as exc_info:
        apply_install_plan_v2(
            plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={"a.yaml": b"new"}
        )

    assert exc_info.value.reason_code == INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2
    assert list(outside.iterdir()) == []


def test_bound_fd_relative_write_stays_with_the_original_directory_after_path_replacement(tmp_path: Path) -> None:
    """R36/R37/M_PATH_REDERIVATION: pathname replacement cannot redirect a held writer."""

    target = tmp_path / "target"
    target.mkdir()
    content = b"seed"
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(content)
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=target, previous_receipt=None)
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True) as lease:
        with lease.bind_target_root_v2(target_root=target) as binding:
            original = tmp_path / "target-original"
            target.rename(original)
            target.mkdir()
            written = _apply_install_plan_v2(
                plan=plan,
                manifest=manifest,
                seed_content_by_path={"a.yaml": content},
                lease=lease,
                target_binding=binding,
            )
    assert written == ("a.yaml",)
    assert (original / "a.yaml").read_bytes() == content
    assert not (target / "a.yaml").exists()


def test_merged_declarative_only_replaces_the_fenced_block(tmp_path: Path) -> None:
    target_file = tmp_path / ".gitignore"
    target_file.write_text(
        "node_modules/\n"
        "# --- agent-review-v2:begin ---\n"
        "old-generated-line\n"
        "# --- agent-review-v2:end ---\n"
        "*.local\n",
        encoding="utf-8",
    )
    new_block = (
        "# --- agent-review-v2:begin ---\n"
        "new-generated-line\n"
        "# --- agent-review-v2:end ---\n"
    ).encode("utf-8")
    entry = GeneratedFileEntryV2(
        path=".gitignore", ownership=TargetPackFileOwnershipV2.MERGED_DECLARATIVE, content_sha256=_sha256(new_block)
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    apply_install_plan_v2(
        plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={".gitignore": new_block}
    )

    result = target_file.read_text(encoding="utf-8")
    assert "node_modules/" in result
    assert "*.local" in result
    assert "new-generated-line" in result
    assert "old-generated-line" not in result


def test_merged_declarative_creates_the_block_when_file_did_not_exist(tmp_path: Path) -> None:
    new_block = (
        "# --- agent-review-v2:begin ---\nnew-line\n# --- agent-review-v2:end ---\n"
    ).encode("utf-8")
    entry = GeneratedFileEntryV2(
        path=".gitignore", ownership=TargetPackFileOwnershipV2.MERGED_DECLARATIVE, content_sha256=_sha256(new_block)
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)

    apply_install_plan_v2(
        plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={".gitignore": new_block}
    )

    assert "new-line" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def _receipt() -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest="d" * 64,
        target_repo="owner/repo",
        portable_target_root_identity="e" * 64,
        target_profile_hash="a" * 64,
        target_policy_hash=None,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={},
        target_owned_paths=(),
        required_capabilities=(),
        expected_runner_labels=(),
        required_secret_names=(),
        rollout_mode="off",
        compatibility="compatible",
        previous_install_identity=None,
        generated_at=None,
    )
    receipt_hash = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=receipt_hash)


def test_installed_files_are_not_more_restrictive_than_an_ordinary_write(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed:
    `tempfile.mkstemp` creates `0600`, and `os.replace` preserves that mode
    onto the final path -- every pack-installed file therefore landed more
    restrictive than an ordinary write. Not a security hole (0600 is MORE
    restrictive, never less), but a real gap against ordinary-file
    expectations."""

    import stat

    content = b"seed content"
    entry = GeneratedFileEntryV2(
        path="a.yaml", ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED, content_sha256=_sha256(content)
    )
    manifest = _manifest(entry)
    plan = compute_install_plan_v2(manifest=manifest, target_root=tmp_path, previous_receipt=None)
    apply_install_plan_v2(plan=plan, manifest=manifest, target_root=tmp_path, seed_content_by_path={"a.yaml": content})

    mode = stat.S_IMODE((tmp_path / "a.yaml").stat().st_mode)
    assert mode == 0o644


def test_write_receipt_accepts_when_root_identity_matches_the_plans(tmp_path: Path) -> None:
    write_receipt_v2(target_root=tmp_path, receipt=_receipt(), expected_target_root_real=str(tmp_path.resolve()))
    assert (tmp_path / ".aiops" / "install-receipt.v2.json").is_file()


def test_write_receipt_refuses_when_root_identity_changed_since_the_plan(tmp_path: Path) -> None:
    """P2-C, spec rev.2 §5.4: `apply_install_plan_v2` binds its own writes to
    `plan.target_root_real`, captured at plan time, but `write_receipt_v2`
    previously re-resolved `target_root` independently, with no cross-check
    against any prior identity at all. Reproduced before the fix: calling
    `write_receipt_v2` against a root that had been replaced by a symlink
    landed the receipt straight through it, no refusal, no binding to the
    install it was supposed to describe. `expected_target_root_real` closes
    the window between `apply_install_plan_v2` completing and this call."""

    import shutil

    outside = tmp_path.parent / f"{tmp_path.name}-outside-receipt-root-swap"
    outside.mkdir()
    plan_time_identity = str(tmp_path.resolve())

    # target_root ITSELF replaced by a symlink AFTER the plan/apply identity
    # was captured -- the same attack class as the apply-time root swap.
    shutil.rmtree(tmp_path)
    tmp_path.symlink_to(outside)

    with pytest.raises(TargetPackInstallError) as exc_info:
        write_receipt_v2(target_root=tmp_path, receipt=_receipt(), expected_target_root_real=plan_time_identity)

    assert exc_info.value.reason_code == INSTALL_TARGET_ROOT_IDENTITY_CHANGED_REASON_V2
    assert not (outside / ".aiops" / "install-receipt.v2.json").exists()
