# CT102 Backup and Rollback — AIOps v0.19.0

> Historical runbook for the completed `v0.19.0` transition. It is preserved
> as release evidence and must not be treated as authorization or as the
> current `v0.20.0` operational procedure.

## 1. Objetivo

Define the minimum backup and rollback coverage required for the persistent
runtime stores of the AIOps Orchestrator on CT102 **before** any `v0.19.0`
transition window is opened.

This document closes a planning gap found by the CT102 read-only audit. It does
**not** execute backup or rollback, and it does not modify the runtime. Its goal
is to record which stores must be preserved, how absent stores must be handled,
and which evidence must be attached to issue #52 before the final `v0.19.0`
release.

## 2. Contexto da auditoria read-only

A read-only audit of CT102 was completed with no mutating actions. Observed
baseline:

- Runtime repo: `/opt/aiops-orchestrator`
- Commit observed: `58129569f622773da609c05090190fe583a76327`
- Version observed: `0.1.0`
- `/health`: OK / 200
- `/ready`: OK / 200
- `/metrics`: OK / 200
- Worktree: clean
- Branch: `master`
- Recommendation: `ready_for_transition_planning`

Postcheck limitations recorded at baseline:

- `expected_version_mismatch` — the runtime reports `0.1.0`, not `0.19.0`. The
  `0.19.0` track is a release line, not the in-code `__version__`. This is
  expected during planning and is resolved only by the controlled transition.
- `minimum_paths_failed` — explained in section 6. Some required paths are
  absent at baseline because they are created on demand or live inside a Docker
  volume, not on the host filesystem.

The audit also reviewed the existing operational scripts:

- `scripts/backup.sh` exists, has no dry-run, and is mutating/remote
  (`pct exec`, `docker`). It backs up the database, `config/`, and `.env`.
- `scripts/rollback.sh` exists, has no dry-run; its `stop` and `restore`
  actions are mutating.
- Neither script clearly covers the JSONL runtime stores `var/audit`,
  `var/approvals`, and `var/runs`.

This PR does not change those scripts. It documents the coverage they must reach
before the transition window and provides a read-only manifest generator
(`scripts/aiops-runtime-backup-manifest.py`) to declare that coverage as
evidence.

## 3. Layout real observado no CT102

| Path | Baseline | Notes |
| --- | --- | --- |
| `/opt/aiops-orchestrator` | present | runtime repo root |
| `config/` | present | action catalog and runtime config |
| `.env` | present | secrets — presence only, never read |
| `var/audit` | present | audit JSONL store |
| `var/approvals` | absent | code default; created on first write |
| `var/runs` | absent | code default; created on first write |
| `data/` (host) | absent | DB lives in the Docker volume, not on host |
| `deploy/docker-compose.yml` | present | containerized deploy definition |

Store paths come from the runtime configuration (`app/core/config.py`):

- audit log: `var/audit/aiops_audit.jsonl`
- approval store: `var/approvals/aiops_approvals.jsonl`
- run store: `var/runs/aiops_runs.jsonl`
- database: `sqlite+aiosqlite:///data/aiops.db`, mounted in the container at
  `/app/data/aiops.db` through the Docker volume `aiops-data:/app/data`.

## 4. Stores persistentes obrigatórios

The following must always be covered by backup and rollback before a transition
window:

- **Database** — `/app/data/aiops.db` inside the container, backed by the Docker
  volume `aiops-data`. Backup must preserve the volume or an equivalent DB file.
- **`config/`** — action catalog and runtime configuration.
- **`.env`** — preserved as a unit **without exposing or printing secrets**.
  Presence/absence only; content is never read by tooling.
- **`var/audit`** — audit JSONL store. Present at baseline; must be preserved.

## 5. Stores opcionais/criados sob demanda

- **`var/approvals`** — absent at baseline; the path is a code default and is
  created on the first write. Before the window, the operator must decide whether
  it will be created explicitly and preserved.
- **`var/runs`** — same policy as `var/approvals`.

These are "optional at baseline" only in the sense that they may not exist yet.
They are **not** discardable: once they exist they hold approval and run history
that must be preserved.

## 6. Política para paths ausentes

- `data/`
  - May be absent on the host repository.
  - In the containerized deploy the DB lives at `/app/data/aiops.db`.
  - The host uses the Docker volume `aiops-data:/app/data`.
  - Backup must preserve that volume or the equivalent DB file. Host-side
    absence is expected and acceptable **as long as** the volume/container path
    is captured.

- `var/audit`
  - Exists on the host.
  - Must be preserved.
  - Must enter backup and rollback.

- `var/approvals`
  - Absent at baseline.
  - Code default; may be created on demand on the first write.
  - Before the window, decide whether it will be created explicitly and
    preserved.
  - If it does not exist, backup must record the absence, and rollback must
    preserve that absent state — or restore the directory if it was created
    during the transition.

- `var/runs`
  - Same policy as `var/approvals`.

### Política obrigatória

- If `var/approvals` or `var/runs` are created during the transition, rollback
  must remove them or restore them according to the original snapshot.
- If they already exist before the transition, backup and rollback must preserve
  their content.
- Never assume a JSONL store can be discarded.
- The final `v0.19.0` release requires this preservation policy to be recorded
  in issue #52.

### Sobre `minimum_backup_complete=false`

On the current CT102 baseline, `scripts/aiops-runtime-backup-manifest.py` will
report `minimum_backup_complete=false` because `var/approvals` and `var/runs` are
absent. **This is expected.** It does **not** block planning, but it **does**
block opening the transition window until the preservation/rollback policy for
those stores is registered in issue #52.

The manifest only declares required coverage and observed presence/absence. It
never asserts that a backup exists or is reliable — that requires real
post-execution evidence captured during the controlled window.

## 7. Backup mínimo exigido antes da transição

Before the window, backup must cover, at minimum:

- [ ] Database: Docker volume `aiops-data` **or** `/app/data/aiops.db`.
- [ ] `config/`.
- [ ] `.env` (as a unit, without exposing secrets).
- [ ] `var/audit`.
- [ ] `var/approvals` — content if present; recorded absence if missing.
- [ ] `var/runs` — content if present; recorded absence if missing.
- [ ] A backup/snapshot identifier and timestamp.
- [ ] The current runtime branch, commit, or tag.

## 8. Rollback mínimo exigido antes da transição

Before the window, rollback must be able to:

- [ ] Restore the database from the preserved volume / DB file.
- [ ] Restore `config/`.
- [ ] Restore `.env` without exposing secrets.
- [ ] Restore `var/audit`.
- [ ] For `var/approvals` / `var/runs`: restore content if they existed before,
      or **remove** them if they were created during the transition (preserving
      the original absent state).
- [ ] Identify the rollback commit or tag.
- [ ] Name the checks that prove rollback succeeded (`/health`, `/ready`,
      `/metrics`, action catalog, store observability).

## 9. Evidências que devem ser anexadas na issue #52

- CT102 store layout (output of `scripts/aiops-runtime-backup-manifest.py`).
- Backup/snapshot identifier and timestamp.
- Database / volume preservation evidence.
- `config/` and `.env` preservation evidence (no secret values).
- `var/audit`, `var/approvals`, `var/runs` preservation/absence evidence.
- Rollback commit or tag.
- The chosen preservation policy for `var/approvals` and `var/runs`.
- Operator notes for any limitation.

## 10. Critérios de bloqueio

Do **not** open the transition window if any of the following is true:

- Database / volume `aiops-data` backup cannot be confirmed.
- `config/` backup cannot be confirmed.
- `.env` cannot be preserved (or would require exposing secrets to do so).
- `var/audit` backup cannot be confirmed.
- The preservation/rollback policy for `var/approvals` / `var/runs` is not
  registered in issue #52.
- A rollback commit or tag cannot be identified.
- `minimum_backup_complete` is `false` for a reason other than the expected
  baseline absence of `var/approvals` / `var/runs` (e.g. `config`, `audit`, or
  `docker-compose.yml` missing).

## 11. Critérios de liberação para janela

The transition window may open only when all of the following hold:

- Backup covers DB/volume, `config/`, `.env`, `var/audit`.
- The `var/approvals` / `var/runs` preservation policy is registered in #52.
- Rollback covers the same scope and can restore or remove on-demand stores
  per the original snapshot.
- A rollback commit or tag is identified.
- A manifest (`scripts/aiops-runtime-backup-manifest.py` output) is attached to
  #52 describing the baseline layout.

## 12. Não escopo

- No backup execution.
- No rollback execution.
- No deploy.
- No restart.
- No service manager (`systemctl`) operation.
- No Docker operation.
- No SSH or remote command.
- No CT102 call from this PR.
- No provider or Agent Router call.
- No `.env` content read.
- No secret change.
- No FastAPI runtime route change.
- No change to `scripts/backup.sh` or `scripts/rollback.sh`.
- No automation of backup/rollback.
- No tag, release, or `v0.19.0` final from this PR.

## 13. Checklist pré-janela

- [ ] CT102 host and repo path confirmed by the operator.
- [ ] Baseline manifest generated and attached to #52.
- [ ] Database / volume `aiops-data` backup confirmed.
- [ ] `config/` backup confirmed.
- [ ] `.env` preserved without exposing secrets.
- [ ] `var/audit` backup confirmed.
- [ ] `var/approvals` / `var/runs` preservation policy registered in #52.
- [ ] Rollback commit/tag identified.
- [ ] Rollback procedure for on-demand stores defined (restore vs remove).
- [ ] Change window scheduled with operator and reviewer.

## 14. Checklist pós-rollback

- [ ] `/health` OK.
- [ ] `/ready` OK.
- [ ] `/metrics` OK.
- [ ] Action catalog loaded.
- [ ] Database restored and consistent.
- [ ] `config/` restored.
- [ ] `.env` restored without exposing secrets.
- [ ] `var/audit` restored.
- [ ] `var/approvals` restored to original state (content or original absence).
- [ ] `var/runs` restored to original state (content or original absence).
- [ ] Runtime version matches the rollback target.
- [ ] Evidence attached to #52.

## 15. Relação com `v0.19.0` final

The final `v0.19.0` release was created after the following gate was accepted:

- The CT102 runtime transition is completed in a controlled window.
- Backup/snapshot covers the database/volume, `config/`, `.env`, and the JSONL
  runtime stores (`var/audit`, `var/approvals`, `var/runs`).
- Rollback covers the same scope, including the remove-or-restore policy for
  on-demand stores.
- The baseline absence of `var/approvals` and `var/runs` is documented or
  resolved.
- The CT102 store layout (manifest) and the backup/rollback evidence are
  attached to issue #52.

See also `docs/CT102_RUNTIME_TRANSITION_V019.md` and `docs/RELEASE_V0_19_0.md`.
AgentReview remains a CT104 toolrepo workflow; CT102 remains the production
runtime and must not run AgentReview tooling.
