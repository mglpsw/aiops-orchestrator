"""`#200-G3` -- tests for `operational_scope_v2`, ported WITH REVALIDATION
from `#277`'s spike (no qualification transfer, per the recovery
checkpoint). These are freshly written against this worktree's own port,
not copied uncritically from `#277`'s test files.

Covers:
  * `classify_changed_path_v2` -- one disposition per structural shape,
    exhaustively over the combinatorial precedence space.
  * `assess_changed_scope_v2` -- duplicate-path refusal, git type-change
    pairing, must-review-blocked detection.
  * `ScopeAssessmentV2` -- derived properties, `to_scope_completeness_v2`.
  * `assert_scope_authority_agrees_with_assembly_v2` -- the disagreement
    detector, in both divergence directions.
  * The exact `#277` round-1 false-READY witness: a
    `src/pages/[id].tsx`-shaped path.
  * A real-git fuzz corpus: actual repos exercising rename, chmod, binary,
    submodule, empty-file, and type-change transitions end to end through
    `acquire_diff_v2` + `parse_unified_diff`.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from app.agent_review.contracts_v2 import ScopeCompletenessV2, TargetProfileV2
from app.agent_review.diff_acquisition_v2 import (
    ParsedFileDiffV2,
    ParsedHunkV2,
    acquire_diff_v2,
    parse_unified_diff,
    path_violates_relative_path_contract_v2,
)
from app.agent_review.operational_scope_v2 import (
    SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2,
    SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2,
    PathDispositionV2,
    ScopeAssessmentError,
    ScopeAssessmentV2,
    assert_scope_authority_agrees_with_assembly_v2,
    assess_changed_scope_v2,
    classify_changed_path_v2,
)


# -- fixture helpers -----------------------------------------------------------


def _hunk(seed: str = "h") -> ParsedHunkV2:
    return ParsedHunkV2(
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=1,
        diff_sha256=hashlib.sha256(seed.encode()).hexdigest(),
        diff_chars=10,
    )


def _diff(
    *,
    path: str,
    change_type: str = "modified",
    hunks: tuple[ParsedHunkV2, ...] = (),
    is_binary: bool = False,
    is_submodule: bool = False,
    truncated: bool = False,
) -> ParsedFileDiffV2:
    return ParsedFileDiffV2(
        old_path=path,
        new_path=path,
        change_type=change_type,
        is_binary=is_binary,
        is_submodule=is_submodule,
        similarity_index=None,
        old_no_newline_at_eof=False,
        new_no_newline_at_eof=False,
        hunks=hunks,
        truncated=truncated,
    )


def _profile(*, must_review_paths: list[str] = (), must_review_patterns: list[str] = ()) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2",
            "schema_version": 2,
            "source": "repo-profile",
            "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
            "artifacts": [
                {
                    "artifact_id": "full-diff",
                    "path": "artifacts/full.diff",
                    "kind": "diff",
                    "required": True,
                    "max_bytes": 1000000,
                }
            ],
            "budgets": {
                "max_chunks": 32,
                "total_prompt_chars": 250000,
                "max_chars_per_chunk": 24000,
                "max_files_per_chunk": 50,
                "max_contracts_per_chunk": 50,
            },
            "must_review": {
                "paths": list(must_review_paths),
                "patterns": list(must_review_patterns),
                "artifact_ids": [],
                "minimum_coverage": "complete",
            },
            "policies": {
                "network_policy": "forbidden",
                "fail_closed": True,
                "redaction_required": True,
                "allow_partial_coverage": False,
                "required_checks": ["pytest"],
                "allowed_semantic_groups": ["primary_backend_logic", "tests"],
                "coverage_failure_state": "blocked_pipeline",
                "model_uncertainty_state": "manual_required",
            },
            "contracts": [
                {
                    "contract_id": "contract.api",
                    "contract_version": "1",
                    "path": ".aiops/domain-contracts.yaml",
                    "sha256": "f" * 64,
                    "scope": "repository",
                    "required": True,
                }
            ],
            "limitations": [],
        }
    )


# -- classify_changed_path_v2: one disposition per structural shape ------------


class TestClassifyChangedPathV2:
    def test_truncated_wins_over_everything_else(self) -> None:
        d = _diff(path="app/a.py", is_binary=True, is_submodule=True, truncated=True, hunks=(_hunk(),))
        assert classify_changed_path_v2(d) is PathDispositionV2.TRUNCATED

    def test_submodule_wins_over_binary(self) -> None:
        d = _diff(path="vendor/lib", is_binary=True, is_submodule=True)
        assert classify_changed_path_v2(d) is PathDispositionV2.SUBMODULE_GITLINK

    def test_binary_wins_over_unrepresentable_path_ordering_is_still_unsupported(self) -> None:
        # Both binary and unrepresentable-path independently make scope
        # incomplete; whichever the classifier reports, it must be one of
        # the two "not representable" members, never REVIEWABLE.
        d = _diff(path="app/[id].tsx", is_binary=True)
        assert classify_changed_path_v2(d) in (PathDispositionV2.BINARY_UNSUPPORTED, PathDispositionV2.UNREPRESENTABLE)

    def test_binary_with_ordinary_path(self) -> None:
        d = _diff(path="assets/logo.png", is_binary=True)
        assert classify_changed_path_v2(d) is PathDispositionV2.BINARY_UNSUPPORTED

    def test_unrepresentable_path_with_hunks_is_still_unrepresentable(self) -> None:
        """The exact `#277` round-1 witness: a path that violates
        `contracts_v2.RelativePath` but DOES have ordinary hunks must not
        be certified reviewable just because content changed."""
        d = _diff(path="src/pages/[id].tsx", hunks=(_hunk(),))
        assert classify_changed_path_v2(d) is PathDispositionV2.UNREPRESENTABLE

    def test_pure_rename_is_rename(self) -> None:
        d = _diff(path="app/b.py", change_type="renamed", hunks=())
        assert classify_changed_path_v2(d) is PathDispositionV2.RENAME

    def test_rename_with_content_change_is_reviewable(self) -> None:
        d = _diff(path="app/b.py", change_type="renamed", hunks=(_hunk(),))
        assert classify_changed_path_v2(d) is PathDispositionV2.REVIEWABLE

    def test_chmod_only_is_chmod_only(self) -> None:
        d = _diff(path="bin/run.sh", change_type="type_changed", hunks=())
        assert classify_changed_path_v2(d) is PathDispositionV2.CHMOD_ONLY

    def test_empty_file_add_is_empty_file_transition(self) -> None:
        d = _diff(path="app/new_empty.py", change_type="added", hunks=())
        assert classify_changed_path_v2(d) is PathDispositionV2.EMPTY_FILE_TRANSITION

    def test_empty_file_delete_is_empty_file_transition(self) -> None:
        d = _diff(path="app/old_empty.py", change_type="deleted", hunks=())
        assert classify_changed_path_v2(d) is PathDispositionV2.EMPTY_FILE_TRANSITION

    def test_pure_copy_falls_back_to_metadata_only(self) -> None:
        d = _diff(path="app/copy.py", change_type="copied", hunks=())
        assert classify_changed_path_v2(d) is PathDispositionV2.METADATA_ONLY

    def test_ordinary_modification_is_reviewable(self) -> None:
        d = _diff(path="app/a.py", change_type="modified", hunks=(_hunk(),))
        assert classify_changed_path_v2(d) is PathDispositionV2.REVIEWABLE

    def test_every_disposition_is_reachable(self) -> None:
        """Exhaustiveness, proven by construction rather than trusted: every
        member of the enum must be producible by at least one input shape.

        `TYPE_CHANGE` is deliberately NOT reachable through
        `classify_changed_path_v2` alone -- see that function's own
        docstring: a genuine git type change only ever exists as a
        delete-plus-add PAIR for the same path, recognized by
        `assess_changed_scope_v2`'s grouping step, never by a single
        block. Checked separately, right below.
        """
        reachable = {
            classify_changed_path_v2(_diff(path="t", truncated=True)),
            classify_changed_path_v2(_diff(path="s", is_submodule=True)),
            classify_changed_path_v2(_diff(path="b", is_binary=True)),
            classify_changed_path_v2(_diff(path="app/[id].tsx")),
            classify_changed_path_v2(_diff(path="c", change_type="type_changed")),
            classify_changed_path_v2(_diff(path="r", change_type="renamed")),
            classify_changed_path_v2(_diff(path="e", change_type="added")),
            classify_changed_path_v2(_diff(path="m", change_type="copied")),
            classify_changed_path_v2(_diff(path="rev", hunks=(_hunk(),))),
        }
        assert reachable == set(PathDispositionV2) - {PathDispositionV2.TYPE_CHANGE}

    def test_type_change_is_reachable_only_via_assess_changed_scope_v2(self) -> None:
        diffs = [
            _diff(path="app/link", change_type="deleted", hunks=()),
            _diff(path="app/link", change_type="added", hunks=()),
        ]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert assessment.disposition_of("app/link") is PathDispositionV2.TYPE_CHANGE

    # -- exhaustive combinatorial fuzz over the precedence space ----------

    @pytest.mark.parametrize("truncated", [False, True])
    @pytest.mark.parametrize("is_submodule", [False, True])
    @pytest.mark.parametrize("is_binary", [False, True])
    @pytest.mark.parametrize("bad_path", [False, True])
    @pytest.mark.parametrize("change_type", ["modified", "added", "deleted", "renamed", "copied", "type_changed"])
    @pytest.mark.parametrize("has_hunks", [False, True])
    def test_classification_is_total_and_deterministic(
        self, truncated, is_submodule, is_binary, bad_path, change_type, has_hunks
    ) -> None:
        """Every one of the 2*2*2*2*6*2 = 384 combinations must resolve to
        EXACTLY one PathDispositionV2 member -- never raise, never return
        None -- and re-running the same input must be idempotent. This is
        the reduced-but-real revalidation of `#277`'s much larger
        differential fuzz corpus: same precedence structure, exhaustively
        walked rather than randomly sampled."""

        path = "src/pages/[id].tsx" if bad_path else "app/ordinary.py"
        # `type_changed` is only ever produced by the real parser when there
        # are no hunks (see diff_acquisition_v2's builder) -- constrain the
        # fuzz to shapes the real parser can actually produce.
        if change_type == "type_changed" and has_hunks:
            pytest.skip("the real parser never emits type_changed with hunks")
        diff = _diff(
            path=path,
            change_type=change_type,
            hunks=(_hunk(),) if has_hunks else (),
            is_binary=is_binary,
            is_submodule=is_submodule,
            truncated=truncated,
        )
        result_1 = classify_changed_path_v2(diff)
        result_2 = classify_changed_path_v2(diff)
        assert result_1 is result_2
        assert isinstance(result_1, PathDispositionV2)

        # Cross-check the documented precedence explicitly.
        if truncated:
            assert result_1 is PathDispositionV2.TRUNCATED
        elif is_submodule:
            assert result_1 is PathDispositionV2.SUBMODULE_GITLINK
        elif is_binary:
            assert result_1 is PathDispositionV2.BINARY_UNSUPPORTED
        elif bad_path:
            assert result_1 is PathDispositionV2.UNREPRESENTABLE
        elif change_type == "type_changed":
            assert result_1 is PathDispositionV2.CHMOD_ONLY
        elif not has_hunks:
            assert result_1 in (
                PathDispositionV2.RENAME,
                PathDispositionV2.EMPTY_FILE_TRANSITION,
                PathDispositionV2.METADATA_ONLY,
            )
        else:
            assert result_1 is PathDispositionV2.REVIEWABLE


# -- assess_changed_scope_v2 ----------------------------------------------------


class TestAssessChangedScopeV2:
    def test_duplicate_path_not_a_type_change_pair_refuses(self) -> None:
        diffs = [
            _diff(path="app/a.py", change_type="modified", hunks=(_hunk("x"),)),
            _diff(path="app/a.py", change_type="modified", hunks=(_hunk("y"),)),
        ]
        with pytest.raises(ScopeAssessmentError) as excinfo:
            assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert excinfo.value.reason_code == SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2

    def test_delete_plus_add_pair_is_type_change(self) -> None:
        diffs = [
            _diff(path="app/link", change_type="deleted", hunks=()),
            _diff(path="app/link", change_type="added", hunks=()),
        ]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert assessment.disposition_of("app/link") is PathDispositionV2.TYPE_CHANGE
        assert not assessment.scope_complete

    def test_three_blocks_same_path_refuses_even_if_two_are_delete_add(self) -> None:
        diffs = [
            _diff(path="app/x", change_type="deleted", hunks=()),
            _diff(path="app/x", change_type="added", hunks=()),
            _diff(path="app/x", change_type="modified", hunks=(_hunk(),)),
        ]
        with pytest.raises(ScopeAssessmentError) as excinfo:
            assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert excinfo.value.reason_code == SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2

    def test_ordinary_rename_does_not_make_scope_incomplete(self) -> None:
        diffs = [_diff(path="app/renamed.py", change_type="renamed", hunks=())]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert assessment.scope_complete is True
        assert assessment.metadata_only_paths == ("app/renamed.py",)
        assert assessment.unsupported_paths == ()

    def test_binary_makes_scope_incomplete_but_not_blocked(self) -> None:
        diffs = [_diff(path="assets/logo.png", is_binary=True)]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert assessment.scope_complete is False
        assert assessment.blocked is False
        assert assessment.unsupported_paths == ("assets/logo.png",)

    def test_must_review_binary_is_blocked_and_stronger_than_incomplete(self) -> None:
        diffs = [_diff(path="assets/logo.png", is_binary=True)]
        profile = _profile(must_review_paths=["assets/logo.png"])
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=profile)
        assert assessment.scope_complete is False
        assert assessment.blocked is True
        assert assessment.must_review_blocked_paths == ("assets/logo.png",)

    def test_must_review_pure_rename_is_blocked_even_though_scope_stays_complete(self) -> None:
        """ADR-200F's own distinction: `blocked` is strictly stronger than
        `not scope_complete` -- a required rename produced nothing
        reviewable and blocks the run, even though an ORDINARY rename
        would leave `scope_complete` True."""
        diffs = [_diff(path="app/renamed.py", change_type="renamed", hunks=())]
        profile = _profile(must_review_paths=["app/renamed.py"])
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=profile)
        assert assessment.scope_complete is True
        assert assessment.blocked is True
        assert assessment.must_review_blocked_paths == ("app/renamed.py",)

    def test_must_review_pattern_matches(self) -> None:
        diffs = [_diff(path="app/models/user.py", is_binary=True)]
        profile = _profile(must_review_patterns=["app/models/*.py"])
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=profile)
        assert assessment.blocked is True

    def test_reviewable_and_unsupported_paths_are_both_accounted_for(self) -> None:
        diffs = [
            _diff(path="app/a.py", hunks=(_hunk("a"),)),
            _diff(path="assets/x.png", is_binary=True),
            _diff(path="app/renamed.py", change_type="renamed", hunks=()),
        ]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert set(assessment.accounted_paths) == {"app/a.py", "assets/x.png", "app/renamed.py"}
        assert assessment.reviewable_paths == ("app/a.py",)
        assert assessment.unsupported_paths == ("assets/x.png",)
        assert assessment.metadata_only_paths == ("app/renamed.py",)
        assert assessment.scope_complete is False


# -- ScopeAssessmentV2 -> ScopeCompletenessV2 -----------------------------------


class TestToScopeCompletenessV2:
    def test_fully_complete_case_round_trips(self) -> None:
        diffs = [
            _diff(path="app/a.py", hunks=(_hunk("a"),)),
            _diff(path="app/renamed.py", change_type="renamed", hunks=()),
        ]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        published = assessment.to_scope_completeness_v2()
        assert isinstance(published, ScopeCompletenessV2)
        assert published.complete is True
        assert set(published.changed_paths) == {"app/a.py", "app/renamed.py"}
        assert published.unsupported_paths == ()
        assert published.must_review_blocked_paths == ()

    def test_fragments_complete_scope_incomplete_case_round_trips(self) -> None:
        """The exact case this whole primitive exists to represent
        honestly: fragment coverage over `app/a.py` is complete, but the
        binary `assets/logo.png` also changed and this product cannot
        represent it -- total scope is NOT complete."""
        diffs = [
            _diff(path="app/a.py", hunks=(_hunk("a"),)),
            _diff(path="assets/logo.png", is_binary=True),
        ]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        published = assessment.to_scope_completeness_v2()
        assert published.complete is False
        assert published.reviewable_paths == ("app/a.py",)
        assert published.unsupported_paths == ("assets/logo.png",)

    def test_duplicate_path_raises_from_dataclass_construction_too(self) -> None:
        with pytest.raises(ScopeAssessmentError):
            ScopeAssessmentV2(
                path_dispositions=(
                    ("app/a.py", PathDispositionV2.REVIEWABLE),
                    ("app/a.py", PathDispositionV2.METADATA_ONLY),
                ),
                must_review_paths=frozenset(),
            )


# -- the disagreement detector --------------------------------------------------


class TestScopeAuthorityAgreesWithAssembly:
    def test_agreement_passes_silently(self) -> None:
        diffs = [_diff(path="app/a.py", hunks=(_hunk(),))]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        assert_scope_authority_agrees_with_assembly_v2(assessment, assembly_expected_files=["app/a.py"])

    def test_assembly_claims_a_path_the_scope_authority_does_not(self) -> None:
        diffs = [_diff(path="app/a.py", hunks=(_hunk(),))]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        with pytest.raises(ScopeAssessmentError) as excinfo:
            assert_scope_authority_agrees_with_assembly_v2(
                assessment, assembly_expected_files=["app/a.py", "app/phantom.py"]
            )
        assert excinfo.value.reason_code == SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2

    def test_scope_authority_claims_a_path_the_assembly_excluded(self) -> None:
        """This is the exact `#277` round-1 shape, reconstructed: if a
        future change reintroduced a second, independent representability
        check that disagreed with `path_violates_relative_path_contract_v2`,
        the scope authority could certify `reviewable` for a path the
        assembly actually excluded. Simulated here directly, without
        needing to actually break the shared predicate, by handing the
        detector a scope authority result that (hypothetically) diverges."""
        diffs = [_diff(path="app/a.py", hunks=(_hunk(),))]
        assessment = assess_changed_scope_v2(file_diffs=diffs, profile=_profile())
        with pytest.raises(ScopeAssessmentError) as excinfo:
            assert_scope_authority_agrees_with_assembly_v2(assessment, assembly_expected_files=[])
        assert excinfo.value.reason_code == SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2


# -- path_violates_relative_path_contract_v2 is genuinely shared ---------------


def test_shared_predicate_is_the_same_object_both_modules_call() -> None:
    """Structural proof, not merely behavioral: `operational_scope_v2`
    imports the exact function object from `diff_acquisition_v2` rather
    than defining its own. A future silent reimplementation would show up
    here as two different function objects."""
    import app.agent_review.operational_scope_v2 as scope_module

    assert scope_module.path_violates_relative_path_contract_v2 is path_violates_relative_path_contract_v2


def test_shared_predicate_rejects_the_witness_path() -> None:
    assert path_violates_relative_path_contract_v2("src/pages/[id].tsx") is True


def test_shared_predicate_accepts_an_ordinary_path() -> None:
    assert path_violates_relative_path_contract_v2("app/models/user.py") is False


# -- real-git fuzz: actual repos, actual git diffs ------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"},
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


@pytest.mark.parametrize(
    "scenario",
    ["rename", "chmod", "binary", "empty_file_add", "empty_file_delete", "type_change_symlink"],
)
def test_real_git_scenarios_classify_as_expected(tmp_path: Path, scenario: str) -> None:
    repo = _init_repo(tmp_path)

    if scenario == "rename":
        (repo / "old.py").write_text("print(1)\n")
        _git(repo, "add", "old.py")
        _git(repo, "commit", "-q", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "mv", "old.py", "new.py")
        _git(repo, "commit", "-q", "-m", "rename")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.RENAME

    elif scenario == "chmod":
        target = repo / "run.sh"
        target.write_text("#!/bin/sh\necho hi\n")
        target.chmod(0o644)
        _git(repo, "add", "run.sh")
        _git(repo, "commit", "-q", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        target.chmod(0o755)
        _git(repo, "add", "run.sh")
        _git(repo, "commit", "-q", "-m", "chmod")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.CHMOD_ONLY

    elif scenario == "binary":
        _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "blob.bin").write_bytes(bytes(range(256)))
        _git(repo, "add", "blob.bin")
        _git(repo, "commit", "-q", "-m", "binary")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.BINARY_UNSUPPORTED

    elif scenario == "empty_file_add":
        _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "empty.txt").write_text("")
        _git(repo, "add", "empty.txt")
        _git(repo, "commit", "-q", "-m", "add empty")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.EMPTY_FILE_TRANSITION

    elif scenario == "empty_file_delete":
        (repo / "empty.txt").write_text("")
        _git(repo, "add", "empty.txt")
        _git(repo, "commit", "-q", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "rm", "-q", "empty.txt")
        _git(repo, "commit", "-q", "-m", "delete empty")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.EMPTY_FILE_TRANSITION

    elif scenario == "type_change_symlink":
        (repo / "thing").write_text("regular file content\n")
        _git(repo, "add", "thing")
        _git(repo, "commit", "-q", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "thing").unlink()
        (repo / "thing").symlink_to("elsewhere")
        _git(repo, "add", "thing")
        _git(repo, "commit", "-q", "-m", "type change")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        expected = PathDispositionV2.TYPE_CHANGE

    else:  # pragma: no cover - exhaustive parametrize guard
        raise AssertionError(scenario)

    diff_text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
    blocks = parse_unified_diff(diff_text)

    if expected is PathDispositionV2.TYPE_CHANGE:
        assert len(blocks) == 2
        from app.agent_review.operational_scope_v2 import _is_type_change_pair_v2

        assert _is_type_change_pair_v2(blocks) is True
    else:
        assert len(blocks) == 1
        assert classify_changed_path_v2(blocks[0]) is expected
