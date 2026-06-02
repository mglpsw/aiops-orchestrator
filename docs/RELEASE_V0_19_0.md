# Release v0.19.0

## 1. Objetivo

Release `v0.19.0` after the AgentReview E2E path and the CT102 runtime
transition are validated with evidence.

## 2. Estado atual

- `v0.19.0-rc.1` marked AgentReview Engine offline E2E.
- `v0.19.0-rc.2` marked AgentEscala thin-wrapper E2E validated on CT104.
- CT104 remains the development and AgentReview toolrepo path.
- CT102 remains the production AIOps runtime.
- Final `v0.19.0` is not created yet.

## 3. RCs

- `v0.19.0-rc.1`: AgentReview Engine offline E2E in AIOps.
- `v0.19.0-rc.2`: AgentEscala thin-wrapper E2E validated on CT104.
- `v0.19.0`: allowed only after CT102 runtime transition evidence is reviewed
  and accepted.

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
- Final `v0.19.0` before CT102 evidence is complete.

## 6. Criterios para `v0.19.0` final

Final `v0.19.0` can be created only after:

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
- Evidence is attached to issue #52.

## 7. Checklist final

- [ ] `v0.19.0-rc.1` evidence reviewed
- [ ] `v0.19.0-rc.2` evidence reviewed
- [ ] CT102 pre-transition inventory attached to issue #52
- [ ] CT102 backup or snapshot evidence attached to issue #52
- [ ] CT102 rollback commit/tag documented
- [ ] CT102 postcheck attached to issue #52
- [ ] Health OK
- [ ] Readiness OK
- [ ] Metrics OK
- [ ] Action catalog OK
- [ ] Audit/run/approval stores OK
- [ ] No AgentReview tooling moved to CT102
- [ ] Final release notes reviewed
- [ ] Final tag/release approved separately

## 8. Release notes draft

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

### Future work

- Quality gate preparation.
- Telemetry preparation.
- Additional semantic validation evidence.
- Second opinion review path.
