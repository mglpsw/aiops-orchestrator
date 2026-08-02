# Codex review workflow — scoped AGENTS.md and read-only subagents (#87)

Refs #87 (parent roadmap #46, related epic #80, feeds calibration #88).
Adds a guidance/specialization layer so Codex can review
`mglpsw/aiops-orchestrator` with more precision, without becoming an
authority over the quality gate and without widening AgentReview's
operational surface.

## Architectural decision

Codex enters through three separate surfaces:

1. **VS Code/CLI, local** — review assisted by hierarchical `AGENTS.md`
   files and four read-only subagents (this document, Escopo 1/2).
2. **GitHub shadow review** — `@codex review` as an independent opinion,
   with no required check and no effect on the AIOps verdict.
3. **later correlation** — Codex findings may feed benchmark/telemetry
   (#88) only after repo/PR/HEAD revalidation, never automatically.

Central rule, restated from the issue and the root `AGENTS.md`:

```text
Codex observes and proposes.
AIOps validates contracts, binding, coverage, and lifecycle.
The quality gate remains the only automatic authority.
```

This issue does NOT insert a Codex response into `ReviewReadinessV2` or
`review-quality-gate.json`. Any future consumption of Codex output requires
its own schema, exact repo/PR/HEAD binding, manifest-linked path/range,
sanitization, deduplication without automatic promotion, and calibration
through #88 — none of that is implemented here.

## Escopo 1 — hierarchical instructions

```text
AGENTS.md                          (root — generated CAEM view, NOT edited by this issue)
app/agent_review/AGENTS.md         (new)
app/agent_router/AGENTS.md         (new)
schemas/agent-review/AGENTS.md     (new)
.github/AGENTS.md                  (new)
```

The root `AGENTS.md` is a generated CAEM view (see its own header comment:
`Source: policy/caem-policy.json`, `Regenerate/validate: ...`). This issue
adds four new, directory-scoped files that specialize it — none edits the
root file, and none weakens a hard boundary it declares. A more specific
instruction may narrow or add detail; it may never contradict or relax a
root-level invariant (CT102/CT104 separation, v1/v2 non-mixing, fail-closed
on schema/binding/coverage/identity failure, no free shell/SSH/deploy/direct
provider access).

## Escopo 2 — read-only Codex subagents

```text
.codex/config.toml
.codex/agents/contract-reviewer.toml
.codex/agents/trust-boundary-reviewer.toml
.codex/agents/test-reviewer.toml
.codex/agents/target-integration-reviewer.toml
```

All four run with `sandbox_mode = "read-only"` and `approval_policy =
"never"` — none writes to the filesystem, executes code, or reaches the
network. Each has exactly one responsibility, with no overlap:

| Subagent | Reviews | Does NOT review |
|---|---|---|
| `contract-reviewer` | contracts, hashing/canonicalization, binding, version selection, schemas, lifecycle reason-code precedence | trust boundaries, test quality, target integration |
| `trust-boundary-reviewer` | CT102/CT104, network/provider/GitHub write, runner/secrets, untrusted-data-as-instruction | contract correctness, test quality, target integration |
| `test-reviewer` | whether tests genuinely prove what they claim (happy/negative path, mutated-object bypass, stale HEAD/cross-run, partial coverage, byte-reproducibility, v1 preservation, vacuity) | production contract correctness, trust boundaries, target integration |
| `target-integration-reviewer` | generic profile/review-pack/fixture-driven integration, anti-branching on target name, InterLeitos's DLP/PHI boundary | contract correctness in isolation, general trust boundaries, general test quality |

Each `.codex/agents/*.toml` file is auto-discovered by Codex from the
`agents/` directory next to `config.toml` — no separate registration list
is needed in (or supported by) `config.toml` itself; confirmed directly
with `codex doctor`, which reports `config.load: ok` with zero startup
warnings once each file defines `developer_instructions` (not
`instructions` — an earlier draft of this PR used the wrong field name and
`codex doctor` silently dropped all four agents as a result, confirmed and
fixed before merge). Recommended initial parallelism: 3–4 agents at once,
prioritizing independent read-only investigation over concurrent writes —
none of the four write, so this is a throughput/cost knob, never a safety
one.

## Escopo 3 — reproducible review recipe

Local (Codex CLI/VS Code):

```text
/review
Revise a branch atual contra master.
Delegue contratos, trust boundaries, testes e integração de target.
Não comente estilo.
Publique apenas achados demonstráveis no diff, com arquivo/linha,
cenário de falha, severidade e regressão capaz de provar a correção.
```

This recipe delegates to the four subagents above (contract, trust
boundary, test, target integration), each read-only, each reviewing its own
slice of the diff. It never asks Codex to fix anything itself — findings
only.

GitHub:

```text
@codex review
```

The GitHub opinion remains shadow/advisory in this phase: no required
check is created or implied, and it has no effect on `review-quality-gate
.json` (v1) or `ReviewReadinessV2` (v2). See `.github/AGENTS.md` for the
trust-boundary rules governing anything triggered from a PR comment.

## Escopo 4 — rule evaluation (deferred, not a gate)

The issue's Escopo 4 (a fixture/patch that must trigger each critical rule,
a safe counterexample that must not, and an unrelated change that must
generate no noise, tracked per rule ID) is **not** a gate for #88's
calibration — #88 (issue text, line ~91) asks for "as regras e subagentes
da #87", not this meta-evaluation. A minimal starting structure is provided
at `tests/codex_review_rules/README.md` and one worked example per
subagent's first rule; growing it further is explicit follow-up work, not
part of this slice's acceptance criteria.

## Security

- `danger-full-access` is never the default for any of the four subagents;
- no secret is provided to a job that executes PR-authored code;
- network/provider access is off by default for local subagents
  (`.codex/config.toml`'s `sandbox_workspace_write.network_access = false`);
- no prompt/config in this repository carries a token, local path, real
  env value, or raw payload;
- Codex is never authorized to merge, deploy, or remediate;
- Codex output is never a required check in this phase.

## Deliberately out of scope

- the Codex execution API as part of the v2 pipeline;
- automatic confirmation of Codex findings;
- a required check backed by Codex output;
- concurrent writes by subagents (all four are read-only);
- the ChatGPT Workspace Agent;
- auto-merge, auto-deploy, or automated remediation.

## References

- AGENTS.md: https://developers.openai.com/codex/guides/agents-md
- Subagents: https://developers.openai.com/codex/subagents
- Code review: https://developers.openai.com/codex/integrations/github
- GitHub Action: https://developers.openai.com/codex/github-action
