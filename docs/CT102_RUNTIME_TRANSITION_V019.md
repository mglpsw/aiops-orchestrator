# CT102 Runtime Transition - AIOps v0.19.0

## 1. Objetivo

Prepare the controlled CT102 runtime transition required before the final
`v0.19.0` release. This document defines the evidence, checks, safety limits,
rollback expectations, and issue #52 attachments needed before any production
runtime change is executed.

This PR does not execute the transition.

## 2. Escopo

- Prepare a CT102 runtime transition runbook.
- Define read-only inventory and postcheck evidence.
- Keep AgentReview execution on CT104.
- Keep CT102 limited to the operational AIOps runtime.
- Gate the final `v0.19.0` release on validated CT102 runtime evidence.

## 3. Nao escopo

- No deploy.
- No restart.
- No service manager operation.
- No Docker operation.
- No SSH or remote command.
- No CT102 call from this PR.
- No provider or Agent Router call.
- No secret change.
- No FastAPI runtime route change.
- No action catalog behavior change.
- No version bump to `0.19.0`.
- No final tag or release.
- No AgentReview tooling on CT102.

## 4. Pre-condicoes

- The transition PR is merged and reviewed.
- CT102 host identity is confirmed by the operator.
- CT102 repo path is confirmed by the operator.
- Current CT102 branch, commit, or tag is captured.
- Current CT102 runtime health, readiness, metrics, action catalog, database,
  provider registry, audit log path, approval store path, and run store path are
  captured through read-only checks.
- Rollback commit or tag is identified before any change window begins.
- Backup or snapshot evidence exists before any change window begins.

Minimum pre-transition checklist:

- [ ] Confirmar host CT102
- [ ] Confirmar path do repo runtime
- [ ] Confirmar branch/commit/tag atual
- [ ] Confirmar health atual
- [ ] Confirmar readiness atual
- [ ] Confirmar metrics atual
- [ ] Confirmar action catalog atual
- [ ] Confirmar database atual
- [ ] Confirmar provider registry atual
- [ ] Confirmar audit log path
- [ ] Confirmar approval store path
- [ ] Confirmar run store path
- [ ] Confirmar rollback commit/tag
- [ ] Confirmar backup/snapshot antes de qualquer alteracao

## 5. Artefatos de release

- `v0.19.0-rc.1`: AgentReview Engine offline E2E.
- `v0.19.0-rc.2`: AgentEscala thin-wrapper E2E validated on CT104.
- `v0.19.0` final: allowed only after CT102 runtime transition is completed,
  validated, rollback remains documented, and evidence is attached to issue #52.

## 6. Inventario read-only

The first CT102 activity after this PR is merged is inventory only. The
inventory script must be executed locally on the runtime host by the operator
and must write evidence outside the repository path.

Example shape:

```text
python scripts/aiops-runtime-inventory.py --repo-root /path/to/aiops-orchestrator --output /tmp/aiops-runtime-inventory.json
```

Optional local HTTP checks may be supplied for health, readiness, and metrics.
Use `http://127.0.0.1:<port>` for real CT102 execution. `http://0.0.0.0...` is
accepted by the tooling only as local-only compatibility because `0.0.0.0` is a
bind address, not the preferred client destination.

The inventory must not read `.env`, dump environment variables, read persistent
store contents, call external network destinations, call providers, call Agent
Router, run subprocesses, run Docker, run service manager commands, or modify
repo files.

## 7. Backup/snapshot

Before any transition window, the operator must confirm a backup or snapshot
that can restore the current CT102 runtime state. Evidence should include:

- Snapshot or backup identifier.
- Timestamp.
- Current runtime branch, commit, or tag.
- Rollback commit or tag.
- Confirmation that audit, approval, and run stores are preserved.

## 8. Plano de rollback

Rollback must be documented before transition execution. The rollback plan must
state:

- Which commit or tag restores the previous runtime.
- Which runtime stores must be preserved.
- Which checks prove the rollback succeeded.
- Who owns the rollback decision during the change window.

Rollback is mandatory if abort criteria are met and the operator cannot restore
health, readiness, metrics, action catalog, and persistent store observability
within the approved window.

## 9. Janela de mudanca

The transition must happen only in an approved operational window. The window
must define:

- Start and end time.
- Operator and reviewer.
- Expected runtime commit or tag.
- Rollback commit or tag.
- Communication channel.
- Evidence destination in issue #52.

## 10. Passos de transicao controlada

1. Confirm CT102 host and repo path.
2. Run read-only inventory locally on CT102.
3. Review inventory evidence before any runtime change.
4. Confirm backup or snapshot.
5. Confirm rollback commit or tag.
6. Enter the approved change window.
7. Apply the runtime transition using the separately approved operational
   procedure.
8. Run postcheck locally on CT102.
9. Attach evidence to issue #52.
10. Decide whether final `v0.19.0` release criteria are satisfied.

These steps are intentionally procedural. This PR does not include deploy
automation or operational command execution.

## 11. Pos-check

The postcheck must prove that the runtime is healthy after transition and that
the minimum runtime boundaries remain intact.

Post-transition checklist:

- [ ] `/health` OK
- [ ] `/ready` OK
- [ ] `/metrics` OK
- [ ] action catalog loaded
- [ ] database OK
- [ ] providers OK ou estado esperado
- [ ] audit log gravavel
- [ ] approval store preservado
- [ ] run store preservado
- [ ] legacy endpoints deprecados continuam observaveis
- [ ] versao reportada correta
- [ ] logs sem erro critico
- [ ] rollback ainda possivel

The postcheck tooling must keep `ready_for_final_release=false` if health or
readiness are skipped. Metrics must also be executed and OK for final release
readiness.

## 12. Criterios de sucesso

- Runtime CT102 updated in the controlled window.
- `/health` OK.
- `/ready` OK.
- `/metrics` OK.
- Action catalog loaded.
- Database OK.
- Provider registry OK or in the expected documented state.
- Audit log writable.
- Approval store preserved.
- Run store preserved.
- Rollback still possible.
- Evidence attached to issue #52.

## 13. Criterios de abort/rollback

Abort or rollback if any of the following happens:

- CT102 host or repo path cannot be confirmed.
- Backup or snapshot cannot be confirmed.
- Rollback commit or tag cannot be confirmed.
- Health fails after transition.
- Readiness fails after transition.
- Metrics fail after transition.
- Action catalog fails to load.
- Audit, approval, or run stores are missing or unexpectedly changed.
- Provider registry is not in the expected state.
- Critical errors appear in runtime logs.
- Evidence cannot be captured.

## 14. Evidencias a anexar na issue #52

- Pre-transition inventory JSON.
- Current CT102 branch, commit, or tag.
- Backup or snapshot identifier.
- Rollback commit or tag.
- Post-transition postcheck JSON.
- Health, readiness, and metrics evidence.
- Action catalog evidence.
- Audit, approval, and run store preservation evidence.
- Operator notes for any limitation or expected provider state.

## 15. Relacao com `v0.19.0-rc.1`, `v0.19.0-rc.2` e `v0.19.0` final

`v0.19.0-rc.1` marked the AgentReview Engine offline E2E foundation.
`v0.19.0-rc.2` marked AgentEscala thin-wrapper E2E validation on CT104.

The final `v0.19.0` release can be created only after:

- CT102 runtime is updated.
- Health is OK.
- Readiness is OK.
- Metrics are OK.
- Action catalog is OK.
- Audit, run, and approval stores are OK.
- Rollback is documented.
- Evidence is registered in issue #52.

AgentReview remains a CT104 toolrepo workflow. CT102 remains the production
runtime and must not run AgentReview tooling.
