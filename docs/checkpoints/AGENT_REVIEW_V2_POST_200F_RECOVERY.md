# AgentReview v2 — Post-#200-F Recovery Checkpoint

**Corte temporal:** 2026-09-01. **Classe:** estado observado, revalidado por
census read-only imediatamente antes deste documento; não confie neste
documento além do próximo `git fetch`.

## Identidade viva revalidada

```yaml
repository: mglpsw/aiops-orchestrator
master_sha: f70af2e635643d1ee96ba431857002ae079b502b
master_tree: 945f3247a9e8ad534a0d35f4450b24446906f30c
drift: none   # matches the overnight grant's expected identity exactly

pr_277:
  state: CLOSED
  merged: false
  head: fe5b0705c66087fb50312865b545c8a3a2b359e0
  branch_preserved: true
  classification: FROZEN_FORENSIC_200F
  qualification_transfer: false

issue_200:
  state: OPEN

competing_work: none   # no open issue/PR referencing 200-G1..200-G4 at census time
```

## What exists on `master` right now

`app/agent_review/` carries 75 modules and `tests/agent_review/` ~95 test
files — the full v1/v2 pipeline (chunking, payload building, trusted checks,
target-pack install/apply, quality gate, readiness, redaction, etc.) — plus
one existing CLI entry point, `app/agent_review/cli.py` (`build_intake`),
which is not a composer.

**There is no operational composer and no product CLI on master.** Confirmed
by exhaustive grep at `f70af2e6` for every #274/#276/#277 symbol
(`operational_inner_control`, `operational_run_v2`, `operational_subject_v2`,
`operational_scope_v2`, `operational_refusal_v2`, `operational_ingress_v2`,
`operational_bounded_git_v2`, `operational_result_binding_v2`,
`controlled_subject_v2`, `toolrepo_execution_subject_v2`,
`scripts/aiops-review-run-v2.py`, `declared_toolrepo_sha`, `--no-replace-objects`)
— zero hits. All of it was branch-only artifact from #274/#276/#277 and died
unmerged with each. This is the same fact #200-F's own checkpoint already
recorded; it is reconfirmed here rather than re-asserted.

**Full `tests/agent_review/` run at `f70af2e6`:** 2559 passed, 48 failed, 12
skipped (~4m). All 48 failures are environment-class, not product
regressions: 46 are `target_repo_write_blocked` — the tool's own
git-worktree-detection guard tripping because the run was isolated inside a
`git worktree add` checkout (not a defect in the tested code; re-run from a
non-worktree checkout to fully confirm — flagged as inference, not proven);
2 are the known `sudo`-denial class already on file
([[project_sudo_tests_env_failure]] equivalent — "fail locally, pass in CI").
No product/regression-class failures found on live master.

Master's own `app/agent_review/redaction.py` is a **different, currently
shipping** module — do not conflate it with the failed #277 round-2
quoted-secret redesign. The `DO_NOT_PORT` verdict on Authority E below
applies only to that experimental redesign, never merged, not to what
master ships today.

## Port ledger

| Artifact | Origin (branch/commit) | Disposition | Basis |
|---|---|---|---|
| `ControlledTargetSubjectV2` (`controlled_subject_v2.py`) | #276/#277 | **PORT_WITH_REVALIDATION** | source severance + non-mutation held; needs new tests, no qualification transfer |
| `ToolrepoExecutionSubjectV2` (`toolrepo_execution_subject_v2.py`) | #276/#277 | **PORT_WITH_REVALIDATION** | committed-byte identity held under forgery attempts in isolation |
| bounded child git environment (allowlist env, `os.defpath` pin, absolute `argv[0]`) | #276/#277 | **PORT_WITH_REVALIDATION** | round-2 `PATH` P0 genuinely closed; needs new fake-binary corpus |
| controlled reference-material read path | #276/#277 | **PORT_WITH_REVALIDATION** | reads from controlled subject, not live target |
| one-synthesis invariant (object-identity, non-vacuous) | #276/#277 | **PORT_WITH_REVALIDATION** | genuine under isolation; needs non-interned-value proof in successor |
| shared representability predicate (`path_violates_relative_path_contract_v2`, `diff_acquisition_v2.py` ↔ `operational_scope_v2.py`) | #200-F round-1 correction | **PORT_WITH_REVALIDATION** | Lane B `NON_REFUTED` twice; 2,592-case differential fuzz + 82-repo real-git fuzz + hostile `git mktree` corpus |
| scope-authority disagreement detector (`operational_run_scope_authority_disagreement`) | #200-F | **PORT_WITH_REVALIDATION** | same Lane B evidence set; structural anti-recurrence pattern worth keeping independent of its current host module |
| git type-change pairing (`_is_type_change_pair_v2`) | #200-F | **PORT_WITH_REVALIDATION** | held under fuzz; docstring overclaimed exactness (P2, cosmetic) |
| `ls-tree` + `cat-file --batch` subject materialization (not `git archive`) | #276/#277 | **PORT_WITH_REVALIDATION** | proved structurally superior to `git archive` (immune to `.gitattributes` export-ignore/export-subst); same Lane B evidence set |
| `--no-replace-objects` + bounded git subprocess discipline | #276/#277 | **PORT_WITH_REVALIDATION** | part of same NON_REFUTED set |
| two-process outer/inner architecture; stdout-only product contract | #274/#276/#277 | **PORT_AS_CONCEPT** | re-derive the channel itself; do not copy `operational_inner_control_v2.py` |
| full #274+#276+#277 adversarial corpus (all rounds) | #274/#276/#277 | **PORT_AS_RED_TEST** | carry forward as failing-first tests against the new primitives, not as passing inheritance |
| `operational_inner_control_v2.py` — exclusive outer→inner authority channel, `declared_toolrepo_sha` binding | #200-F | **DO_NOT_PORT (authority)** | refuted twice independently: round 1 forgeable by narrowing `subject_root`; round 2 forgeable by fabrication (honest digest over tampered-but-correctly-rooted code, sha only shape-checked) |
| #277 round-2 "suspect unless benign" quoted-secret redesign of `redaction.py` | #200-F | **DO_NOT_PORT (authority)** | refuted twice; round 2 worse than the code it replaced — ~44% random-secret leak, JWTs entirely spared, multiline triple-quote leak reproduced byte-for-byte, quadratic (16k-char line → 90s, real DoS) |
| CLI exception-tuple "complete by construction" control | #200-E | **DO_NOT_PORT (authority)** | green while leaking a raw traceback outside its own enumerated classes |
| private `--_` argv flags / textual blacklist | #200-E | **DO_NOT_PORT (authority)** | bypassed via argparse unambiguous-abbreviation parsing |
| `if assembly.excluded_paths: raise` | #200-E | **DO_NOT_PORT (product policy)** | denies review outright for renames/chmod/binary/lockfile/empty-file changes — wrong level of abstraction |
| `operational_run_scope_silently_narrowed` reason code | #200-E | **DO_NOT_PORT (product policy)** | misnomer: composer refused, nothing was silently narrowed |
| #200-D and earlier composition attempts (`ar-200-d`, `ar-200d-successor` worktrees) | #200-D | **SUPERSEDED** | superseded by #200-E/#200-F's stricter two-process design; not revisited unless G5 independently needs a fallback |

## Decomposition for this slice

Per the overnight grant, the remaining work is split into four independent
primitives plus a recomposition, tracked as children of `#200`:

- **G1 — executed source identity.** Replace the falsified self-reported
  model (`bytes + caller document → claimed commit`) with a strictly
  directional one (`commit → bytes`). Verifier owns the verification domain;
  the inner process never "believes" a caller-supplied sha.
- **G2 — safe review material / DLP.** Replace the falsified regex-widening
  model with `SAFE_UNCHANGED | SAFELY_TRANSFORMED | BLOCKED_UNSAFE_TO_TRANSFORM`
  and a fail-closed rule: suspected-sensitive + transformation not provably
  safe → never sent to the model.
- **G3 — scope completeness contract.** Resolve `STOP_SCOPE_CONTRACT_REQUIRED`
  from #200-F's ADR: `FragmentCoverage != ScopeCompleteness`, represented
  honestly in an additive contract.
- **G4 — external material ingress closure.** Close every caller-controlled
  material source (not just the 9 scalar CLI flags already covered) —
  `--responses` file content is the mandatory RED witness, reproduced live
  in #200-F round 2 and never fixed.
- **G5 — operational product recomposition.** Only attempted if G1–G4 are
  each independently `PRIMITIVE_NON_REFUTED`. Requalifies everything from
  zero; no qualification transfer from any primitive PR.

No primitive branch reuses #274/#276/#277/#200-F branches or worktrees. Each
starts fresh from live `master`.

## CAEM lessons pending

```yaml
CAEM_LESSONS_PENDING:
  refutation_diversity:
    proposition: >
      Repeated independent refutation by distinct mechanisms is stronger
      evidence of an incorrect abstraction than simple recurrence of one
      known witness.
  oracle_independence_is_not_completeness:
    proposition: >
      Oracle independence (a second detector not sharing the first
      detector's blind spots or witness bookkeeping) and oracle completeness
      (the detector enumerates its whole domain) are separate properties.
      SAFE must come from positive proof within a bounded, provable domain,
      never from mere absence of a match over an open/unenumerable grammar
      such as arbitrary source code -- "no finding" is not "proven safe."
      Surfaced by #200-G2B round-1 review against PR #293: an independent,
      correctly-seated (real pre-HTTP body, structurally unrelated to the
      forward redactor) oracle still returned false OUTBOUND_SAFE on
      `password={"..."}`, kwarg-shaped assignments, and hex-token values
      exempted as SHA-like -- independence alone did not close the gap that
      sank the original #277/#286 witness-scoped design.
```
