# AgentReview v1 — C2 source boundary (`SourceBoundaryDecision`)

Owner: `mglpsw/aiops-orchestrator#375` · Epic: `#221` · Roadmap: `#46` · Campaign: `AR-V1-FINAL-INTEGRATION-20261014`

Frozen before the production patch. It supersedes nothing in the MethodGuidance carrier decision
(`AGENT_REVIEW_V1_METHOD_GUIDANCE_CARRIER.md`); it generalises its reader to every v1 source reader.

## Property

```text
GenuinelyAbsentSource != PresentButInvalidOrUnsafeSource
```

A target-owned file that is present but unsafe or unreadable must never read as "nothing declared" and produce
`plan=complete / gate=passed`. Measured on `master@6330058c` before this change: a dangling link at
`.aiops/domain-contracts.yaml` or `.aiops/review-packs.yaml` was indistinguishable from an absent file.

```text
Untrusted target file -> acquire_source (provenance, containment, bounded read)
  -> decode -> parse -> scan_structure (cycle / depth / expanded nodes) -> typed source state
  -> ReviewIntake -> SemanticChunkPlan -> payload / synthesis -> QualityGate
```

## Decision

```yaml
SourceBoundaryDecision:
  trusted_roots:
    repo_root: the CLI-supplied --repo-root (profile, contracts, packs, method sidecar and sources)
    agent_dir: the CLI-supplied --agent-dir (declared artifacts)   # not necessarily the Git root
  single_read_authority: app/agent_review/source_boundary.py::acquire_source
  consumers:
    repo_profile:        repo_profile._load_yaml_file         (.aiops/repo-profile.yaml)
    domain_contracts:    repo_profile._load_optional_yaml     (.aiops/domain-contracts.yaml)
    review_packs:        repo_profile._load_optional_yaml     (.aiops/review-packs.yaml)
    method_guidance:     repo_profile._load_method_guidance_sidecar + _read_bounded_in_checkout (sources)
    declared_artifacts:  artifact_loader.load_declared_artifacts (agent_dir)
  acquisition:
    directory_descriptor: root opened once; each component opened relative to the previous descriptor
    symlink_policy: refused on EVERY component (O_NOFOLLOW; dangling, external and in-tree links alike)
    hardlink_policy: st_nlink != 1 refused
    fifo_policy: O_NONBLOCK open, then refused as a non-regular file (never blocks)
    byte_identity: the fstat that is checked is on the descriptor that is read; one read feeds the parser
    presence: decided by the SAME open; only ENOENT is ABSENT; no exists()/lexists() probe, no resolve-then-open
    unsupported_platform: fails closed (UNSUPPORTED)
  source_states:
    ABSENT:      the entry does not exist (ENOENT)
    PRESENT:     a bounded regular file with a single link
    UNSAFE:      link on the path, hard link, non-regular file, non-directory parent, invalid path
    UNREADABLE:  presence or bytes cannot be established (EACCES, EIO, root unavailable)
    OVERSIZE:    beyond the byte bound (never truncated)
    UNSUPPORTED: platform cannot give the guarantees
  parser:
    yaml: yaml.safe_load; decode_invalid / yaml_invalid / resource_limit (RecursionError) are typed
    json: json.loads; json_invalid / artifact_structure_limit
    text: decoded only
  structure: # scan_structure, iterative, memoised, runs BEFORE redaction / model_dump / serialisation
    max_depth: 64
    max_expanded_nodes: 50000        # contract / profile / sidecar sources
    max_expanded_nodes_artifact: 500000
    cycle: rejected;  sharing (DAG): admitted, counted once per reference
    scanner_work: bounded by the node budget for JSON (len >= max_nodes is rejected before enumeration; every unique
      container adds its own contribution to a running sum that rejects as soon as it exceeds the budget) and by the
      number of references in the input for YAML aliases (linear, capped by the 2 MiB byte cap), so a
      64 MiB JSON of millions of empty lists costs the PARSE, not the scan (8 M elements: 2.9 s / 654 MB, was 24.8 s / 1.9 GB)
  bounds_bytes:
    yaml_sources: 2 MiB   # repo-profile, contracts, packs, sidecar, yaml artifacts
    method_documents: 4 MiB   # hashed and searched, never parsed
    artifacts_json_text_diff: 64 MiB   # a special/huge-file bound, not a truncation policy
  contracts:
    absent_source: ABSENT -> no declaration (unchanged)
    present_valid: PRESENT_VALID
    present_invalid_or_unsafe: typed loader limitation + type-valid INVALID carrier
        -> invalid_source_contract: / invalid_source_review_packs: MALFORMED_SHAPE (already critical)
        -> plan degraded -> gate != passed
    repo_profile_unsafe: status=failed, repo_profile_source_<state>; intake exits non-zero (never "profile missing")
    method_guidance_unsafe: method_guidance_yaml_invalid -> invalid_source_method_guidance: (unchanged)
    artifacts:
      missing: status=missing; REQUIRED -> required_artifact_missing (unchanged)
      unsafe_or_unreadable: status=invalid, error_class artifact_unsafe | artifact_unreadable | artifact_oversize |
                            artifact_structure_limit | artifact_unsupported  -> artifact_invalid:<name>, intake degraded
```

Loader limitation codes (`<name>_source_unsafe`, `_source_unreadable`, `_source_oversize`, `_source_unsupported`,
`_decode_invalid`, `_resource_limit`, `_structure_limit`; `_yaml_invalid` unchanged and receipt-pinned) are
observability. The **consequence** is carried by the INVALID carrier that the planner already reads; no new code is
introduced that no consumer reads, and `CRITICAL_CONTRACT_LIMITATION_PREFIXES` is untouched.

## Parameters and why

| Parameter | Value | Evidence |
|---|---|---|
| depth / expanded nodes | 64 / 50 000 | Real AgentEscala sources: ≤ 21 KiB, ≤ 493 expanded nodes, depth ≤ 4 (≈ 100× headroom). These are **design values**, not normative requirements. |
| YAML byte cap | 2 MiB | The MethodGuidance sidecar grammar bounds a valid document at ≈ 1.2 MiB; real sources ≤ 21 KiB. |
| artifact cap | 64 MiB | v1 artifacts declare no `max_bytes`; the cap exists to bound special/huge files, an oversize artifact is INVALID, never truncated. |

PyYAML is pure Python. For plain documents (anchors and aliases are shared by reference) its cost is linear in bytes:
measured ≈ 5.8 s / 142 MB at 1.3 MB and ≈ 27 s / 538 MB at 5.7 MB. **YAML merge keys (`<<`) are the exception:
`flatten_mapping` copies per reference, so a ~1 KB chain of 30 merges did not finish in 60 s — an exponential cost
inside the parser, before any scan.** Decision: merge keys are refused (`SafeSourceLoader`), as the v2 profile loader
already does; no real AgentEscala source uses one. Nesting ≳ 500 raises `RecursionError` inside the parser (typed
`resource_limit`, < 1 s).

**The byte cap is the only remaining pre-parse bound, so it is a cost cap and not a proof that the parser is
resource-bounded** (see non-claims). Measured by the independent review inside the cap: the densest nested map/sequence
input costs ≈ 11 s/MiB (≈ 22 s at 2 MiB), a 200k-pair flow map ≈ 15 s / 485 MB; all linear. A 64 MiB artifact cap is likewise not a memory bound: a 64 MiB JSON of empty
lists peaked at ≈ 1.5 GB RSS because the parse precedes the scan (a `MemoryError` is typed, not fatal).

## Reader census (what is and is not behind the boundary)

| Surface | Reader | Status |
|---|---|---|
| `.aiops/repo-profile.yaml`, `domain-contracts.yaml`, `review-packs.yaml` | `repo_profile._load_yaml_file` | behind `acquire_source` + `scan_structure` |
| `.aiops/method-guidance.yaml` and method source documents | `repo_profile._load_method_guidance_sidecar`, `_read_bounded_in_checkout` | behind `acquire_source`; sidecar also behind `scan_structure` |
| declared artifacts (`agent_dir`) | `artifact_loader.load_declared_artifacts` | behind `acquire_source`; json / yaml also behind `scan_structure` |
| `--checks`, `--validation-evidence` | `payload_cost_model.load_optional_json_with_limitation` | **outside**: explicit CLI paths supplied by the workflow (pipeline stage inputs, not target-declared sources); a dangling path is `optional_artifact_missing`, an informational limitation, never a contract `complete` |
| chunk plan / results / final review / telemetry / signature inputs | `json.loads(Path(...).read_text())` in the stage modules | **outside**: produced by earlier pipeline stages of the same run |
| `migrate-agent-review-profile-v1-v2.py --profile-v1` | `Path.read_text` | **outside**: operator-supplied |

The consumers (`build_intake`, planner, builder, synthesizer, gate) receive typed states only through the carriers and
statuses above; no new limitation code was added that none of them reads.

## What changes for the positive cases

No observable change for: no sidecar, absent or valid contract sources, absent optional artifacts, the MethodGuidance
CURRENT/BOUND path and (the universal-newline translation of `read_text` is preserved) CR/LF text artifacts. These
are the DESIGNED rejections of inputs that used to be accepted:

- links in the declared-artifact tree, even ones that resolved inside `agent_dir`, and hard-linked files (single no-link
  policy);
- a YAML source or YAML artifact above 2 MiB; a contract / profile / sidecar source above 50 000 expanded nodes or 64
  levels; a JSON or YAML artifact above 500 000 expanded nodes (measured: 75 000 `file-diff-context` entries are
  rejected, 60 000 are accepted) or 64 levels;
- YAML merge keys (`<<`) in any v1 C2 YAML source; `!!binary` scalars (raw bytes cannot be carried).

## Pending normative decision (not invented here)

`parse(...) or {}` reads an **empty file, a comments-only file, `null`, `~`, `0`, `false`, `""`, `{}` and `[]`** as
an empty declaration, exactly as before. The same coercion applies to `repo-profile.yaml`, and a profile that parses to
a non-mapping (a list or a scalar) is read as an empty profile (intake `complete`; the gate is degraded only
incidentally, by `file_context_missing`). Whether a PRESENT-but-empty contract file should be INVALID (a truncated write is
indistinguishable from "no contracts") is a policy decision recorded as `STOP_POLICY_DECISION_REQUIRED` for that
sub-item; compatibility is preserved and pinned as a characterisation test, not endorsed.

## Non-claims

- **Whole-parser resource-boundedness is NOT claimed.** `scan_structure` is a post-parse bound (it prevents the
  alias-expansion blow-up of redaction/serialisation, which was measured as a non-terminating load at the master
  readers) and merge keys are refused because they are exponential inside the parser; every other pre-scan parser
  cost is bounded only by the byte cap above. No second parser and no isolation process were introduced; if a
  further parser-internal blow-up is reproduced, parsing isolation needs its own architecture decision.
- Universal filesystem security is NOT claimed (bind mounts, NFS/overlay `st_nlink` semantics, concurrent mutation of
  the directory tree under the walk are not modelled; the walk is descriptor-relative, so a swap cannot redirect it,
  but a file can still change content between the single read and a later decision that reuses the path).
- Duplicate mapping keys in contract sources are still last-wins (only the MethodGuidance sidecar refuses them).
- A non-string mapping key inside a YAML **artifact** is not refused (artifacts never had that census; contract and
  sidecar sources do).
- The internal carrier sentinel `__source_yaml_unparsable__` remains visible in `intake.json` (never in plan, payload,
  final or gate); it is the typed carrier that makes INVALID reach the planner and is pinned by tests.
- Not Gate B/C, Control A/B, release, repin or `V1_FINAL_FREEZE`.

## Delivery

One Draft PR for #375 with causally separated commits: **A** source acquisition / containment / typed states,
**B** bounded structural scan, **C** end-to-end composition tests and this document.
