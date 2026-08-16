"""Production-path mutation discrimination for the TargetProfile YAML
authority (`#238`).

Each test here monkeypatches a PRODUCTION symbol in
``app.agent_review.profile_loader_v2`` -- never a parallel mutant loader
built inside the test -- and exercises a PRODUCTION entry point. A parallel
mutant loader would prove only that a *copy* of the design fails without
the guard; it says nothing about whether the guard is actually on the path
production runs. Each mutation proves three things:

1. the REAL (unmutated) authority has the intended disposition;
2. the monkeypatch actually replaced the production symbol (guards against
   a silently-ineffective patch that would let a broken test pass);
3. the MUTATED production path behaves differently.

M1/M2 are observed through ``_read_unambiguously_v2`` -- the reading layer
-- rather than ``load_target_profile_text_v2``, because most corpus cases
are fragments that contract validation would refuse for unrelated reasons,
which would mask the mutation behind a same-looking failure (the same
reason `legal` cases are read through this entry point; see
`docs/adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md`).

M3 was step-4-observed at ``load_target_profile_text_v2`` against every
`invalid`-classified corpus case before a case was chosen: `simple_merge`
(the case named in the original mutation-discrimination sketch) parses
without a residual collision once the merge guard is bypassed and is then
refused only by contract validation -- with the SAME reason-code string as
the real, merge-language-exclusion refusal, so it does NOT discriminate at
that entry point. `duplicate_merge_keys` does: bypassing the guard sends
its two `<<:` pairs through stock `flatten_mapping`, which splices both
merge sources' `k` key into the mapping and re-triggers collision point 1,
flipping the observed reason code from `target_profile_invalid` to
`target_profile_unreadable`. See `CORPUS.json`'s `duplicate_merge_keys`
and `simple_merge` records for the observation notes.

M4 -- that contract validation does not re-serialise the parsed object
(the seam that makes ``manufactured_json_duplicate`` reachable at all) --
is preserved verbatim as
``test_the_validated_object_is_the_parsed_object_not_a_reserialisation``
in ``test_profile_loader_v2.py``, not duplicated here.
"""

from __future__ import annotations

import pytest
import yaml

import app.agent_review.profile_loader_v2 as module
from tests.agent_review.target_profile_yaml_corpus import case as corpus_case
from tests.agent_review.target_profile_yaml_corpus import cases as corpus_cases

# Every corpus case's `mutation_target`, cross-checked so a case cannot
# silently stop being exercised by any mutation test.
_COVERED_MUTATION_TARGETS = frozenset({"collision_point_1", "collision_point_2", "merge_bypass"})


def test_every_declared_mutation_target_is_covered_by_a_mutation_test() -> None:
    declared = {c.mutation_target for c in corpus_cases() if c.mutation_target is not None}
    assert declared == _COVERED_MUTATION_TARGETS


def test_m1_collision_point_1_mapping_assignment_discriminates() -> None:
    """mutation_target: collision_point_1 -- plain_duplicate_divergent.

    Real: `construct_mapping` refuses the moment it would have to resolve
    a duplicate authored key silently. Mutated (stock `construct_mapping`
    restored): the same production `_read_unambiguously_v2` call silently
    accepts the document, last-wins -- exactly the pre-#237 behaviour this
    authority exists to refuse.
    """
    case = corpus_case("plain_duplicate_divergent")
    assert case.mutation_target == "collision_point_1"

    with pytest.raises(module.TargetProfileLoadErrorV2) as excinfo:
        module._read_unambiguously_v2(case.text)
    assert excinfo.value.reason_code == case.expected_reason_code

    original = module._CollisionRefusingSafeLoaderV2.construct_mapping
    stock = yaml.SafeLoader.construct_mapping
    module._CollisionRefusingSafeLoaderV2.construct_mapping = stock
    try:
        assert module._CollisionRefusingSafeLoaderV2.construct_mapping is stock
        assert module._CollisionRefusingSafeLoaderV2.construct_mapping is not original

        mutated_value = module._read_unambiguously_v2(case.text)
        assert mutated_value == {"identity": {"repo": "attacker/evil"}}, (
            "mutation did not discriminate: the document is still refused "
            "with the collision-observing guard bypassed"
        )
    finally:
        module._CollisionRefusingSafeLoaderV2.construct_mapping = original
        assert module._CollisionRefusingSafeLoaderV2.construct_mapping is original


def test_m2_collision_point_2_value_tag_candidates_discriminates() -> None:
    """mutation_target: collision_point_2 -- tagged_str_duplicate_value_key.

    Real: `construct_scalar` refuses when a mapping consumed as a scalar
    (a `!!value`-tagged key) offers more than one candidate. Mutated
    (stock `construct_scalar` restored): the first `tag:yaml.org,2002:value`
    candidate silently wins, exactly stock PyYAML's own documented
    behaviour for this construct.
    """
    case = corpus_case("tagged_str_duplicate_value_key")
    assert case.mutation_target == "collision_point_2"

    with pytest.raises(module.TargetProfileLoadErrorV2) as excinfo:
        module._read_unambiguously_v2(case.text)
    assert excinfo.value.reason_code == case.expected_reason_code

    original = module._CollisionRefusingSafeLoaderV2.construct_scalar
    stock = yaml.SafeLoader.construct_scalar
    module._CollisionRefusingSafeLoaderV2.construct_scalar = stock
    try:
        assert module._CollisionRefusingSafeLoaderV2.construct_scalar is stock
        assert module._CollisionRefusingSafeLoaderV2.construct_scalar is not original

        mutated_value = module._read_unambiguously_v2(case.text)
        assert mutated_value == {"m": {"repo": "v"}}, (
            "mutation did not discriminate: the document is still refused "
            "with the value-tag-candidate guard bypassed"
        )
    finally:
        module._CollisionRefusingSafeLoaderV2.construct_scalar = original
        assert module._CollisionRefusingSafeLoaderV2.construct_scalar is original


def test_m3_merge_bypass_changes_the_observed_reason_code() -> None:
    """mutation_target: merge_bypass -- duplicate_merge_keys.

    Real: `_document_uses_merge_v2` sees `<<:` anywhere in the document and
    `_read_unambiguously_v2` refuses immediately with
    `target_profile_invalid`, before `yaml.load` ever runs. Mutated
    (guard bypassed): the document reaches stock `flatten_mapping`, which
    splices both merge sources' `k` pair into the mapping -- reintroducing
    a genuine collision that collision point 1 (unmutated) now catches,
    changing the reason code to `target_profile_unreadable`.
    """
    case = corpus_case("duplicate_merge_keys")
    assert case.mutation_target == "merge_bypass"

    with pytest.raises(module.TargetProfileLoadErrorV2) as excinfo:
        module.load_target_profile_text_v2(case.text)
    real_reason = excinfo.value.reason_code
    assert real_reason == case.expected_reason_code == "target_profile_invalid"

    original = module._document_uses_merge_v2

    def _bypass(_raw_text: str) -> bool:
        return False

    module._document_uses_merge_v2 = _bypass
    try:
        assert module._document_uses_merge_v2 is _bypass
        assert module._document_uses_merge_v2 is not original

        with pytest.raises(module.TargetProfileLoadErrorV2) as mutated_excinfo:
            module.load_target_profile_text_v2(case.text)
        mutated_reason = mutated_excinfo.value.reason_code
    finally:
        module._document_uses_merge_v2 = original
        assert module._document_uses_merge_v2 is original

    assert mutated_reason == "target_profile_unreadable"
    assert mutated_reason != real_reason, (
        "mutation did not discriminate: bypassing the merge guard produced "
        "the same reason code as the real, merge-exclusion refusal"
    )


def test_m3_counterexample_simple_merge_does_not_discriminate_at_this_entry_point() -> None:
    """Records, executably, the step-4 finding that ruled `simple_merge`
    out as the M3 exemplar: it is refused with the SAME reason-code string
    whether or not the merge guard is bypassed, because the post-bypass
    parse hits unrelated contract validation instead of a residual
    collision. This is why `duplicate_merge_keys` -- not `simple_merge` --
    carries `mutation_target: merge_bypass` in `CORPUS.json`.
    """
    case = corpus_case("simple_merge")
    assert case.mutation_target is None

    with pytest.raises(module.TargetProfileLoadErrorV2) as excinfo:
        module.load_target_profile_text_v2(case.text)
    real_reason = excinfo.value.reason_code

    original = module._document_uses_merge_v2
    module._document_uses_merge_v2 = lambda _t: False
    try:
        with pytest.raises(module.TargetProfileLoadErrorV2) as mutated_excinfo:
            module.load_target_profile_text_v2(case.text)
        mutated_reason = mutated_excinfo.value.reason_code
    finally:
        module._document_uses_merge_v2 = original

    assert mutated_reason == real_reason == "target_profile_invalid"
