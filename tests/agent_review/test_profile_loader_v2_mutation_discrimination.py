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

Round-1 review of `#238`/PR #239 found the original coverage test compared
SETS of declared `mutation_target` labels -- proving only that each label
occurs somewhere in the corpus, not that the case declaring it is actually
exercised by a mutation test. A second case silently reusing an
already-covered label left the set unchanged and the coverage test green
while exercising nothing new. `CORPUS.json` is now the only authority for
which case is which mutation's exemplar (`mutation_target != null` means
"the sole exemplar of that target", enforced exactly-once by
`load_corpus`); the fix is to consume `mutation_case(target)` directly
instead of maintaining a second, parallel table of exemplars here.
"""

from __future__ import annotations

import pytest
import yaml

import app.agent_review.profile_loader_v2 as module
from tests.agent_review.target_profile_yaml_corpus import MUTATION_TARGETS
from tests.agent_review.target_profile_yaml_corpus import case as corpus_case
from tests.agent_review.target_profile_yaml_corpus import cases as corpus_cases
from tests.agent_review.target_profile_yaml_corpus import mutation_case


def _run_collision_point_1(case) -> None:
    """mutation_target: collision_point_1 -- plain_duplicate_divergent.

    Real: `construct_mapping` refuses the moment it would have to resolve
    a duplicate authored key silently. Mutated (stock `construct_mapping`
    restored): the same production `_read_unambiguously_v2` call silently
    accepts the document, last-wins -- exactly the pre-#237 behaviour this
    authority exists to refuse.
    """
    assert case.case_id == "plain_duplicate_divergent"

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


def _run_collision_point_2(case) -> None:
    """mutation_target: collision_point_2 -- tagged_str_duplicate_value_key.

    Real: `construct_scalar` refuses when a mapping consumed as a scalar
    (a `!!value`-tagged key) offers more than one candidate. Mutated
    (stock `construct_scalar` restored): the first `tag:yaml.org,2002:value`
    candidate silently wins, exactly stock PyYAML's own documented
    behaviour for this construct.
    """
    assert case.case_id == "tagged_str_duplicate_value_key"

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


def _run_merge_bypass(case) -> None:
    """mutation_target: merge_bypass -- duplicate_merge_keys.

    Real: `_document_uses_merge_v2` sees `<<:` anywhere in the document and
    `_read_unambiguously_v2` refuses immediately with
    `target_profile_invalid`, before `yaml.load` ever runs. Mutated
    (guard bypassed): the document reaches stock `flatten_mapping`, which
    splices both merge sources' `k` pair into the mapping -- reintroducing
    a genuine collision that collision point 1 (unmutated) now catches,
    changing the reason code to `target_profile_unreadable`.
    """
    assert case.case_id == "duplicate_merge_keys"

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


# mutation_target -> the mutation that actually EXECUTES for it. Round-6
# redesign: this replaces a separate "every target resolves to an exemplar"
# closure test, which asserted a property of DECLARATIONS (every vocabulary
# member has a corpus exemplar) and was therefore satisfiable while no
# mutation test consumed the target at all.
_MUTATION_IMPLEMENTATIONS = {
    "collision_point_1": _run_collision_point_1,
    "collision_point_2": _run_collision_point_2,
    "merge_bypass": _run_merge_bypass,
}

# Parametrized from the UNION of vocabulary and implementations, so both
# directions of drift are collection-time failures rather than assertions
# someone must remember to keep true:
#   target in MUTATION_TARGETS, no implementation -> KeyError here
#   target implemented, not in the vocabulary      -> mutation_case() raises
#   target in MUTATION_TARGETS, no corpus exemplar -> mutation_case() raises
_MUTATION_TARGETS_UNDER_TEST = sorted(MUTATION_TARGETS | set(_MUTATION_IMPLEMENTATIONS))


@pytest.mark.parametrize("mutation_target", _MUTATION_TARGETS_UNDER_TEST)
def test_mutation_target_discriminates_on_the_production_path(mutation_target: str) -> None:
    """Every `MUTATION_TARGETS` member is EXECUTED, not merely declared.

    There is no separate coverage assertion to keep in sync: a target with
    no implementation, no corpus exemplar, or an implementation for a
    target the vocabulary does not contain, each fail here by construction.
    """
    case = mutation_case(mutation_target)
    assert case.mutation_target == mutation_target
    _MUTATION_IMPLEMENTATIONS[mutation_target](case)


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


def test_document_uses_merge_v2_detects_every_merge_form_in_the_corpus() -> None:
    """Round-2 finding: M3 exercises `_document_uses_merge_v2` only through
    one mutation that removes it entirely (`lambda _t: False`), on one
    exemplar case. A regression that still recognizes `duplicate_merge_keys`'s
    shape but misses a different merge form -- e.g. a single-source merge
    with no residual collision, which would then be silently accepted as a
    valid document rather than refused for using a language construct the
    profile does not accept -- would not be caught by M3 alone, since
    M3 measures the END-TO-END disposition, and a partial detection miss on
    a DIFFERENT case is invisible from that one case's result.

    This tests the detector seam directly, at every merge-shaped fixture in
    the corpus (`property_family == "merge_key_unsupported"`), independent
    of what `load_target_profile_text_v2` ultimately does with the result.
    """
    merge_cases = [c for c in corpus_cases("invalid") if c.property_family == "merge_key_unsupported"]
    assert len(merge_cases) == 7, "expected all 7 merge-shaped corpus fixtures"
    for merge_case in merge_cases:
        assert module._document_uses_merge_v2(merge_case.text) is True, merge_case.case_id
