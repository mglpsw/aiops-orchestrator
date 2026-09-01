"""`#200-F` authority C -- every changed path gets an explicit disposition.

Carries the `#276` round-4 regression corpus as first-class cases. Under the
predecessor's `if assembly.excluded_paths: raise`, each of pure rename,
chmod-only, binary, lockfile, image and empty-file add denied the *entire*
review. Here they are dispositioned instead, and the review continues.
"""

from __future__ import annotations

import pytest

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.diff_acquisition_v2 import parse_unified_diff
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2
from app.agent_review.operational_scope_v2 import (
    SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2,
    PathDispositionV2,
    ScopeAssessmentError,
    assess_changed_scope_v2,
    classify_changed_path_v2,
)

_ORDINARY_DIFF_V2 = """diff --git a/src/a.py b/src/a.py
index 1111111..2222222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
 z = 3
"""

_PURE_RENAME_DIFF_V2 = """diff --git a/src/old.py b/src/new.py
similarity index 100%
rename from src/old.py
rename to src/new.py
"""

_CHMOD_ONLY_DIFF_V2 = """diff --git a/scripts/run.sh b/scripts/run.sh
old mode 100644
new mode 100755
"""

_BINARY_DIFF_V2 = """diff --git a/assets/logo.png b/assets/logo.png
index 3333333..4444444 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""

_LOCKFILE_BINARY_DIFF_V2 = """diff --git a/poetry.lock b/poetry.lock
index 5555555..6666666 100644
Binary files a/poetry.lock and b/poetry.lock differ
"""

_EMPTY_ADD_DIFF_V2 = """diff --git a/src/empty.py b/src/empty.py
new file mode 100644
index 0000000..e69de29
"""

_EMPTY_DELETE_DIFF_V2 = """diff --git a/src/gone.py b/src/gone.py
deleted file mode 100644
index e69de29..0000000
"""

_SUBMODULE_DIFF_V2 = """diff --git a/vendor/lib b/vendor/lib
index aaaaaaa..bbbbbbb 160000
--- a/vendor/lib
+++ b/vendor/lib
@@ -1 +1 @@
-Subproject commit aaaaaaa
+Subproject commit bbbbbbb
"""


def _profile_v2(
    *,
    must_review_paths: tuple[str, ...] = (),
    must_review_patterns: tuple[str, ...] = (),
) -> TargetProfileV2:
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
            "contracts": [],
            "limitations": [],
        }
    )


def _assess_v2(*diff_texts: str, profile: TargetProfileV2 | None = None):
    file_diffs = [
        file_diff
        for diff_text in diff_texts
        for file_diff in parse_unified_diff(diff_text)
    ]
    return assess_changed_scope_v2(
        file_diffs=file_diffs, profile=profile or _profile_v2()
    )


@pytest.mark.parametrize(
    "diff_text, expected_path, expected_disposition",
    [
        (_ORDINARY_DIFF_V2, "src/a.py", PathDispositionV2.REVIEWABLE),
        (_PURE_RENAME_DIFF_V2, "src/new.py", PathDispositionV2.METADATA_ONLY),
        (_CHMOD_ONLY_DIFF_V2, "scripts/run.sh", PathDispositionV2.METADATA_ONLY),
        (_BINARY_DIFF_V2, "assets/logo.png", PathDispositionV2.UNSUPPORTED),
        (_LOCKFILE_BINARY_DIFF_V2, "poetry.lock", PathDispositionV2.UNSUPPORTED),
        (_EMPTY_ADD_DIFF_V2, "src/empty.py", PathDispositionV2.METADATA_ONLY),
        (_EMPTY_DELETE_DIFF_V2, "src/gone.py", PathDispositionV2.METADATA_ONLY),
        (_SUBMODULE_DIFF_V2, "vendor/lib", PathDispositionV2.METADATA_ONLY),
    ],
)
def test_every_required_change_class_receives_a_disposition(
    diff_text: str, expected_path: str, expected_disposition: PathDispositionV2
) -> None:
    """The grant's §7 class list, one case each.

    Under `#276` every non-reviewable row here denied the whole review.
    """
    (file_diff,) = parse_unified_diff(diff_text)

    assert file_diff.path == expected_path
    assert classify_changed_path_v2(file_diff) is expected_disposition


def test_a_truncated_patch_is_unsupported_even_if_it_looks_textual() -> None:
    """Truncation is checked first, deliberately.

    A patch can be cut off anywhere, including before the marker that would
    have identified it as binary. Nothing after the truncation point is
    trustworthy, so the fail-safe reading is the only sound one.
    """
    (file_diff,) = parse_unified_diff(_ORDINARY_DIFF_V2)
    truncated = type(file_diff)(**{**file_diff.__dict__, "truncated": True})

    assert classify_changed_path_v2(truncated) is PathDispositionV2.UNSUPPORTED


def test_a_submodule_is_metadata_not_an_unsupported_binary() -> None:
    """Ordering guard.

    A gitlink is a 40-byte pointer. Filing it as unsupported would invent a
    capability gap and make ``ready`` unreachable for every submodule bump.
    """
    assessment = _assess_v2(_SUBMODULE_DIFF_V2)

    assert assessment.metadata_only_paths == ("vendor/lib",)
    assert assessment.unsupported_paths == ()
    assert assessment.scope_complete is True


def test_metadata_only_changes_do_not_make_scope_incomplete() -> None:
    """The core reversal of the `#276` regression.

    Two reviewed files plus a rename, a chmod and an empty add. Every path is
    accounted for, none carries reviewable material that was skipped, so scope
    is complete and the review proceeds. The predecessor refused this run.
    """
    assessment = _assess_v2(
        _ORDINARY_DIFF_V2,
        _PURE_RENAME_DIFF_V2,
        _CHMOD_ONLY_DIFF_V2,
        _EMPTY_ADD_DIFF_V2,
    )

    assert assessment.reviewable_paths == ("src/a.py",)
    assert assessment.metadata_only_paths == (
        "scripts/run.sh",
        "src/empty.py",
        "src/new.py",
    )
    assert assessment.unsupported_paths == ()
    assert assessment.scope_complete is True
    assert assessment.blocked is False


def test_unsupported_material_makes_total_scope_incomplete() -> None:
    """A binary carries material we cannot render. That is a real gap.

    Distinct from the metadata-only case above: review may still continue, but
    ``ready`` must become impossible.
    """
    assessment = _assess_v2(_ORDINARY_DIFF_V2, _BINARY_DIFF_V2)

    assert assessment.reviewable_paths == ("src/a.py",)
    assert assessment.unsupported_paths == ("assets/logo.png",)
    assert assessment.scope_complete is False
    assert assessment.blocked is False, (
        "an ordinary binary makes scope incomplete but must not fail closed"
    )


def test_a_must_review_path_that_is_unreviewable_fails_closed() -> None:
    """The target declared it must be examined; we could not examine it.

    "There was nothing to look at" is a conclusion only a human is entitled to
    draw about material the profile marked as required.
    """
    profile = _profile_v2(must_review_paths=("assets/logo.png",))
    assessment = _assess_v2(_ORDINARY_DIFF_V2, _BINARY_DIFF_V2, profile=profile)

    assert assessment.must_review_blocked_paths == ("assets/logo.png",)
    assert assessment.blocked is True
    assert assessment.scope_complete is False


def test_a_must_review_pattern_blocks_a_metadata_only_path_too() -> None:
    """Even a pure rename blocks if the profile required that path reviewed.

    Absence of material does not discharge an explicit must-review obligation.
    """
    profile = _profile_v2(must_review_patterns=("src/*.py",))
    assessment = _assess_v2(_PURE_RENAME_DIFF_V2, profile=profile)

    assert assessment.metadata_only_paths == ("src/new.py",)
    assert assessment.must_review_blocked_paths == ("src/new.py",)
    assert assessment.blocked is True


def test_a_reviewable_must_review_path_is_not_blocked() -> None:
    """Non-vacuity control for both must-review tests above.

    Without it, a matcher that flagged *everything* would satisfy them.
    """
    profile = _profile_v2(must_review_patterns=("src/*.py",))
    assessment = _assess_v2(_ORDINARY_DIFF_V2, profile=profile)

    assert assessment.reviewable_paths == ("src/a.py",)
    assert assessment.must_review_blocked_paths == ()
    assert assessment.blocked is False
    assert assessment.scope_complete is True


def test_no_changed_path_is_ever_dropped() -> None:
    """The property the whole authority exists to guarantee.

    Asserted as a partition over the union of dispositions rather than by
    spot-checking, so a future class that fell through every branch would
    surface here instead of vanishing.
    """
    assessment = _assess_v2(
        _ORDINARY_DIFF_V2,
        _PURE_RENAME_DIFF_V2,
        _CHMOD_ONLY_DIFF_V2,
        _BINARY_DIFF_V2,
        _LOCKFILE_BINARY_DIFF_V2,
        _EMPTY_ADD_DIFF_V2,
        _EMPTY_DELETE_DIFF_V2,
        _SUBMODULE_DIFF_V2,
    )

    assert assessment.accounted_paths == assessment.changed_paths
    assert len(assessment.changed_paths) == 8

    partitions = (
        set(assessment.reviewable_paths),
        set(assessment.metadata_only_paths),
        set(assessment.unsupported_paths),
    )
    for left_index, left in enumerate(partitions):
        for right in partitions[left_index + 1 :]:
            assert not (left & right), "dispositions must be mutually exclusive"


def test_a_duplicate_changed_path_is_refused_not_coalesced() -> None:
    """Silently keeping one of two diffs for the same path is a loss.

    Exactly the class of disappearance this authority exists to prevent, so it
    refuses rather than picking a winner.
    """
    with pytest.raises(ScopeAssessmentError) as caught:
        _assess_v2(_ORDINARY_DIFF_V2, _ORDINARY_DIFF_V2)

    assert caught.value.reason_code == SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_an_empty_change_set_is_complete_and_unblocked() -> None:
    """Degenerate case, pinned so it cannot drift into a refusal."""
    assessment = _assess_v2()

    assert assessment.changed_paths == ()
    assert assessment.scope_complete is True
    assert assessment.blocked is False
