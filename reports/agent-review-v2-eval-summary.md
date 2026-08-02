# AgentReview v2 benchmark summary (Lane 1 -- AIOps deterministic/offline)

- total cases: 9
- readiness matches: 9/9
- false approvals (critical KPI): 0
- stale cases correctly rejected: 1/1
- expected findings recovered: 2/2
- forbidden findings leaked: 0
- duplicate finding_ids detected: 0
- total duration: 86.7 ms

## Recall by severity

| severity | recovered/total |
|---|---|
| P0 | 0/0 |
| P1 | 1/1 |
| P2 | 1/1 |
| P3 | 0/0 |

| case_id | category | expected | actual | match | findings | forbidden leaked |
|---|---|---|---|---|---|---|
| agentescala-contract-p3-finding-still-ready | contract | ready | ready | yes | 0/0 | 0 |
| agentescala-domain-new-finding-manual-required | domain | manual_required | manual_required | yes | 1/1 | 0 |
| agentescala-false-positive-unrelated-change | false-positive | ready | ready | yes | 0/0 | 0 |
| agentescala-security-confirmed-finding-blocked-code | security | blocked_code | blocked_code | yes | 1/1 | 0 |
| aiops-contract-missing-must-review | coverage | blocked_pipeline | blocked_pipeline | yes | 0/0 | 0 |
| aiops-contract-stale-head | stale | stale | stale | yes | 0/0 | 0 |
| interleitos-domain-dlp-suspicion-manual-required | domain | manual_required | manual_required | yes | 0/0 | 0 |
| interleitos-false-positive-synthetic-clinical-terms | false-positive | ready | ready | yes | 0/0 | 0 |
| interleitos-security-blocked-prior-to-response | security | blocked_pipeline | blocked_pipeline | yes | 0/0 | 0 |
