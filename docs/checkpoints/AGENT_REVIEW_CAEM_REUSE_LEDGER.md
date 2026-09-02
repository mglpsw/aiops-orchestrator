# AgentReview / CAEM reuse ledger

**Status:** first entry (`G1C`), started on `feat/200-g1c-trusted-object-authority`.
This file is carried forward by later branches in the `#200-G` sequence
(`G1D`, `G1B`, `G3C`, `G2C`, the internal/Codex review rounds, `G5`, and
final merge/CI wiring); each fills in its own entry when it lands. Entries
are added by the branch that actually did the work -- this file does not
speculate about content a later branch has not written yet.

## Authority note -- read this before reading anything else in this file

CAEM is design reference and prior art for this repository, **not**
automatically authority and **not** automatically qualification.

This repository's own CAEM pin, `config/caem/caem-3.0-f0.pin.json`, declares
`authority.authority_effect: "none"`. Every ADR or mechanism cited below
lives past that pin (in `caem-3.0-c7`/`c8`, `maturity: candidate,
published: false`), in `mglpsw/caem`, a repository this project does not
own and does not have a grant to bind against. Citing an ADR here means: an
engineer read it, recognised a shape this repository's own problem also
has, and implemented an independent solution for this repository's own
subject matter. It never means:

- that CAEM has reviewed, ratified, or is aware of this repository's use of
  the shape;
- that this repository's implementation is conformant with, certified by,
  or interoperable with CAEM's own N-series pipeline;
- that qualification, verification, or trust established in CAEM transfers
  to this repository's artifacts, or vice versa;
- that a future CAEM repin would make any past entry in this ledger
  retroactively authoritative -- repin is its own separate, explicit,
  CAEM-repo-level grant, decided on its own merits at the time it is
  requested, never implied by a citation made before it.

Where an entry below says a prior-art ADR is "read as design reference,"
that is the complete extent of the claim. The engineering judgement, the
implementation, the test corpus, and the review are this repository's own
and stand or fall on their own evidence, not on CAEM's.

## Entries

```yaml
G1C:
  predecessors: [CAEM ADR 0011, CAEM ADR 0012]
  reuse:
    - trusted local CAS by exact digest
    - PATH/network not authority
    - mutable path as discovery only
    - retained capability
    - hard budgets before untrusted parsing
  scope: >-
    #200-G1C (issue #303) -- isolated immutable Git object-store authority.
    Read ADR 0011 (N5 proof-carrying bundle: "verifier and tool bytes
    resolve from a trusted local CAS by exact digest without PATH or
    network... hard budgets precede untrusted parsing") and ADR 0012 (N5
    authenticated detached launcher attestation: "runtime paths are
    discovery inputs only... mutation of the discovery inode after
    authentication cannot change the executed bytes") as design reference
    for the same shape applied to a different subject -- a Git object
    store, not a launcher/verifier binary. Independently implemented in
    `app/agent_review/trusted_object_authority_v2.py`: the live/hostile Git
    checkout is discovery input only (a `.git`/`commondir` pointer file
    read, plus a raw filesystem byte-copy of `objects/`+`refs/`, never a
    fetch-capable git invocation), the private content-addressed copy
    (hand-authored, remote-less, config-less) is the sole subsequent trust
    root, the capability object (`TrustedObjectAuthorityV2`) is retained
    and unforgeable (sentinel-gated construction, marker-verified on every
    use, no external-repo-path parameter on any operation), and hard
    byte/object-count budgets are enforced during acquisition, before any
    copied byte is handed to a git reading primitive for interpretation.
    Correction round 1 (two internal adversarial review lanes; findings
    independently reproduced before fixing) sharpened the "resolve by exact
    digest" reuse from a structural description into an enforced check:
    `_verify_loose_object_hash_v2` re-derives every copied loose object's
    sha1/sha256 from its own decompressed content and refuses on any
    mismatch against the path it claims to be -- the same digest-verified-
    copy shape ADR 0011 states for verifier/tool bytes, independently
    implemented here for git loose objects specifically. Correction round
    2 (independent human review) closed the adjacent packed-object gap the
    round-1 entry above left as a named residual: `_verify_pack_integrity_v2`
    delegates to git's own `verify-pack` plumbing (bounded, local,
    network-incapable, run against the CAS itself after every pack/idx is
    copied in and before the authority is ever yielded) rather than
    independently re-hashing packed objects in Python, which would mean
    reimplementing git's pack/delta format. Round 2 also closed a
    root-of-walk symlink-following gap in round 1's own no-follow
    enforcement (`objects`, `objects/pack`, `refs/heads`, `refs/tags` were
    checked with a plain, symlink-following `.is_dir()` before round 1's
    scandir-level guard was ever entered) and a linked-worktree `HEAD`
    resolution defect (round 1 collapsed `git_dir`/`common_dir` into one
    value and read `HEAD` from the shared one, silently returning the MAIN
    worktree's `HEAD` for a caller pointed at a different, linked
    worktree) -- see `_GitDirectoriesV2`.
  same_uid_scope_decision: >-
    Correction round 2 also required an explicit TCB-floor-style scope
    decision (previously implicit): same-UID/concurrent mutation of the
    private CAS directory during an authority's lifetime is OUT OF SCOPE
    for G1C, inheriting the existing `host_arbitrary_code_attacker`
    exclusion already declared in `commit_derived_execution_identity_v2.py`
    (the sibling module G1C composes with), not a new or separately-argued
    boundary. CAEM ADR 0012's `memfd`+seal same-UID closure is prior art
    for a categorically higher-assurance TCB (a detached launcher producing
    attestations for parties trusting neither this host nor its operator)
    and is not reused here. If a later phase (`#200-G1B`/`#200-G5`) needs
    to widen this process's own trust boundary to include a same-UID
    adversary, that is a genuinely new TCB floor for that phase to declare
    explicitly, not something G1C's "immutable" language should be read as
    having silently already provided.
  not_claimed: >-
    No CAEM conformance, ratification, or qualification transfer. No
    N1-N5 pipeline invoked. No consumption of CAEM's own CAS, schemas, or
    verifier machinery -- only the architectural shape (mutable-path
    resolves once, then trust moves to an authored, controlled copy) is
    reused, reimplemented from scratch for this repository's own subject.
    No same-UID/concurrent-mutation TCB closure is claimed or reused from
    ADR 0012 -- see `same_uid_scope_decision` above.

G1D: TODO -- not yet implemented; fill in when that branch lands.

G1B: TODO -- not yet implemented; fill in when that branch lands.

G3C: TODO -- not yet implemented; fill in when that branch lands.

G2C: TODO -- not yet implemented; fill in when that branch lands.

REVIEWS: TODO -- internal/Codex review-round entries land as they occur.

G5: TODO -- not yet implemented; fill in when that branch lands.

MERGE_CI: TODO -- not yet implemented; fill in when final merge/CI wiring lands.
```
