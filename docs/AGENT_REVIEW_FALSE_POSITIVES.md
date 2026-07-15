# AgentReview False-Positive Signatures

`scripts/aiops-review-false-positives.py` records deterministic false-positive
signatures for confirmed AgentReview findings and can emit separate manual-only
contract update suggestions. It never changes `.aiops/domain-contracts.yaml`,
never changes the final review verdict or quality gate, and never applies a
suggestion automatically.

## CLI Contract

```text
python scripts/aiops-review-false-positives.py \
  --review-telemetry "$RUNNER_TEMP/agent/review-telemetry.json" \
  --quality-gate "$RUNNER_TEMP/agent/review-quality-gate.json" \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --markers "$RUNNER_TEMP/agent/false-positive-markers.json" \
  --output "$RUNNER_TEMP/agent/false-positive-signatures.json" \
  --suggestions-output "$RUNNER_TEMP/agent/suggested-contract-updates.yaml"
```

Required inputs are `review-telemetry.json`, `review-quality-gate.json`, and
`final-review.json`. `chunk-results.json` and `false-positive-markers.json` are
optional inputs: missing or invalid chunk results are reported as limitations,
and missing markers are normal. The JSON output path is required; the YAML
suggestions output path is optional.

The CLI rejects attempts to overwrite an input artifact, write both outputs to
the same path, or write an artifact inside a Git worktree or declared target
repository path. Symlinks are resolved before these checks, and `.git` may be a
file or directory.

## Schemas

The false-positive phase uses these schemas:

```text
agent-review.false-positive-markers.v1
agent-review.false-positive-signatures.v1
agent-review.contract-suggestions.v1
```

Manual marker reasons are limited to:

```text
docs_only_overseverity
missing_source_artifact
test_file_in_other_chunk
contract_obsolete
```

Unknown marker reasons are ignored and recorded as structured limitations.

## Candidate Authority

Candidates come only from `final-review.json.confirmed_findings`. Risks,
rejected findings, downgraded findings, severity, confidence, line, evidence,
impact, chunk identity, and input order do not create or alter a signature.
`chunk-results.json` is used only for provenance.

## Signature Algorithm

The signature basis is:

```text
normalized_title + file_path + contract_id
```

Normalization is deterministic:

- title: Unicode NFKC, trim, collapse whitespace, casefold;
- path: convert `\` to `/`, remove `./` and duplicate separators, and require a
  safe relative path;
- contract ID: trim and casefold, or `null` when absent.

Absolute paths, drive-letter paths, and parent traversal paths do not receive a
signature and produce a limitation without leaking the unsafe path.

The canonical form is hashed as:

```python
canonical = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
signature = "fp:v1:" + sha256(canonical.encode()).hexdigest()
```

## Manual Markers

A marker file is optional and manual:

```json
{
  "schema_id": "agent-review.false-positive-markers.v1",
  "schema_version": 1,
  "source": "manual",
  "markers": [
    {
      "finding_signature": "fp:v1:<sha256>",
      "reason": "docs_only_overseverity",
      "suggested_rule": "Docs findings default to P3 unless deterministic high-impact evidence exists",
      "contract_id": "review.docs-severity"
    }
  ]
}
```

Markers correlate by exact signature. Unmatched markers emit
`manual_marker_unmatched:<signature>` and do not generate suggestions. Duplicate
identical markers are deduplicated. Conflicting marker variants for the same
signature emit a warning and are not silently merged into a candidate.

## Suggested Contract Updates

`suggested-contract-updates.yaml` is separate and human-reviewable. It is only
emitted when `--suggestions-output` is provided. A suggestion is generated only
when a manual marker matches a real candidate and includes a sanitized
`suggested_rule`; rules are never invented from the reason alone.

The YAML always uses manual mode:

```yaml
schema_id: agent-review.contract-suggestions.v1
schema_version: 1
source: aiops-review-false-positives
apply_mode: manual_only
applied: false
target:
  repository: mglpsw/AgentEscala
suggestions: []
limitations: []
```

`yaml.safe_dump(sort_keys=True, allow_unicode=True)` renders the file, and the
result is validated with `yaml.safe_load` before writing.

## Sanitization and Determinism

All output goes through the AgentReview redaction sanitizer plus local path and
sensitive label redaction. Artifacts avoid raw prompts, raw payloads, absolute
paths, credentials, authorization headers, cookies, API keys, provider data,
Router data, and operational CT102 references.

No clocks, UUIDs, random values, hostnames, or temp paths are written to the
artifact content. Candidates are sorted by signature, markers by
signature/reason/rule/contract, suggestions by suggestion ID, and warnings and
limitations lexicographically.
