# Release v0.19.0

## 1. Objetivo

Historical release record for `v0.19.0`, finalized on 2 June 2026 after the
AgentReview E2E path and CT102 runtime transition were validated with evidence.

## 2. Estado final

- `v0.19.0-rc.1` marked AgentReview Engine offline E2E.
- `v0.19.0-rc.2` marked AgentEscala thin-wrapper E2E validated on CT104.
- CT104 remains the development and AgentReview toolrepo path.
- CT102 remains the production AIOps runtime.
- Runtime internal version default aligned to `0.19.0` (`app/__init__.py`,
  `app/core/config.py`) so `/health` reports the release version after the
  controlled CT102 runtime transition. No runtime behavior changes beyond the
  reported version.
- Final signed tag `v0.19.0` targets
  `9c90eac6205782a17a1567737aef026728f88089`.
- The GitHub release is final and issue #52 is closed as completed.
- `v0.19.0` is the rollback release for `v0.20.0`.

## 3. RCs

- `v0.19.0-rc.1`: AgentReview Engine offline E2E in AIOps.
- `v0.19.0-rc.2`: AgentEscala thin-wrapper E2E validated on CT104.
- `v0.19.0`: finalized after CT102 runtime transition evidence was reviewed and
  accepted.

## 4. Escopo do release

- AgentReview Engine offline path in AIOps.
- AgentEscala thin-wrapper E2E integration through the CT104 review path.
- CT104 as the canonical review/toolrepo environment.
- CT102 runtime boundaries documented and validated before final release.
- No direct provider execution from AIOps AgentReview tooling.
- No deploy or remediation automation in this release.

## 5. Fora do escopo

- Definitive quality gate.
- Telemetry rollout.
- Second opinion service.
- Complete Validation Evidence semantic pre-review.
- Transition of other services.
- Direct provider automation.
- Runtime deploy automation.
- Any publication before CT102 evidence was complete.

## 6. Evidência exigida para `v0.19.0` final

Final `v0.19.0` was created after the following gates were accepted:

- CT102 runtime transition is completed in a controlled window.
- `/health` is OK.
- `/ready` is OK.
- `/metrics` is OK.
- Action catalog is loaded.
- Database is OK.
- Provider registry is OK or in the expected documented state.
- Audit log remains writable.
- Approval store is preserved.
- Run store is preserved.
- Rollback is documented and still possible.
- Backup/snapshot covers the database/config/env **and** the JSONL runtime stores
  (`var/audit`, `var/approvals`, `var/runs`).
- Rollback covers the database/config/env **and** the JSONL runtime stores,
  including the remove-or-restore policy for on-demand stores.
- The baseline absence of `var/approvals` and `var/runs` is documented or resolved.
- The CT102 store layout (backup manifest) is attached to issue #52.
- Evidence is attached to issue #52.

See `docs/CT102_BACKUP_ROLLBACK_V019.md` for the store-level backup/rollback
coverage requirements.

## 7. Checklist final concluído

- [x] `v0.19.0-rc.1` evidence reviewed
- [x] `v0.19.0-rc.2` evidence reviewed
- [x] CT102 pre-transition inventory attached to issue #52
- [x] CT102 backup or snapshot evidence attached to issue #52
- [x] Backup/snapshot covers DB/config/env and runtime stores JSONL
- [x] Rollback covers DB/config/env and runtime stores JSONL
- [x] Baseline absence of `var/approvals` and `var/runs` documented or resolved
- [x] CT102 store layout (backup manifest) attached to issue #52
- [x] CT102 rollback commit/tag documented
- [x] CT102 postcheck attached to issue #52
- [x] Health OK
- [x] Readiness OK
- [x] Metrics OK
- [x] Action catalog OK
- [x] Audit/run/approval stores OK
- [x] No AgentReview tooling moved to CT102
- [x] Final release notes reviewed
- [x] Final tag/release approved separately

## 8. Final release notes

### Added

- AgentReview Engine offline flow in AIOps.
- AgentEscala thin-wrapper E2E review path.
- CT104 as the canonical review path for AgentReview tooling.
- CT102 runtime boundary documentation for the final release transition.

### Preserved boundaries

- No direct provider calls from AIOps AgentReview tooling.
- No `/v1/chat/ingest`.
- No deploy automation.
- No remediation automation.
- No AgentReview tooling on CT102.

### Subsequent work

- Deterministic quality gate, telemetry and bounded chunk payload contracts were
  delivered later in `v0.20.0`.
- Additional semantic validation evidence and an optional second-opinion path
  remain separately scoped follow-ups.
