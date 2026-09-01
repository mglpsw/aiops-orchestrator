# AgentReview v2 — #200-G2 Safe Review Material Checkpoint

**Corte temporal:** 2026-09-01. **Classe:** estado observado, produzido ao
final desta slice; revalidar HEAD/PR/checks vivos antes de qualquer ação
subsequente (merge, G5, etc.).

## Identidade

```yaml
repository: mglpsw/aiops-orchestrator
base: f70af2e635643d1ee96ba431857002ae079b502b   # live master, verified at start, no drift
branch: feat/200-g2-safe-review-material
head: <see PR — frozen after this checkpoint's commit>
worktree: /opt/agent-tools/aiops-orchestrator-toolrepo/.claude/worktrees/agent-a59a345ec5f66dbc6
issue: https://github.com/mglpsw/aiops-orchestrator/issues/280  # #200-G2
predecessor: PR #277, branch feat/200-f-derivable-operational-boundary
  classification: FROZEN_FORENSIC_200F, STOP_200F_ARCHITECTURE_NOT_CONVERGING
  qualification_transfer: false
```

## Mission recap

Authority E (safe review material / DLP) was one of two abstractions
independently refuted twice in #277 (the other, Authority B, is out of
scope here — tracked as #200-G1). #277's round-2 "suspect unless benign"
quoted-secret redesign of `redaction.py` was declared `DO_NOT_PORT`: it
leaked ~44% of random secrets, spared JWTs entirely, reproduced a
"claims `[REDACTED]` while leaking" defect on a multiline triple-quoted
secret, and was quadratic on non-matching input (16,000-char line, ~90s).

Master's `app/agent_review/redaction.py` was, and remains, a **different,
currently-shipping module**, never touched by that failed lineage. This
slice extends it in place — it does not port the failed redesign, and does
not reinvent it as a single expanding regex.

## Design

### What changed in `app/agent_review/redaction.py`

- **Quoted secret values now redact.** The pre-existing gap (the original
  value class excluded quote characters entirely, so `password = "..."`
  never matched) is closed via a hand-written linear scanner
  (`_scan_and_redact_key_values`), not a bigger regex.
- **Extended key coverage**: `secret_key`, `apikey`, `signing_key`,
  `access_key`, `encryption_key`, `session_token`, `auth_token`,
  `private_key` added alongside the original eight.
- **Standalone credential-shape detectors**, independent of key context:
  JWT structure (`_JWT_CANDIDATE_RE` + `_looks_like_jwt` — a cheap
  length-bounded shape filter, then a real structural check: does the
  header segment base64url-decode to a JSON object), plus AWS, Slack,
  GitLab, npm, Google, and Stripe token-prefix patterns.
- **Postcondition-safe transformation primitives**: the scanner tracks
  every redacted witness (`RedactionState.redacted_witnesses`) and an
  `unbounded_construct_present` flag for constructs it could not safely
  bound (see below).
- **No casing heuristics.** The #277 round-2 defect's actual mechanism —
  a `(?i)`-flagged "this looks like a CapitalCase type name" carve-out
  that, under the case-insensitive flag, spared base64-ish secrets exactly
  as readily as real type names — is not reproduced. Sparing an unquoted
  value is done on STRUCTURAL grounds only: numeric/keyword/template
  literals, a dotted reference whose first segment starts lowercase
  (`settings.claude_api_key`), a call/subscript/collection-literal
  opening (`tokens[index]`, `get_secret()`), a `name: Type = default`
  Python annotation (colon-form + unquoted + a bare `=` shortly after,
  never for `=`-form itself), a small enumerated closed-list of
  builtin/typing keywords (`str`, `int`, `Optional`, …), or a value that
  is EXACTLY the key's own name (`token=token`). None of these apply to a
  QUOTED value — a quoted string is data, never spared structurally, only
  via the placeholder list.
- **Two pre-existing O(n²) regexes fixed** (`_CREDENTIAL_URL_RE`,
  `_DATABASE_URL_RE`): an unbounded URL-scheme character class
  (`[a-z][a-z0-9+.-]*`, `.`/`-` both in-class) immediately followed by a
  required literal `://` is the same backtracking shape as #277's
  witness. Found empirically while proving the module's linear-envelope
  claim (`'abcdefghij.' * 8000` ≈ 5s in `_sub_credential_urls` alone
  before the bound). Bounded to `{0,20}` — real schemes are a handful of
  characters.

### New module: `app/agent_review/safe_review_material.py`

`derive_safe_review_material(text, *, dlp_config=None) -> SafeMaterialResult`
composes on top of `redaction.py`'s primitives and adds the disposition
contract:

```
SAFE_UNCHANGED | SAFELY_TRANSFORMED | BLOCKED_UNSAFE_TO_TRANSFORM
```

- `SAFE_UNCHANGED`: nothing flagged, output byte-identical to input
  (asserted, not assumed — an unexplained divergence with zero flagged
  secrets is itself routed to `BLOCKED_UNSAFE_TO_TRANSFORM`).
- `SAFELY_TRANSFORMED`: something was redacted AND every witness value is
  independently verified absent from the output
  (`_verify_postcondition`). This is the single highest-priority
  invariant per the mission — the one defect class that recurred on every
  #277 round.
- `BLOCKED_UNSAFE_TO_TRANSFORM`: the underlying scanner could not bound a
  construct (an unterminated triple-quoted value — its true extent is
  unknowable from this text alone, e.g. past a chunk boundary), the
  material exceeds a length circuit breaker (defense in depth only; the
  scanner is linear, so this should never fire on legitimate input, and
  when it does it blocks rather than truncating-and-passing-through), or
  a target-owned DLP override explicitly forbids a substring. `output` is
  `None` in this state — nothing is emitted for downstream consumption.

`DLPOverrideConfig` is the explicit target-owned extension point named in
the mission (`additional_blocked_substrings`, `additional_safe_substrings`
— the latter only excuses a witness when it is exactly the whole matched
value, never widening what counts as benign inside a still-suspect larger
value). Wiring a concrete loader from a target's profile/policy file is
target-pack surface (`docs/engineering/PROJECT_OVERLAY.md`'s
engine/target-pack boundary) and out of scope for this primitive slice.

### Deliberate scope boundaries (named, not silent)

- **No Python-tokenizer integration.** Considered (per the mission's
  suggestion) for the colon-form annotation ambiguity, but the linear
  hand-written scanner plus the narrow structural carve-outs above closed
  every real-source false positive the oracle found without it. Recorded
  as a live option for a future slice if a new false-positive class
  surfaces that these carve-outs don't cover.
- **No standalone/blanket high-entropy scanner.** Entropy is not used as
  an independent whole-file detector — real source is full of legitimate
  high-entropy strings (hashes, UUIDs, git SHAs, base64 fixtures) and a
  blanket scanner was judged, by the same reasoning as the #277 postmortem,
  to fail the real-source oracle badly. High-confidence STRUCTURAL
  detectors (JWT, vendor prefixes, PEM blocks) are used instead.
- **`api_key=router_api_key`-shaped bare references remain over-redacted.**
  The same-name carve-out is exact-match only (see Round 2 below for why);
  a bare value that merely CONTAINS the key's name, but is not the key's
  exact name, stays suspect. This is the pre-#200-G2 baseline behavior for
  bare non-dotted identifiers (already true of the original 8-key
  `_ASSIGNMENT_RE`), not a new regression.
- **A bare (unquoted) annotation on a sensitive parameter name with a
  project-defined type and no default** (`secret_key: SecretKeyConfig`
  alone, no ` = ...` after it) is still redacted. Only the WITH-default
  form (`name: Type = default`) is spared, via the trailing-`=`
  lookahead. Casing alone was rejected as a distinguishing signal because
  the random-secret corpus proved it reopens the colon-form leak class
  (see Round 2 below).

## RED corpus (mission item 1)

`tests/agent_review/test_redaction_200f_red_corpus.py` — 27 cases, all
GREEN at final HEAD:

- Round 1 (`ed6692d`): 7 quoted-secret shapes (double/single-quoted,
  colon separator, JSON-quoted key, enumerated-key variety, upper-case
  key, attribute target).
- Round 2 (`e910dfb`): 6 key-pattern-gap shapes (`secret_key`, `apikey`,
  `signing_key`, `access_key`, `encryption_key`, `session_token`) plus a
  "no false claim of redaction" invariant test across both rounds' cases.
- PR #277 close-comment witnesses (authoritative, independently
  reproduced twice): `export API_KEY=...` bare leak, bare `password=...`
  leak, a 200-case reproducible random-secret battery (base64-ish,
  hex-ish, mixed-alnum, forced mixed-case) asserting **0/200 leak** (not
  "improved from 44%" — zero), 6 JWT placement shapes (bearer header,
  enumerated key bare/quoted, non-enumerated key, `jwt_`-named key, bare
  in prose with no key context at all), the multiline triple-quote
  false-success reproduction, an unterminated-triple-quote leak/flag
  check, and the 16,000-char ReDoS witness (plus a second,
  independently-discovered quadratic shape — the URL-scheme regex above —
  found while proving the module's linear envelope).

## Bidirectional corpus (mission item 2)

`tests/agent_review/test_safe_review_material.py`:

- **Positive corpus** (16 cases, must never leave in plaintext): quoted
  password, shell `export`, `.env` line, bearer header, `secret_key`/
  `apikey`/`signing_key`/colon-form `token`, f-string/b-string/raw-string
  embedded secrets, an escaped-quote secret, GitHub token, AWS access key,
  a database-URL password, and — added after a real leak was found by the
  differential oracle — a secret whose OWN descriptive name contains the
  key word as a whole segment (`AGENTESCALA_FIXTURE_TOKEN_SECRET`, see
  Round 2).
- **Negative corpus** (17 cases, must never be damaged): every item named
  in the issue text (`prompt_tokens`, `max_tokens` numeric/annotation,
  `input_tokens`, `dedupe_key`, `namespace_key` annotation/subscript,
  `==`/`!=`/`<=` comparisons, CapitalCase type annotation, dotted
  reference, subscript reference, Pydantic field reference) plus
  `token=token`/`self.token = token` (added after Round 1's script-file
  damage) and a call/reference-mixed annotation.
- Postcondition tests (see next section) and DLP override tests
  (force-block on a declared substring; a declared-safe substring only
  excuses the exact whole witness, never partial).

`tests/agent_review/test_redaction_source_parseability_regressions.py` —
9 additional cases, each reproducing an ACTUAL defect the real-source
oracle found (not hypothetical): bearer/cookie/bare-value scans consuming
an enclosing quote, a bare-value scan consuming a function signature's
closing paren or an f-string's interpolation brace, a trailing `\n`
escape being dropped, an empty-value-adjacent-to-enclosing-quote
misdetection (`assert b"token=" not in ...`), and a key-match-inside-a-
string search not hunting for a distant unrelated triple-quote close.
Every case asserts `ast.parse` succeeds on the transformed output.

## Postcondition discipline (mission item 4)

- `test_safely_transformed_postcondition_holds` — parametrized across the
  full positive corpus: whenever the disposition claims
  `SAFELY_TRANSFORMED`, the triggering witness must be verifiably absent.
- `test_postcondition_guard_actually_fires_on_a_forced_violation` — calls
  `_verify_postcondition` directly with a witness still present and
  asserts it reports failure (proves the guard is live, not vacuously
  true on real input).
- `test_disposition_flag_and_output_never_disagree` — the
  `redaction_applied` property must never be `True` while a witness
  remains in the output, checked across the whole positive-corpus batch
  in one pass.
- Mutation-confirmed (see below): a forced always-true postcondition
  mutation flips this test class RED.

## Performance (mission item 5)

- `test_redos_witness_stays_linear`: the exact 16,000-char adversarial
  line completes in well under 2s (measured: single-digit milliseconds),
  AND a doubling-size series (4k/8k/16k/32k) shows no growth ratio ≥ 3x
  between consecutive sizes (quadratic growth would show ~4x per
  doubling) — the actual complexity claim, not a single timing point.
- `test_redos_witness_url_scheme_backtrack`: the independently-discovered
  `_CREDENTIAL_URL_RE` quadratic shape, same bound.
- Empirical scaling measured during development (not asserted in CI, but
  recorded here as evidence): `'abcdefghij.' * n` through the full
  `redact_text` pipeline —

  | n | chars | duration before fix | duration after fix |
  |---:|---:|---:|---:|
  | 2,000 | 22,000 | 0.33s | 0.015s |
  | 4,000 | 44,000 | 1.31s | 0.034s |
  | 8,000 | 88,000 | 5.31s | 0.061s |
  | 16,000 | 176,000 | 20.9s | 0.118s |
  | 32,000 | 352,000 | — | 0.234s |

  Before-fix column is `_sub_credential_urls` alone on the unbounded
  scheme regex; after-fix is the full pipeline. Growth is linear
  post-fix (~2x per doubling), quadratic pre-fix (~4x per doubling).
- The length circuit breaker (`_MAX_MATERIAL_LENGTH = 5_000_000`) is
  defense in depth, not the complexity fix — it routes to
  `BLOCKED_UNSAFE_TO_TRANSFORM`, never a silent truncate-and-pass-through
  (`test_length_circuit_breaker_blocks_rather_than_silently_truncates`).

## Real-source differential oracle (mission item 3)

`scripts/agent-review-safe-material-differential-oracle.py` — scans
`app/`, `scripts/`, AND `tests/` (the mission explicitly flagged #277's
own regression test as mis-scoped for missing `scripts/`/`tests/`;
corrected here). Read-only, not wired into CI.

**Results at round-1 frozen HEAD (`ed25358`, pre-correction):**

```yaml
files_scanned: 301
lines_examined: 122944
changed_lines: 184          # via difflib SequenceMatcher opcodes, not
                             # naive positional line comparison -- see
                             # methodology note below
files_with_changes: 29
parseability_regressions: 0  # ast.parse succeeds on every transformed file
```

**Results after the round-2 correction (`d97d656`)**, re-run repeatedly
during the fix (not once at the end — see "Correction sub-rounds" below
for why that mattered):

```yaml
files_scanned: 301
lines_examined: 123265
changed_lines: 228            # broader key coverage legitimately touches
                               # more lines (compound keys now caught)
files_with_changes: 39
parseability_regressions: 0
```

### Methodology note: diff counting

An early version of this oracle counted changed lines by positional index
comparison (`original_lines[i] != transformed_lines[i]`). When a
multi-line match collapses N source lines into 1 (a private-key block
spanning 3 lines, at that point), every SUBSEQUENT line in the file
appeared "changed" purely from the index offset shift — one real change
inflated to ~2,700 spurious ones in a single file
(`tests/test_github_agent_review.py`). Replaced with a
`difflib.SequenceMatcher` opcode-based diff, which correctly attributes
each real edit to its own block regardless of line-count shift.

### Classification of the 184 changed lines / 29 files

The overwhelming majority (all but one) are **expected**: test fixtures
across `tests/agent_review/` and `tests/` deliberately embed example/fake
secrets (`ghp_...`, `sk-...`, `Bearer <fake>`, `token=SUPERSECRET`,
`password=super-secret`, JWT-shaped tokens, fake database-URL
credentials) specifically to exercise redaction — catching MORE of them
(particularly the previously-leaking quoted forms) is this slice's whole
point. A handful are this module's OWN comments quoting example secret
shapes for documentation (self-referential, same pattern as the
historical "71 altered lines → 8, all in comments" precedent).

**One pre-existing, out-of-scope false positive, left unfixed and named
here rather than silently left as a footnote:**
`scripts/github_agent_review.py:533` — `"Authorization header contains a
bearer token-like value."` (a PROSE STRING, not an actual header) gets
partially redacted because `_BEARER_RE` matches "bearer token-like" and
treats "token-like" as if it were a token value. This regex is
byte-identical to what shipped on master before this slice (not touched
here); the false positive is over-redaction of a benign sentence, not a
leak or a syntax break, and `scripts/github_agent_review.py` does not
import `app.agent_review.redaction` at all (it has its own, separate
redaction implementation) — this only matters if that FILE ITSELF is ever
part of a diff under AgentReview review. Recorded as a known,
pre-existing limitation; not fixed in this slice to avoid scope creep
into a regex this slice did not otherwise touch.

**Real regressions found DURING development and fixed before this
checkpoint** (not present at the frozen HEAD, listed because the process
requires it, not because they're live):

1. Bare-value scans (assignment scanner, `_AUTHORIZATION_BEARER_RE`,
   `_COOKIE_RE`) not excluding `"`/`'`/`)`/`]`/`}`/`{`/`\` — each caused a
   real syntax-breaking or content-dropping defect on real source
   (function signature losing its closing paren, f-string losing its
   interpolation brace, a string literal losing its closing quote, a
   trailing `\n` escape silently dropped). All fixed by narrowing the
   scanner boundaries; each has a permanent regression test in
   `test_redaction_source_parseability_regressions.py`.
2. An unbounded triple-quote-close search (`text.find` with no window)
   could walk past a locally-misdetected "opening" triple-quote (three
   literal quote characters as DATA inside an already-open, differently-
   quoted string) into an unrelated, much-later real triple-quoted string
   and swallow everything between. Fixed with a bounded search window
   (`_MAX_TRIPLE_QUOTE_SEARCH_WINDOW = 20_000`) — also a further
   complexity-boundedness improvement, not just a correctness fix.
3. An unterminated single/double (non-triple) quote used to be redacted
   "to end of line" unconditionally; the oracle found this misfires on
   `assert b"token=" not in canonical` (the `"` right after `=` is the
   ENCLOSING b-string's own closing delimiter, not a new value's
   opening). Changed: an unterminated single/double quote is now spared
   entirely (treated as "no real value here") rather than guess-redacted,
   since real-source evidence showed this misdetection is more common
   than a genuine truncated single-line secret.
4. A first version of the same-name reference carve-out
   (`api_key=router_api_key`-shaped sparing) used substring containment
   rather than exact match, and the oracle found it spared a REAL secret
   whose fixture name happened to contain the key word as a whole segment
   (`AGENTESCALA_FIXTURE_TOKEN_SECRET`) in
   `tests/agent_review/test_aiops_review_intake_cli.py`. Narrowed to
   exact match only; the leaking shape is now a permanent positive-corpus
   case (`secret_name_contains_key_word`).

Each of the four defect classes above is exactly the process working as
intended per the mission: "a hand-written corpus alone is NOT sufficient
evidence... this is exactly how #277 passed internally and failed
externally twice." None of these were caught by the hand-written RED/
positive/negative corpus before the oracle ran against real source.

## Mutation testing (mission item 6)

Committed at `2ddd676` before mutating. Five targeted mutations against
the core detection/transformation propositions, each: mutate → run
relevant test file(s) → confirm RED → `git checkout --` to restore →
confirm GREEN.

| # | Mutation | Proposition | Result |
|---|---|---|---|
| 1 | `_is_benign_literal`: `if quoted: return False` → `return True` | quoted values are never structurally spared | 26 tests RED (round-1/2 quoted shapes, random-secret battery, multiline triple-quote) |
| 2 | Disable the `==` comparison guard (`if False and sep_char == "="...`) | bare `==` must not be mis-parsed as assignment | 2 tests RED (negative-corpus comparison case, parseability regression) |
| 3 | `_verify_postcondition`: force `return True` on a still-present witness | postcondition must fail when witness remains | 1 test RED (direct guard-liveness test) |
| 4 | `_looks_like_jwt`: force `return False` | standalone JWT detector must fire independent of key context | 4/6 JWT-placement tests RED (2 still caught by bearer/quoted-value paths — confirms layered defense) |
| 5 | `derive_safe_review_material`: disable `unbounded_construct_present` routing | unbounded constructs must BLOCK, not best-effort-transform | 1 test RED |

All five restored to byte-identical GREEN (`git status --short` clean
after each restore).

## Regression suite (mission item 7)

Full `tests/agent_review/` at frozen HEAD:

```
2656 passed, 48 failed, 12 skipped, 1 warning (268s)
```

All 48 failures are the SAME, already-documented environment class from
the recovery checkpoint (`AGENT_REVIEW_V2_POST_200F_RECOVERY.md`): 46
`target_repo_write_blocked` (the tool's own git-worktree-detection guard
tripping because this run executes inside a `git worktree add` checkout,
not a defect in tested code) — this run measured 41 in that shape
directly plus the same defect surfacing differently across 6
`test_agent_review_e2e_contract.py` cases in this exact run (all
confirmed individually to assert `error_class == "target_repo_write_
blocked"`) — and 2 `sudo`-denial (`test_execute_denies_sudo_inside_the_
isolated_check`, `test_sudo_path_resolves_to_an_absolute_path_via_a_
fixed_search_list`), both confirmed to be the "fail locally, pass in CI"
class already on file. Zero product/regression-class failures.

`tests/agent_review/test_redaction.py` (pre-existing, 7 cases),
`test_redaction_200f_red_corpus.py` (27), `test_safe_review_material.py`
(56), `test_redaction_source_parseability_regressions.py` (14): all 104
pass.

Root-level `tests/` files that also happen to embed example secrets
(`test_action_run.py`, `test_run_history.py`, `test_api_auth.py`,
`test_aiops_chat_router.py`, `test_runtime_transition_tooling.py`,
`test_github_agent_review.py`, `test_aiops_environment_contract.py`) do
NOT import `app.agent_review.redaction` — confirmed by grep, they exercise
a completely separate, unrelated redaction implementation in
`scripts/github_agent_review.py`. Not part of this module's regression
surface; run anyway (290 passed) to confirm no accidental import-path
coupling.

## Review round 1 (head `ed25358`)

Two independent adversarial reviews dispatched via the Agent tool
(general-purpose subagents), explicitly instructed to find NEW leak
shapes and NEW real-source damage beyond this checkpoint's original
corpus — not to re-verify existing tests.

**Lane A — 3 P0s, all in `redaction.py`/`safe_review_material.py`,
independently reproduced by me before any fix:**

1. Compound/prefixed key names (`DB_PASSWORD`, `STRIPE_API_KEY`,
   `JWT_SECRET`, `ADMIN_PASSWORD`) bypassed both the text scanner and the
   JSON dict-field path entirely — `_match_key_at` required an
   underscore-bounded WHOLE-WORD match against an enumerated literal set,
   so a compound identifier never matched at all (kebab-case like
   `db-password` DID match, purely because `-` isn't a word character and
   accidentally acted as a boundary — the asymmetry was itself the tell).
   Reproduced: `derive_safe_review_material('db_password = "SuperSecretPass123!"')`
   → `SAFE_UNCHANGED`, secret verbatim.
2. A comma-joined value under one key (`api_key=abcd1234,efgh5678`) only
   had the pre-comma half captured as a witness and redacted; the tail
   leaked and, because it was never recorded as a witness, postcondition
   verification had nothing to check it against. Reproduced:
   `derive_safe_review_material('api_key=abcd1234,efgh5678')` →
   `SAFELY_TRANSFORMED`, output `'api_key=[REDACTED],efgh5678'`,
   `postcondition_verified=True`.
3. Unquoted dotted `.env`-style values (`token=deadbeef.cafebabe1234`,
   `password=admin.hunter2value`) were spared OUTRIGHT by the Python-
   attribute-chain carve-out (meant for `self.api_key`), with ZERO witness
   recorded — a silent false negative, worse than 1-2 (which at least
   produce a checkable claim). Reproduced directly.

**Lane B — 1 P0, different mechanism:** `DLPOverrideConfig.additional_
safe_substrings` reopened the "claims redacted while leaking" defect
through its own documented extension point. The docstring promised
exemption only for "the entire matched value", but the implementation
removed the value from the GLOBAL postcondition witness list — so a
SECOND, never-redacted occurrence of the same literal elsewhere in the
material (e.g. repeated in a comment) escaped verification entirely.
Reproduced: control (no `dlp_config`) correctly returns
`BLOCKED_UNSAFE_TO_TRANSFORM`; with `additional_safe_substrings={the
secret}`, returns `SAFELY_TRANSFORMED`/`postcondition_verified=True` with
the secret still present in the comment.

Lower priority (P2/P3, addressed in the same pass): `_sub_database_urls`
was missing the placeholder check its sibling `_sub_credential_urls` had
(over-redaction, not a leak) — fixed alongside the P0s.

### Structural vs. point-fix decision

The coordinator's framing was adopted: rather than four independent
patches, the root architectural question was whether postcondition
verification should check against a re-scan of the final output instead
of only the witnesses the scanner happened to record. Investigation found
that framing explains findings 1 and 2 (missed/partial witnesses) but
NOT finding 3 (the value was never even a *candidate*, so no re-scan of
"suspicious remnants" would surface it either) or finding 4 (a
DIFFERENT occurrence, at a location the scanner structurally never
visits — a comment — not a byproduct of an incomplete transform). Each
fix therefore targets its actual mechanism: word-based key matching
(1), a bounded comma-tail lookahead (2), a narrowed dotted-reference
allowlist (3), and rewiring the DLP override through the redactor's own
placeholder check so a declared-safe value is never a witness in the
first place, rather than being subtracted from the witness list after
the fact (4). The postcondition check itself (`_verify_postcondition`)
was NOT found to be logically wrong in round 1 — every witness it WAS
given, it correctly verified; the defects were all upstream, in what did
or didn't become a witness.

### Correction sub-rounds (two were needed)

The word-based key-matching fix for finding 1, naively applied (match if
ANY component word of a compound identifier is sensitive), reopened real-
source damage the differential oracle caught before a second review lane
was ever dispatched:

- `command_token: SafeIdentifier` (this repository's own required
  negative-corpus exemplar for "project-defined CapitalCase type names")
  — "token" as a bare compound SUFFIX is genuinely overloaded
  (`command_token`/`expected_token`/`parse_token` are ordinary code;
  `session_token`/`auth_token` are credentials) with no structural
  signal to tell them apart, and casing was rejected again for the same
  reason it was rejected the first time (the random-secret corpus proved
  it reopens the leak). Resolved: `token` only counts as sensitive when
  it is the WHOLE identifier alone, or via the existing explicit compound
  bigrams (`access_token`, `refresh_token`, `session_token`, `auth_token`)
  — the same "ambiguous word, enumerate the compound forms" trade-off
  this module already applied to `key`.
- `secret_like_values_found` (4 words) and `hardcoded_secret_confirmed`
  (3 words) — both COUNTS/FLAGS describing secret-shaped material, not
  credential holders — were flagged via "secret" as a component. Resolved
  by capping the single-word simple-word check at ≤2 words (every real
  Lane-A witness is exactly 2 words); the bigram check is intentionally
  NOT capped the same way, since `stripe_api_key` (3 words) is a real
  Lane-A witness caught via the `("api","key")` bigram and a bigram is a
  structurally lower-false-positive signal than a single word.
- `allow_credentials=True` (FastAPI/Django CORS boolean flag,
  `app/main.py`) — resolved by moving `credential`/`credentials` to the
  same single-word-only treatment as `token`.
- Two bugs surfaced in the comma-tail lookahead itself while fixing the
  above: it only recognised a BARE identifier as "a new key follows",
  missing JSON/dict-literal QUOTED keys (`{"secret_like_values_found": 1,
  "redacted_lines_present": True}` had its first value swallow the rest
  of the dict); and its whitespace-skip did not include newlines, so
  ANY multi-line dict/call literal (the dominant real-world formatting
  style) failed the lookahead and triggered the same over-absorption.
  Both fixed; both are now permanent regression coverage
  (`json_lines` in the performance corpus, quoted-key + newline handling
  in `_looks_like_new_assignment_after`'s own logic).
- `{"authorization": "Bearer xyz"}` (JSON dict field) briefly regressed
  when the word-vocabulary rewrite dropped `authorization` from the
  matchable-word set entirely (only the TEXT-scanner exclusion flag was
  preserved, not the underlying word membership needed by the JSON path,
  which intentionally does NOT exclude `authorization`). Fixed by adding
  it back to the word set and gating exclusion via a parameter
  (`exclude_authorization`, default `True` for the text scanner, `False`
  for the JSON dict-field path).

Real-source differential oracle was re-run after every sub-fix (not just
once at the end) specifically because the first fix attempt reintroduced
damage silently; final state before dispatching fresh review: 0
parseability regressions, confirmed at `98fe850`/`d97d656`.

### Mutation testing (round 2 additions)

Repeated for the new logic, same discipline (commit before mutating,
confirm RED, restore, confirm GREEN, verified via `git status --short`
clean after each restore):

| # | Mutation | Result |
|---|---|---|
| 6 | Disable compound-bigram matching (`_COMPOUND_SENSITIVE_BIGRAMS` check forced `False`) | RED against a `STRIPE_API_KEY` case using a plain (non vendor-prefix-shaped) value — the FIRST version of this test accidentally used a `sk_live_...`-shaped value that the independent Stripe-prefix detector also catches, which stayed GREEN under the mutation and would have masked a real regression; swapped to an isolated witness |
| 7 | Disable the comma-tail lookahead extension (forced the absorb-loop condition `False`) | RED: 4 tests (comma-joined positive-corpus cases) |
| 8 | Disable `state.extra_safe_values` wiring from `DLPOverrideConfig` | RED: 2 tests (DLP override cases) — mutation fails CLOSED (`BLOCKED_UNSAFE_TO_TRANSFORM`) rather than leaking, confirming the postcondition check remains a live backstop even when the new wiring is broken |

### Verdict, round 1

Both lanes' P0s independently reproduced and fixed. Real-source oracle
clean (0 regressions) at the corrected head. Full `tests/agent_review/`
regression suite re-run (see below). Two fresh, independent review lanes
dispatched against the new frozen head per the mission's requirement that
prior review is invalidated by a source change — at least one lane
instructed to specifically re-attack the postcondition-verification
architecture itself (not just the four found mechanisms), since that
abstraction being refuted a second time is the designated
`STOP_G2_SAFE_REVIEW_MATERIAL_NOT_CONVERGING` trigger.

## Review round 2

[Recorded here once both fresh lanes return, evaluated against head
`d97d656` (or later, if the regression suite requires a further fix
before dispatch).]

## Terminal verdict

[Recorded here once round 2 completes: `PRIMITIVE_NON_REFUTED` or
`STOP_G2_SAFE_REVIEW_MATERIAL_NOT_CONVERGING`.]

## Not authorized / not done

Per the task contract: no Ready marking, no merge, no tag/release, no
deploy, no CI workflow changes, no live Router/LLM provider calls, no
mutation of AgentEscala/InterLeitos/CAEM repos, `#200` not closed, `#273`
not touched. PR opened in Draft state only.
