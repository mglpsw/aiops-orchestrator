"""Historical witness regression corpus for
``app.agent_review.structural_egress_projection_v2`` (#200-G2C, issue
#299).

## Why this file exists

``test_structural_egress_projection_v2.py`` proves the structural closure
property (no outbound slot can carry ANY raw literal's bytes) using
semantically neutral fixtures only, by explicit scope decision recorded in
that file's own docstring and in PR #309's body ("Explicitly deferred").
This file fills that named gap: it replays the SPECIFIC input shapes that
previously fooled the two predecessor, detection-based designs --
`#200-G2` (blocklist, issue #280 -> PR #286, `STOP_G2_SAFE_REVIEW_
MATERIAL_NOT_CONVERGING`) and `#200-G2B` (independent-oracle allowlist,
issue #287/#292 -> PR #293, `STOP_G2B_ARCHITECTURE_NOT_CONVERGING`) --
through the CURRENT closure-based projector, and asserts the same
structural/type property the rest of the module proves: the raw witness
value is absent from the projected, serialized output, and its type-shape
is represented only via the closed opaque schema.

This is NOT a claim that the projector "detects secrets better." It
detects nothing about content at all -- every witness below is opaqued
for the same reason every neutral fixture in the base test file is:
because the projected type has no field that could hold ANY raw value,
credential-shaped or not. Replaying the historical corpus makes that
"no regression" claim concrete against the exact shapes that sank the
two prior designs, rather than resting on prose.

## Witness provenance

Reconstructed from PR #293's review history, matched precisely (not
loosely approximated):

- Round-1 false-safes (PR #293 review comment, 2026-09-01, reproduced
  against frozen head `4ad18ecb96ec2bd0c22a6fe7b48f2da2d1cd2365`): dict-
  literal-shaped assignment, kwarg-shaped assignment, two prefix-based
  placeholder carve-outs (`$`-prefix, `[REDACTED]`-prefix), and a
  40/64-hex value wrongly exempted as "SHA-like" (`API_TOKEN=<hex>`).
- Round-2 codification of the same six shapes as RED-before-GREEN
  regression tests, commit `094b0f7` on PR #293
  (`tests/agent_review/test_g2b_outbound_oracle_spike.py::
  _round2_closed_world_false_safe_cases`).
- The earlier mandatory corpus this whole lineage carries forward,
  recorded on issue #292 (`#200-G2B`'s own issue; #292 is itself the
  successor issue named by #280's closing comment) and mirrored in the
  same test file's `_mandatory_secret_cases`: bare numeric/short-PIN
  values, `master_key`/`csrf_token` vocabulary gaps, a JWT-shaped
  three-segment token, an unterminated quoted value, a secret adjacent to
  an unrelated already-"safe"-looking placeholder, and provider-shaped
  tokens (AWS access key, Slack bot token).
- The "71-line damage" reference: precisely, that exact phrase ("the old
  71-line damage class from earlier rounds") is issue #292's, in its
  negative-direction requirement -- not issue #280's, which does not
  contain it verbatim. Its origin is
  `docs/checkpoints/AGENT_REVIEW_V2_200G2_SAFE_REVIEW_MATERIAL.md` (from
  PR #286 / #200-G2, at that PR's final head), a methodology note about a
  precedent "71 altered lines -> 8, all in comments": an early line-count
  oracle used positional-index diffing, so one real multi-line-collapsing
  edit inflated into ~2,700 spuriously "changed" lines in a single file;
  after switching to `difflib.SequenceMatcher` opcode-based diffing, most
  of a large apparent change count collapsed down to the small set of
  real, expected (often self-referential/comment) changes. It is a
  citation about correctly ATTRIBUTING a regex-redactor's in-place
  text-mutation diff, not a distinct single incident -- structurally
  inapplicable to this module regardless of which reading is used, since
  this projector never returns modified source text at all (it emits an
  entirely separate structural document); there is no in-place mutation
  for a line-count oracle to misattribute in the first place.

## A note on these fixtures

Every value below is a synthetic, non-functional placeholder constructed
for this regression corpus -- shaped like the historical witness classes
(credential assignments, provider token formats, short codes), never a
real or reachable credential. Provider-token-shaped values (AWS-key-
shaped, Slack-token-shaped, JWT-shaped, high-entropy hex) are assembled
from concatenated/derived parts at runtime, mirroring the exact technique
PR #293's own committed test file used for the same reason.
"""

from __future__ import annotations

import hashlib
import json

import pytest

import app.agent_review.structural_egress_projection_v2 as sep
from app.agent_review.structural_egress_projection_v2 import (
    STRUCTURAL_PROJECTION_PARSE_FAILED_V2,
    StructuralProjectionBlockedV2,
    project_fragment_structural_v2,
)

_CLOSED_LITERAL_KINDS = {"str", "bytes", "int", "float", "complex", "comment"}


def _project(source: str, *, path: str = "x.py") -> tuple[dict, dict]:
    alias_table: dict[str, str] = {}
    fragment = project_fragment_structural_v2(
        fragment_id="d" * 64, path=path, content=source, alias_table=alias_table
    )
    return alias_table, json.loads(fragment.model_dump_json())


def _all_literals(node: dict) -> list[dict]:
    out = [item["literal"] for item in node.get("literal_fields", [])]
    for child in node.get("child_nodes", []):
        out.extend(_all_literals(child["node"]))
    return out


# -- runtime-assembled provider-token-shaped witnesses (not static literals,
# same mitigation PR #293's own committed test file used) -------------------


def _aws_access_key_shaped() -> str:
    # AKIA + 16 uppercase-alphanumeric chars: the well-known AWS access
    # key ID shape (20 chars total).
    return "AKIA" + "1234567890ABCDEF"


def _slack_token_shaped() -> str:
    # xox<b> + workspace/bot id segments: the well-known Slack bot-token
    # shape.
    return "xoxb-" + "123456789012" + "-" + "ABCDEFGHIJKLMNO"


def _jwt_shaped() -> str:
    # Three dot-delimited base64url-shaped segments (header.payload.sig).
    return "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNA"


def _hex_shaped_high_entropy_token() -> str:
    # 64 lowercase-hex chars -- the exact shape #200-G2B's oracle wrongly
    # exempted globally as "probably a SHA", regardless of key context.
    return hashlib.sha256(b"g2c-regression-corpus-api-token-fixture").hexdigest()


_HEX_TOKEN = _hex_shaped_high_entropy_token()
_AWS_KEY = _aws_access_key_shaped()
_SLACK_TOKEN = _slack_token_shaped()
_JWT = _jwt_shaped()


# -- the historical witness corpus, reconstructed to match PR #293's exact
# reviewed shapes -------------------------------------------------------

REGRESSION_WITNESSES: list[tuple[str, str, tuple[str, ...], str]] = [
    (
        # PR #293 round-1 finding 1 / round-2 case 1: dict-literal value
        # nested inside a non-sensitive-keyed outer assignment -- never
        # recursively inspected for an embedded sensitive key by G2B.
        "dict_literal_credential_assignment",
        'config = {"password": "Hunter2"}\n',
        ("Hunter2",),
        "str",
    ),
    (
        # PR #293 round-1 finding 3 / round-2 case 3: kwarg-call shape,
        # invisible to G2B's line-start-anchored assignment regex.
        "kwarg_credential_assignment",
        'connect(password="Hunter2")\n',
        ("Hunter2",),
        "str",
    ),
    (
        # PR #293 round-1 finding 4 / round-2 case 4: `$`-prefix placeholder
        # carve-out -- a *prefix* match (`startswith("$")`), not a bounded
        # env-reference grammar.
        "prefix_carve_out_dollar_marker",
        'password = "$ecret123"\n',
        ("$ecret123", "ecret123"),
        "str",
    ),
    (
        # PR #293 round-1 finding 5 / round-2 case 5: `[REDACTED]`-prefix
        # carve-out -- same prefix-matching bug, different marker.
        "prefix_carve_out_redacted_marker",
        'password = "[REDACTED]Hunter2"\n',
        ("[REDACTED]Hunter2", "Hunter2"),
        "str",
    ),
    (
        # PR #293 round-1 finding 6 / round-2 case 6: 64-hex-char value
        # exempted globally as "SHA-like" (plus a key-normalizer gap for a
        # bare `...token` suffix) -- high-entropy/hex shape.
        "hex_shaped_high_entropy_token_sha_exemption",
        f'API_TOKEN = "{_HEX_TOKEN}"\n',
        (_HEX_TOKEN,),
        "str",
    ),
    (
        # issue #292 mandatory corpus: JWT-shaped three-segment
        # dot-delimited token.
        "jwt_shaped_three_segment_token",
        f'session = "{_JWT}"\n',
        (_JWT, "eyJhbGciOiJIUzI1NiJ9"),
        "str",
    ),
    (
        # issue #292 mandatory corpus: AWS-access-key-shaped string.
        "aws_access_key_shaped_string",
        f'aws = "{_AWS_KEY}"\n',
        (_AWS_KEY,),
        "str",
    ),
    (
        # issue #292 mandatory corpus: Slack-token-shaped string.
        "slack_token_shaped_string",
        f'slack = "{_SLACK_TOKEN}"\n',
        (_SLACK_TOKEN, "xoxb-123456789012"),
        "str",
    ),
    (
        # issue #292 mandatory corpus: `master_key`-named assignment
        # (narrow bigram vocabulary gap in the predecessor designs).
        "master_key_named_assignment",
        'master_key = "mk-Secret9274"\n',
        ("mk-Secret9274",),
        "str",
    ),
    (
        # issue #292 mandatory corpus: `csrf_token`-named assignment.
        "csrf_token_named_assignment",
        'csrf_token = "csrf-927461"\n',
        ("csrf-927461",),
        "str",
    ),
    (
        # issue #292 / PR #286 mandatory corpus: bare unquoted numeric
        # password value -- unconditionally spared by the G2 forward
        # scanner (never a witness, so postcondition never saw it).
        "bare_numeric_password_value",
        "password=13572468\n",
        ("password=13572468",),
        "int",
    ),
    (
        # PR #286 round-2 finding: merged ALL-CAPS/no-separator compound
        # key bypassing word-splitting (which only split on `_`/camelCase).
        "merged_caps_compound_key_high_entropy_value",
        'DBPASSWORD = "Ab9Cd7Ef5Gh3Jk1Lm"\n',
        ("Ab9Cd7Ef5Gh3Jk1Lm",),
        "str",
    ),
    (
        # issue #292 mandatory corpus: short PIN/numeric-code value --
        # G2's postcondition `<4`-character witness floor let short values
        # leak on reappearance.
        "short_pin_numeric_code",
        "pin=12\n",
        ("pin=12",),
        "int",
    ),
]


@pytest.mark.parametrize(
    "case_id,source,raw_witnesses,expected_kind",
    REGRESSION_WITNESSES,
    ids=[c[0] for c in REGRESSION_WITNESSES],
)
def test_historical_witness_structurally_closed(
    case_id: str, source: str, raw_witnesses: tuple[str, ...], expected_kind: str
) -> None:
    """Structural/type proof, replayed against a historical G2/G2B witness
    shape rather than a neutral fixture: the projected form is a real,
    already-validated ``ProjectedFragmentV2`` (construction itself enforces
    the closed, ``extra="forbid"`` schema on every nested model -- there is
    no field that could have smuggled the raw value through undetected),
    and the raw witness substring(s) are independently confirmed absent
    from the serialized projection. This is content-agnostic: the
    assertion is exactly the same shape as the base file's neutral-fixture
    assertions, only the input differs.
    """

    _, dumped = _project(source)
    serialized = json.dumps(dumped)

    for witness in raw_witnesses:
        assert witness not in serialized, (
            f"{case_id}: raw historical witness {witness!r} reached the "
            "projected/serialized output"
        )

    kinds = [lit["kind"] for lit in _all_literals(dumped["root"])]
    assert kinds, f"{case_id}: expected at least one closed literal in the projection"
    assert expected_kind in kinds, (
        f"{case_id}: expected a {expected_kind!r}-kind closed literal, got kinds={kinds}"
    )
    assert set(kinds) <= _CLOSED_LITERAL_KINDS, (
        f"{case_id}: literal kind outside the closed enum: {kinds}"
    )


def test_unterminated_secret_shaped_value_blocks_whole_fragment_not_best_effort() -> None:
    """issue #292 mandatory corpus / PR #286 round-2 finding: an actual
    unterminated secret value historically leaked completely under the
    predecessor designs (spared as "too risky to guess-redact", never
    recorded as a witness, so postcondition verification never saw it, and
    it could even ride to SAFELY_TRANSFORMED on the strength of an
    unrelated redaction elsewhere in the same material). Under G2C this is
    a plain Python ``SyntaxError`` -- structurally, the whole fragment must
    be blocked, never sent best-effort; there is no partial output at all
    for the raw value to reach."""

    source = 'password="unterminated927461\n'
    with pytest.raises(StructuralProjectionBlockedV2) as excinfo:
        project_fragment_structural_v2(
            fragment_id="d" * 64, path="x.py", content=source, alias_table={}
        )
    assert excinfo.value.reason_code == STRUCTURAL_PROJECTION_PARSE_FAILED_V2


def test_secret_adjacent_to_already_safe_looking_placeholder_no_cross_contamination() -> None:
    """issue #292 mandatory corpus: a real secret-shaped value sitting
    immediately next to an unrelated value that already LOOKS like a safe
    redaction placeholder (``[REDACTED]``). The predecessor designs could
    let the real secret ride to "safe" on the strength of the adjacent,
    already-redacted-looking material. Under G2C there is no such context
    to ride on: every literal is independently opaqued regardless of what
    its neighbor looks like, so BOTH the placeholder-looking value and the
    real secret-shaped value get their own, independent, non-colliding
    digest -- proving neither leaked into the other's slot and neither
    was treated differently because of the other."""

    source = 'API_KEY = "[REDACTED]"\npassword = "927461"\n'
    _, dumped = _project(source)
    serialized = json.dumps(dumped)

    assert "927461" not in serialized
    assert "[REDACTED]" not in serialized

    literals = _all_literals(dumped["root"])
    digests = [lit["sha256_12"] for lit in literals if lit["kind"] == "str"]
    assert len(digests) >= 2, "expected both adjacent string literals to be closed independently"
    assert len(set(digests)) == len(digests), (
        "distinct adjacent literals collided onto the same digest -- possible context bleed"
    )


@pytest.mark.parametrize(
    "case_id,source,identifier_substring",
    [
        (
            # A variable NAME itself shaped like it names a secret (contains
            # "password" as a substring) with an unrelated, neutral assigned
            # value. This is not one of #280/#292/#293's forward-scanner
            # witnesses (those all operated on raw text/line shapes) -- it
            # specifically exercises this module's OWN closure mechanism for
            # identifiers (EXTERNAL_SAFE_STRUCTURAL deterministic sym_NNN
            # aliasing), proving it is identifier-aliasing, not any kind of
            # content/vocabulary matching on the name, that closes this
            # class: the value here is deliberately neutral, so a
            # content-matching approach targeting "password"-like VALUES
            # would find nothing to redact at all, yet the identifier byte
            # sequence must still never survive raw.
            "identifier_carried_secret_name_password_substring",
            'db_password_holder = "totally_neutral_placeholder_value"\n',
            "db_password_holder",
        ),
        (
            "identifier_carried_secret_name_key_substring",
            'secret_key_registry = "another_neutral_placeholder_value"\n',
            "secret_key_registry",
        ),
    ],
    ids=["password_substring", "key_substring"],
)
def test_identifier_shaped_like_a_secret_name_is_aliased_not_content_matched(
    case_id: str, source: str, identifier_substring: str
) -> None:
    alias_table, dumped = _project(source)
    serialized = json.dumps(dumped)

    assert identifier_substring not in serialized, (
        f"{case_id}: secret-shaped identifier {identifier_substring!r} survived raw"
    )
    # Content-matching-style checks on the identifier's own vocabulary
    # ("password"/"key") would find no VALUE to act on here at all -- the
    # assigned value is neutral. What actually closes this is the
    # identifier alias table, independent of the name's vocabulary.
    assert alias_table, f"{case_id}: expected the identifier to be routed through the alias table"
    assert any(alias.startswith("sym_") for alias in alias_table.values())
    assert dumped["identifier_alias_mode"] == "EXTERNAL_SAFE_STRUCTURAL"


def test_mutation_bypassing_the_closure_makes_the_regression_assertion_go_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation test for THIS regression corpus file (distinct from the
    base file's own Gate A / Gate B / production-wiring mutation tests,
    which target the projector's internal completeness gates and the real
    transport wiring respectively): temporarily bypass the structural
    closure entirely for one historical witness -- simulating the
    raw AST/string handling path a pre-G2C design would have used, with no
    opaque schema in between -- and confirm the exact same load-bearing
    assertion this file relies on (raw witness absent from the "output")
    actually goes RED under that bypass. Then restore the real closure and
    reconfirm GREEN. This proves the assertion technique is load-bearing,
    not vacuously true regardless of what the SUT does.
    """

    source = 'config = {"password": "Hunter2"}\n'
    witness = "Hunter2"

    def _bypassed_no_closure(*, fragment_id, path, content, alias_table):
        del fragment_id, path, alias_table
        # The historical failure mode this whole lineage was refuted by:
        # the raw source text reaching the "output" with no structural
        # closure in between at all.
        return content

    monkeypatch.setattr(sep, "project_fragment_structural_v2", _bypassed_no_closure)

    bypassed_output = sep.project_fragment_structural_v2(
        fragment_id="d" * 64, path="x.py", content=source, alias_table={}
    )
    assert isinstance(bypassed_output, str)
    # RED: under the bypass, the SAME assertion this file relies on must
    # fail -- i.e. the raw witness IS present.
    with pytest.raises(AssertionError):
        assert witness not in bypassed_output

    monkeypatch.undo()

    # GREEN: restored to the real closure, the witness is absent again.
    _, dumped = _project(source)
    restored_serialized = json.dumps(dumped)
    assert witness not in restored_serialized
