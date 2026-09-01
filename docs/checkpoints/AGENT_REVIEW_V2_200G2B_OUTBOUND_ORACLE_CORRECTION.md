# AgentReview v2 -- #200-G2B Checkpoint: Outbound Oracle Round-2 Correction

**Scope:** issue [#287](https://github.com/mglpsw/aiops-orchestrator/issues/287)
(`#200-G2B`, spike level), PR
[#293](https://github.com/mglpsw/aiops-orchestrator/pull/293)
(`feat/200-g2b-outbound-oracle-spike`, Draft). This is a **correction
round** on an existing, independently-reviewed spike -- not a rewrite, and
not authorization to start production G2B implementation.

**Branch:** `feat/200-g2b-outbound-oracle-spike`. Frozen head entering this
round: `4ad18ecb96ec2bd0c22a6fe7b48f2da2d1cd2365` (verified as `origin`'s
head before any change; no drift).

**Files touched:** `evals/agent_review_v2/outbound_safety_oracle_spike.py`
(the oracle) and `tests/agent_review/test_g2b_outbound_oracle_spike.py`
(regression tests). No `app/**` change, no CI workflow change, no
release/deploy/merge action. PR stays Draft.

## 1. What round 1 got right, and what independent review found wrong

Round 1 (`c8c3efc`..`4ad18ec`) correctly fixed the architectural flaw from
`#277`/`#286`: it built a genuinely independent oracle sitting at the exact
pre-HTTP `bytes` object, importing neither the forward redactor nor its
witness set. That structural independence is real and is **not** touched by
this round.

Independent adversarial review (PR #293 comment, reproduced against the
frozen head above) found a *different* defect: `inspect_outbound_body_v2`
promoted "the forward-looking detectors found no match" to `OUTBOUND_SAFE`.
Over an open, unenumerable grammar (arbitrary source code in arbitrary
languages/styles), absence of a match is not proof of safety -- it is
absence of evidence. Six concrete reproduced false-safes, all confirmed
reproducible in this round before any fix was applied (RED regression tests
added first, see §3):

1. `config = {"password": "Hunter2"}` -- dict-literal value nested inside a
   non-sensitive-keyed assignment; never recursively inspected.
2. `"password": "Hunter2"` -- bare quoted-key dict-literal line; the old
   assignment regex required a bare identifier at line start, so a leading
   `"` never matched anything.
3. `connect(password="Hunter2")` -- kwarg-call shape; same line-start
   anchor problem.
4. `password=$ecret123` -- the `$`-prefix placeholder carve-out was a
   `str.startswith` check, proving nothing about what follows the prefix.
5. `password=[REDACTED]Hunter2` -- same prefix-carve-out mechanism.
6. `API_TOKEN=<64-hex secret>` -- two compounding bugs: `apitoken` was not
   in the sensitive-key suffix list, and even if it had been, a global
   "40/64 hex chars is probably a SHA" exemption would have cleared it
   regardless of key context.

## 2. Design rationale, written before treating any single case as "the fix"

The reviewer's own framing, and the coordinating grant, were explicit that
this must not become vocabulary/regex widening on the SAFE side (that is
symmetric to the mistake that sank `#277`/`#286`). The correction is a
**polarity inversion**, implemented as two independent layers:

- **Structural coverage.** The old `_LINE_ASSIGNMENT_RE` was anchored to
  the start of a line and only recognised a bare identifier key. It missed
  kwarg-call and quoted dict-literal shapes not because those shapes were
  judged safe, but because the detector could not see them at all --
  "unrecognised" silently collapsed into "safe". The new `_ASSIGNMENT_RE`
  is unanchored and recognises both a bare/dotted identifier key and a
  quoted dict-literal key, so the pre-existing (and already allowlist-only)
  sensitive-key branch actually gets a chance to run against these shapes.
  A dict/array-literal value under a non-sensitive key is recursed into
  (bounded to depth 6) instead of being judged as one opaque blob, so a
  secret nested inside a non-sensitive-keyed container is still found.
- **Allowlist boundedness.** `_safe_placeholder` (the sole exit from
  `NOT_PROVEN_SAFE` under a proven-sensitive key) changed from prefix
  checks (`value.startswith("$")` / `startswith("[redacted")`) to exact
  set membership or `fullmatch` against a bounded grammar
  (`_ENV_VAR_REF_RE`, `_SAFE_BRACKET_PLACEHOLDER_RE`). The entire value
  must be the placeholder now, not merely start like one.
- **Removing an unconditional shape-based exemption.**
  `_looks_like_sha_or_identifier`'s blanket "any 40/64-hex value is
  probably a SHA" clause is gone; the hex exemption now requires the key
  itself to affirmatively name an identifier/digest role
  (`_HEX_IDENTIFIER_KEY_SUFFIXES`: `sha`, `hash`, `digest`, `commit`,
  `revision`, `checksum`, `etag`, `fingerprint`). A hex-encoded token is
  byte-for-byte indistinguishable from a hex-encoded digest; only the key
  can tell them apart, and only affirmatively, never by default.

The net effect for the sensitive-key branch (`password`, `token`,
`api_key`, ...): the default was **already** "unsafe unless positively
allowlisted" before this round; what was broken was (a) not reaching that
branch at all for several real shapes, and (b) the allowlist itself leaking
via prefix matching and an unconditional hex exemption. Fixing structural
coverage and allowlist boundedness closes the loophole faithfully without
adding new SAFE-side vocabulary. For content with **no** assignment-shaped
construct anywhere (prose, comments-only diffs, pure structural tokens),
`OUTBOUND_SAFE` by absence of any construct remains legitimate -- that is
the "provably outside the risk domain" case sanctioned explicitly by the
correction brief, not the closed-world fallacy being corrected.

## 3. RED before fix, GREEN after (`tests/agent_review/test_g2b_outbound_oracle_spike.py`)

`_round2_closed_world_false_safe_cases()` encodes the six reviewer-reported
shapes (case 6 assembled at runtime via `hashlib.sha256(...).hexdigest()`
so no token-shaped literal is committed to the repo).
`test_round2_closed_world_false_safes_are_blocked` drives them through the
**same real pipeline** as the existing suite (real diff acquisition, real
chunk/payload building, forward `redact_text`/`sanitize_artifact_value`
blinded via monkeypatch, real `execute_chunk_review_v2`, mocked only at
`_open_agent_router_request_v2`), asserting the exact needle is present in
the captured pre-HTTP bytes and that `OutboundSafetyBlockedV2` is raised
with `verdict == "OUTBOUND_NOT_PROVEN_SAFE"`.

Confirmed RED against the frozen head (`4ad18ec`) before any source change:
all 6 cases failed with `AssertionError: real HTTP delegate must never run
for unsafe outbound material` -- i.e. the oracle let them through as SAFE
and the delegate ran, exactly matching the reviewer's report.

After the fix: all 6 GREEN, and all 16 pre-existing cases in
`_mandatory_secret_cases()` (numeric password, merged compound key,
master/csrf tokens, JWT, short PIN, unterminated quote, adjacent successful
redaction, AWS/Slack-shaped tokens, high-entropy opaque candidate) plus the
existing negative controls (`test_normal_real_router_request_is_not_false_blocked`,
the four-file `test_independent_oracle_does_not_false_block_representative_real_source`
parametrisation) remain GREEN. Full suite: **22/22 passed.**

## 4. Self-found issues while implementing (fixed before declaring GREEN, not hidden)

Making the assignment regex unanchored is necessary to see the kwarg/
dict-literal shapes above, but it also creates new ways for the regex to
match things that are not assignments. Three were found and fixed during
this round's own real-source calibration (task step 6), each reproduced
before being fixed, not assumed:

1. **Bracket-literal short-match truncation.** The array/object-literal
   value alternative (`\[[^\[\]\n]*\]`) matched only the short balanced
   span, so `[REDACTED]Hunter2` matched `[REDACTED]` and silently orphaned
   `Hunter2` as unconsumed, unscanned text -- which would have
   reintroduced case 5 as a *new* false-safe inside the fix itself. Caught
   by the regression test for that exact case going red immediately after
   the first implementation pass. Fixed by requiring the container span to
   swallow any immediately-adjacent non-delimiter text
   (`\{...\}[^\s,)\]}\n]*`), so a bracket-prefixed placeholder-with-trailing-secret
   is captured whole and correctly fails the strict `_is_container_literal`
   check instead of being silently split.
2. **Unbounded quoted-value length.** The Router body wraps reviewed
   source as a JSON string. A quoted dict-style key like `"content"`
   sitting in front of that whole string, with no length cap on the
   quoted-value alternative, would capture the *entire file* as one
   candidate value and run the entropy/opaque check across all of it --
   exactly the "generic entropy over an arbitrary JSON string"
   anti-pattern `_opaque_value_is_suspicious`'s own docstring already
   rejects. This had been prevented only incidentally by the old
   line-anchored regex never matching a quoted key at all; reintroducing
   quoted-key support reopened it. Found via the full real-source negative
   scan (`app/`, `scripts/`, `tests/`, `evals/`, 340 files), fixed with a
   512-character bound (`_MAX_VALUE_LEN`) on every value alternative --
   generous for realistic credential shapes including JWTs, far short of
   "a whole file".
3. **Control-flow colon misparsed as assignment.** `if not api_key:`
   followed by a real line break: JSON-escaping turns that line break into
   the two literal characters `\`+`n`, and nothing distinguishes "nothing
   meaningful follows this colon" from "a value follows" once the key is
   no longer required to be the first token on its line. `api_key` read as
   a sensitive key, `\n` (the escape pair, not a real value) read as its
   "value", and the escape pair failed the strict placeholder allowlist --
   a real false-block on ordinary control flow, found in
   `app/agent_review/review_transport_v2.py`,
   `scripts/aiops-runtime-inventory.py`, `scripts/aiops-runtime-postcheck.py`.
   Fixed with a negative lookahead rejecting bare-token values that start
   with `\n`/`\r`/`\t` as a literal two-character escape pair.
4. **Over-broad key suffix (self-reverted within this round).** The first
   attempt at fixing bug 6 above widened the sensitive-key suffix from
   specific compounds (`csrftoken`, `idtoken`, ...) to the bare, generic
   `token`. Real-source calibration showed this repository's own
   diff-parsing code uses `old_path_token`/`new_path_token`/`path_token`/
   `command_token` as ordinary lexer-token identifiers, unrelated to
   credentials -- a bare suffix flagged them exactly as eagerly as
   `API_TOKEN`. Reverted to an enumerated suffix list with `apitoken` added
   as the one compound the review actually needed; `old_path_token`-shaped
   identifiers stopped being flagged, `API_TOKEN` stayed flagged.

## 5. Mutation testing on the polarity-inversion logic (task step 5)

Recorded as instructed: commit before mutating, confirm the mutation makes
the round-2 regression tests fail, restore, confirm green again.

Committed the fix at `<commit-sha-A>` (see §7 for the actual hash), then:

- **Mutation 1 -- revert `_safe_placeholder` to prefix matching.**
  Restored the round-1 `value.lower().startswith(("${", "$", "<",
  "[redacted", "[placeholder"))` shape in place of the exact/`fullmatch`
  checks. Result: `test_round2_closed_world_false_safes_are_blocked[password=$ecret123-...]`
  and the `[REDACTED]Hunter2` case both failed (false-safe reproduced),
  confirming the allowlist-boundedness fix is load-bearing for those two
  cases. Restored the real implementation; suite back to 22/22.
- **Mutation 2 -- revert `_ASSIGNMENT_RE` to line-start-anchored, bare-key-only.**
  Restored the round-1 `_LINE_ASSIGNMENT_RE` shape (dropping quoted-key
  support and the non-anchored scan). Result: the `connect(password=...)`,
  `"password": "Hunter2"`, and nested `config = {...}` cases all failed
  (false-safe reproduced), confirming the structural-coverage fix is
  load-bearing for those three cases. Restored the real implementation;
  suite back to 22/22.

`<mutation results and exact commands recorded in the PR/commit trail;
see §7 for hashes>`

## 6. Real-source negative-direction check (task step 6)

Scanned every `*.py` under `app/`, `scripts/`, `tests/`, `evals/` (340
files) by wrapping each file's raw source as reviewed-content inside a
realistic Router body (same JSON shape as
`test_independent_oracle_does_not_false_block_representative_real_source`,
generalised from 4 files to the whole tree) and calling
`inspect_outbound_body_v2` directly (no live Router, no network).

- **Baseline (pre-fix, same 340-file scan): 47/341 files blocked (~13.8%).**
- **Post-fix: 47/340 files blocked (~13.8%) -- same order of magnitude,
  same rate.**
- Diffing the blocked-file sets file-by-file (not just the count) showed
  the post-fix set is almost entirely the *same class* of content as
  baseline plus a few additions, after two rounds of calibration (§4,
  items 2-4) removed the genuine regressions that first appeared:
  - 11 of the ~12 net-new blocks are this repository's own forward-redactor
    *test fixtures* -- files that deliberately contain secret-shaped
    literals (`password = "super-secret"`, `api_key = "sk-test-key"`,
    `token = "sk-test-token"`, `token=SUPERSECRET`, ...) to exercise
    `redact_text`/`sanitize_artifact_value` elsewhere in the suite. These
    are not "ordinary code with no credentials" -- they are literal
    credential-shaped strings by the fixture author's own construction,
    and the pre-fix oracle false-safed on its own repository's redaction
    test suite. Flagging them is the oracle doing exactly what it is for.
  - 1 remaining case (`scripts/verify-caem-f0-pin.py`, `print(f"pin: ok
    ...")`) is a genuine, narrow word-collision: this repository's CAEM
    "pin" identity object collides with the security-sensitive word "pin"
    (PIN code), which must stay in the sensitive-key set --
    `pin=12` is an existing, required-passing regression case. Not fixed
    in this round; recorded here as an accepted, documented limitation
    rather than patched with another shape-based carve-out, because every
    carve-out attempted for it (CapitalCase/type-name exemption gated by
    entropy, dotted/paren "code reference" exemption) was independently
    shown to reopen a real evasion (see §4a below) before being discarded.
  - `_MAX_VALUE_LEN`, `_HEX_IDENTIFIER_KEY_SUFFIXES`, and the enumerated
    (not bare) `token` suffix list are the load-bearing calibration
    artifacts from this pass; each is documented in-line at its
    definition with the concrete real-source case that required it.

### 6a. A rejected idea, recorded so it is not tried again

A "value is an unquoted code-reference expression" exemption (skip the
opaque/placeholder check when the value contains `.` or `(`, e.g.
`self.api_key`, `os.getenv(...)`) was drafted to resolve several of the
false-positive files in `app/adapters/claude.py`, `app/core/config.py`,
`app/api/auth.py`. Before implementing it, a concrete evasion was
constructed by hand: `password=x.Ab9Cd7Ef5Gh3Jk1Lm` -- prepending a fake
dotted prefix to an otherwise-correctly-blocked real secret
(`Ab9Cd7Ef5Gh3Jk1Lm`, from the existing `DBPASSWORD` regression case) would
have satisfied the exemption and evaded detection. Discarded without being
committed. The corresponding over-blocks on those three files were left
as-is; they are pre-existing (present in the `47/341` baseline already, not
introduced by this round) and out of this round's scope.

## 7. Identities and state at close of this round

```yaml
branch: feat/200-g2b-outbound-oracle-spike
pr: 293
frozen_head_entering_round: 4ad18ecb96ec2bd0c22a6fe7b48f2da2d1cd2365
new_head_at_close: <filled in after push, see PR #293 commit list>
ci: <filled in after gh pr checks 293>
mutation_test: recorded_above_section_5
negative_direction_scan: 47/340 blocked, same order as 47/341 baseline
review_round_2: <filled in after 3 independent lanes return>
production_g2b_started: false
pr_state: Draft
```

## 8. Not authorized / not done in this round

No Ready marking, no merge, no tag/release, no deploy, no live Router or
real LLM provider call, no CI workflow file change, no AgentEscala/
InterLeitos/CAEM mutation, no `#200` closure, no `#273` modification, no
production G2B implementation start. PR #293 remains Draft.
