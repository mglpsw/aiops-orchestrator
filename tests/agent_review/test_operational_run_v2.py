"""`#200-D`: the operational composition seam.

Before this module existed, nothing in ``app/`` or ``scripts/`` could take a
real repository checkout plus run authorities and reach a
``ReviewReadinessV2``. ``run_synthetic_review_v2`` already owned the whole
back half, but its three inputs -- ``content``, ``manifest`` and
``payload_by_chunk_id`` -- had to be hand-wired by every caller, and the only
place that wiring existed was inside tests.

These tests exercise the REAL front half: a real temporary git repository with
real commits, real ``git diff`` acquisition, real manifest assembly, real
profile-derived payloads and real redacted extraction. Only the network seam
(``_open_agent_router_request_v2``) is mocked; every authority between the
checkout and the readiness artifact runs unpatched.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.contracts_v2 import (
    PullRequestStateV2,
    ReadinessStateV2,
    RunOriginV2,
    SemanticGroupV2,
    TargetProfileV2,
)
from app.agent_review.operational_run_v2 import (
    OperationalRunError,
    run_operational_review_v2,
)
from app.agent_review.review_transport_v2 import agent_router_transport_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

_ROUTER_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "router_receipt_v2"

# A string that must never survive redaction into the Router request.
_SECRET_IN_DIFF = "ghp_LIVE0SECRET0TOKEN0THAT0MUST0NOT0LEAK0abcd"


# -- fixture helpers (file-local, matching this codebase's per-file convention) --


def _profile_yaml() -> str:
    return """schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/repo
  default_branch: main
artifacts: []
budgets:
  max_chunks: 32
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths:
    - app.py
  patterns: []
  artifact_ids: []
  minimum_coverage: complete
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks:
    - pytest
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _write_profile_root(root: Path) -> Path:
    aiops = root / ".aiops"
    aiops.mkdir(parents=True, exist_ok=True)
    (aiops / "target-profile.v2.yaml").write_text(_profile_yaml(), encoding="utf-8")
    (aiops / "authoritative-checks.v2.yaml").write_text(
        """schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: example/repo
authoritative_checks:
  - check_name: pytest
    workflow_path: .github/workflows/authoritative-checks.yml
    job_name: authoritative pytest
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: example/repo
      path: .github/workflows/authoritative-checks.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    producer_workflow_ref: refs/heads/main
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
""",
        encoding="utf-8",
    )
    return root


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all",
        semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
        path_patterns=["*"],
        contract_ids=[],
        artifact_ids=[],
        priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [rule],
        "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json")]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _build_real_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A real git repository with two real commits and a real changed hunk."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    _write_profile_root(repo)

    (repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    # the HEAD commit introduces a secret-looking literal on purpose
    (repo / "app.py").write_text(
        f'a = 1\nb = 2\nc = 3\ntoken = "{_SECRET_IN_DIFF}"\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "head")
    head_sha = _git(repo, "rev-parse", "HEAD")
    return repo, base_sha, head_sha


def _empty_authority(profile_root: Path) -> dict:
    """`#201-C0` Class A: an honestly unestablished required-check
    submission, verified against the real unpatched boundary."""

    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import (
        TOOLCHAIN_DIGEST,
        _snapshot_dict,
    )

    return {
        "origin": RunOriginV2(
            event_type="pull_request", event_action="synchronize", delivery_id="delivery-1"
        ),
        "snapshot": parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([]))),
        "toolchain_digest": TOOLCHAIN_DIGEST,
        "pr_state": PullRequestStateV2.OPEN,
    }


def _router_result_document(payload) -> dict:
    return {
        "schema_id": "agent-review.chunk-response.v2",
        "schema_version": 2,
        "summary": "operational router review complete",
        "findings": [],
        "coverage": payload.coverage.model_dump(mode="json"),
        "limitations": [],
    }


def _mocked_router(captured: list, *, result_mutator=None):
    """Mock ONLY the network acquisition seam. Everything above it --
    transport construction, receipt verification, binding, parsing,
    synthesis, readiness -- runs unpatched."""

    class _Resp:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._raw

    def _fake_urlopen(http_request, timeout):
        body = json.loads(http_request.data.decode("utf-8"))
        captured.append(body)
        # the payload the runner actually built is not visible here, so the
        # result document is rebuilt from the request's own declared coverage
        result = json.loads(_CAPTURED_RESULT_DOCUMENT[0])
        if result_mutator is not None:
            result_mutator(result)
        assistant = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        messages_bytes = json.dumps(
            body["messages"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        receipt = json.loads(
            (_ROUTER_FIXTURE_ROOT / "local-success-f2a.json").read_text(encoding="utf-8")
        )
        receipt["requested"]["model"] = body["model"]
        receipt["received_input"]["sha256"] = hashlib.sha256(messages_bytes).hexdigest()
        receipt["returned_output"]["sha256"] = hashlib.sha256(
            assistant.encode("utf-8")
        ).hexdigest()
        receipt["caller_declared_metadata"] = dict(body["metadata"])
        return _Resp(
            json.dumps(
                {
                    "id": "chatcmpl-operational",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "resolved-model-is-not-a-domain-identity",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": assistant},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "request_id": "router-public-request",
                    "inference_receipt": receipt,
                }
            ).encode("utf-8")
        )

    return _fake_urlopen


_CAPTURED_RESULT_DOCUMENT: list[str] = [""]


def _run(tmp_path: Path, *, captured: list, overrides: dict | None = None):
    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    authority = _empty_authority(repo)
    kwargs = dict(
        repo_root=repo,
        target_profile_root=repo,
        grouping_policy=_grouping_policy(),
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=head_sha,
        pr_number=1,
        toolrepo_sha="b" * 40,
        evidence_hash="c" * 64,
        transport=agent_router_transport_v2(
            base_url="https://router.example/", api_key="secret-token", model="review:code"
        ),
        max_lines_per_chunk=1000,
        **authority,
    )
    if overrides:
        kwargs.update(overrides)

    # the result document must describe the payload the runner itself built
    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        **{k: v for k, v in kwargs.items()
           if k in {"repo_root", "target_profile_root", "grouping_policy", "base_sha",
                    "head_sha", "tested_merge_sha", "pr_number", "toolrepo_sha",
                    "evidence_hash", "max_lines_per_chunk"}}
    )
    first_payload = prepared.payload_by_chunk_id[prepared.content.chunks[0].chunk_id]
    _CAPTURED_RESULT_DOCUMENT[0] = json.dumps(_router_result_document(first_payload))

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_mocked_router(captured),
    ):
        return run_operational_review_v2(**kwargs), repo, base_sha, head_sha


# -- the positive provider-free composition proof ---------------------------


def test_operational_runner_reaches_readiness_from_a_real_checkout(tmp_path: Path) -> None:
    """`#200-D` core proposition: real repo -> real diff -> real content ->
    Router-format exchange -> receipt v2 -> bound -> parsed -> synthesized ->
    ReviewReadinessV2, with no step hand-wired by this test."""

    captured: list = []
    outcome, _, _, head_sha = _run(tmp_path, captured=captured)

    # the front half really ran
    assert outcome.prepared.manifest.identity.head_sha == head_sha
    assert outcome.prepared.content.chunks, "extraction produced no chunks"
    assert set(outcome.prepared.payload_by_chunk_id) == {
        chunk.chunk_id for chunk in outcome.prepared.manifest.chunks
    }

    # the Router really was addressed in OpenAI shape
    assert len(captured) == 1
    assert [m["role"] for m in captured[0]["messages"]] == ["system", "user"]
    assert captured[0]["response_format"] == {"type": "json_object"}

    # the back half really bound and parsed
    assert [o.state for o in outcome.review.chunk_outcomes] == ["bound"]
    assert outcome.review.chunk_outcomes[0].result is not None


def test_semantic_success_without_trusted_checks_is_never_ready(tmp_path: Path) -> None:
    """Shadow-minimal: a semantic result is available, yet readiness degrades
    honestly because no trusted check authority was established."""

    captured: list = []
    outcome, _, _, _ = _run(tmp_path, captured=captured)

    assert outcome.review.chunk_outcomes[0].result is not None      # semantics present
    assert outcome.review.readiness.state is not ReadinessStateV2.READY


# -- security: redaction happens BEFORE the Router is addressed -------------


def test_secret_in_the_real_diff_never_reaches_the_router(tmp_path: Path) -> None:
    """The HEAD commit contains a token-shaped literal. Redaction runs inside
    ``extract_review_content_v2``, i.e. strictly before any transport exists
    to be called -- so the proof is the ACTUAL outgoing request bytes, not
    the sanitized readiness artifact at the far end."""

    captured: list = []
    outcome, _, _, _ = _run(tmp_path, captured=captured)

    assert len(captured) == 1
    outgoing = json.dumps(captured[0], ensure_ascii=False)
    assert _SECRET_IN_DIFF not in outgoing

    # Non-vacuity: the secret is absent because it was REDACTED, not because
    # the changed line never reached the request. The line is carried, with
    # the token replaced -- otherwise this test would keep passing even if
    # extraction silently stopped emitting content at all.
    assert "app.py" in outgoing
    assert "[REDACTED]" in outgoing
    assert "token" in outgoing
    assert outcome.prepared.content.chunks[0].fragments[0].content.count("[REDACTED]") == 1


def test_operational_outcome_does_not_carry_transport_material(tmp_path: Path) -> None:
    """The readiness artifact must not become a side channel for the request,
    the assistant content, the raw HTTP body or the credential."""

    captured: list = []
    outcome, repo, _, _ = _run(tmp_path, captured=captured)

    emitted = outcome.review.readiness.model_dump_json()
    for forbidden in (
        _SECRET_IN_DIFF,
        "secret-token",            # the Router API key used by the transport
        "Bearer ",
        "router.example",          # the private Router base URL
        "chatcmpl-operational",    # the raw Router response id
        str(repo),                 # an absolute local path
        "Traceback",
    ):
        assert forbidden not in emitted, f"readiness leaked {forbidden!r}"


# -- negative ordering: an earlier authority prevents every later stage -----


def _count_http_calls(tmp_path: Path, overrides: dict) -> tuple[int, str]:
    """Run with a transport whose network seam counts invocations."""

    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    authority = _empty_authority(repo)
    calls: list = []
    kwargs = dict(
        repo_root=repo,
        target_profile_root=repo,
        grouping_policy=_grouping_policy(),
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=head_sha,
        pr_number=1,
        toolrepo_sha="b" * 40,
        evidence_hash="c" * 64,
        transport=agent_router_transport_v2(
            base_url="https://router.example/", api_key="secret-token", model="review:code"
        ),
        max_lines_per_chunk=1000,
        **authority,
    )
    kwargs.update(overrides)

    def _counting(http_request, timeout):
        calls.append(http_request)
        raise AssertionError("transport must be unreachable in this test")

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_counting,
    ):
        with pytest.raises(OperationalRunError) as excinfo:
            run_operational_review_v2(**kwargs)
    return len(calls), excinfo.value.reason_code


def test_invalid_profile_stops_before_diff_and_router(tmp_path: Path) -> None:
    """A profile that cannot be loaded must stop the run at stage 1."""

    empty_root = tmp_path / "no_profile"
    (empty_root / ".aiops").mkdir(parents=True)
    calls, reason = _count_http_calls(tmp_path, {"target_profile_root": empty_root})
    assert calls == 0
    assert reason  # the profile loader's own reason code, not a synonym


def test_unusable_grouping_policy_stops_before_diff_and_router(tmp_path: Path) -> None:
    """A policy naming a group the profile does not allow is refused before
    any diff is acquired."""

    rule = SemanticGroupingRuleV2(
        rule_id="all",
        semantic_group=SemanticGroupV2.FRONTEND_UI,   # not in allowed_semantic_groups
        path_patterns=["*"],
        contract_ids=[],
        artifact_ids=[],
        priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [rule],
        "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json")]}
    )
    policy = SemanticGroupingPolicyV2(**material, policy_sha256=digest)

    calls, reason = _count_http_calls(tmp_path, {"grouping_policy": policy})
    assert calls == 0
    assert reason == "semantic_grouping_unknown_group"


@pytest.mark.parametrize("field", ["base_sha", "head_sha"])
def test_unresolvable_commit_stops_before_router(tmp_path: Path, field: str) -> None:
    """Diff acquisition against a non-existent commit cannot reach transport."""

    calls, reason = _count_http_calls(tmp_path, {field: "0" * 40})
    assert calls == 0
    assert reason


def test_preparation_closure_precedes_any_transport(tmp_path: Path) -> None:
    """`#200-D`'s own gate: if content and payload chunk sets disagree, the
    run refuses with a typed reason -- never a raw ``KeyError`` from
    ``payload_by_chunk_id[...]`` inside the back half."""

    from app.agent_review import operational_run_v2 as module

    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    authority = _empty_authority(repo)
    prepared = module.prepare_operational_review_v2(
        repo_root=repo, target_profile_root=repo, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
    )
    # drop one payload, leaving manifest/content describing a chunk it lacks
    starved = dict(prepared.payload_by_chunk_id)
    starved.pop(next(iter(starved)))

    with pytest.raises(OperationalRunError) as excinfo:
        module._establish_preparation_closure_v2(
            manifest=prepared.manifest,
            payload_by_chunk_id=starved,
            content=prepared.content,
        )
    assert excinfo.value.reason_code == module.PREPARATION_CHUNK_SET_MISMATCH_REASON_V2


# -- cross-object identity mutations fail at the EARLIEST authority ---------


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"toolrepo_sha": "z" * 40}, id="toolrepo_sha_invalid"),
        pytest.param({"evidence_hash": "z" * 64}, id="evidence_hash_invalid"),
        pytest.param({"pr_number": -1}, id="pr_number_invalid"),
    ],
)
def test_contract_invalid_run_identity_is_a_typed_refusal(
    tmp_path: Path, override: dict
) -> None:
    """`RunIdentityV2` is built inside assembly, so contract-invalid identity
    material raises pydantic's ``ValidationError``, not ``RunAssemblyError``.

    It must still leave as a typed operational refusal with zero Router
    calls -- a raw traceback must never be what tells a caller their identity
    material was malformed.
    """

    from app.agent_review.operational_run_v2 import RUN_IDENTITY_INVALID_REASON_V2

    calls, reason = _count_http_calls(tmp_path, override)
    assert calls == 0
    assert reason == RUN_IDENTITY_INVALID_REASON_V2


def test_malformed_base_sha_is_refused_by_diff_acquisition_first(
    tmp_path: Path,
) -> None:
    """Earliest-authority precedence, proved by discrimination.

    ``base_sha`` is consumed by diff acquisition BEFORE identity is ever
    constructed, so a malformed ref is ``invalid_git_ref`` -- not this
    module's identity code. If assembly were reached first, the reason would
    change, and that would mean the stage order had silently moved.
    """

    calls, reason = _count_http_calls(tmp_path, {"base_sha": "not-a-sha"})
    assert calls == 0
    assert reason == "invalid_git_ref"


def test_tested_merge_sha_is_a_caller_declaration_not_a_proven_fact(
    tmp_path: Path,
) -> None:
    """Recorded boundary, not a defect.

    ``tested_merge_sha`` is well-formed-but-unresolvable here and the run
    proceeds: nothing in this composition independently proves that the
    declared tested tree exists. It is a CallerDeclared fact carried into
    identity, exactly as the authority model says. Proving it belongs to the
    live-canary grant, alongside independent head observation.
    """

    captured: list = []
    outcome, _, _, _ = _run(tmp_path, captured=captured, overrides={"tested_merge_sha": "0" * 40})
    assert outcome.prepared.manifest.identity.tested_merge_sha == "0" * 40


def test_content_payload_hash_mutation_is_refused_by_the_closure(tmp_path: Path) -> None:
    """A content chunk whose ``payload_sha256`` disagrees with its payload is
    refused with the EXISTING content/payload reason code, at the closure --
    not by the per-chunk prebind inside the back half, and not by transport."""

    from app.agent_review import operational_run_v2 as module
    from app.agent_review.review_content_v2 import (
        CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2,
    )

    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    prepared = module.prepare_operational_review_v2(
        repo_root=repo, target_profile_root=repo, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
    )
    chunk_id = prepared.content.chunks[0].chunk_id
    tampered = dict(prepared.payload_by_chunk_id)
    tampered[chunk_id] = tampered[chunk_id].model_copy(update={"payload_sha256": "d" * 64})

    with pytest.raises(OperationalRunError) as excinfo:
        module._establish_preparation_closure_v2(
            manifest=prepared.manifest,
            payload_by_chunk_id=tampered,
            content=prepared.content,
        )
    assert excinfo.value.reason_code == CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2


def test_one_profile_governs_assembly_and_extraction(tmp_path: Path) -> None:
    """Anti-``M_SKIP_PROFILE_HASH_BINDING``.

    The runner loads the profile ONCE and uses that same object for assembly,
    payloads and extraction, so ``compute_profile_hash_v2(profile)`` always
    equals ``manifest.identity.profile_hash``. This proves the binding holds
    rather than assuming it: a run cannot be assembled under one profile and
    judged under another.
    """

    from app.agent_review.profile_loader_v2 import compute_profile_hash_v2

    captured: list = []
    outcome, _, _, _ = _run(tmp_path, captured=captured)
    assert (
        compute_profile_hash_v2(outcome.prepared.profile)
        == outcome.prepared.manifest.identity.profile_hash
    )


# -- the runner, not the caller, performs the front half --------------------


def test_runner_itself_acquires_the_authoritative_diff(tmp_path: Path) -> None:
    """Anti-``M_SKIP_REAL_DIFF_ACQUISITION``: the composition authority must
    call the real git-backed acquisition. If a future edit accepted a
    caller-supplied diff instead, this dies."""

    from app.agent_review import operational_run_v2 as module

    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    seen: list = []
    real = module.acquire_authoritative_diff_v2

    def _spy(repo_root, *, base_sha, head_sha):
        seen.append((str(repo_root), base_sha, head_sha))
        return real(repo_root, base_sha=base_sha, head_sha=head_sha)

    with mock.patch.object(module, "acquire_authoritative_diff_v2", _spy):
        module.prepare_operational_review_v2(
            repo_root=repo, target_profile_root=repo, grouping_policy=_grouping_policy(),
            base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
            toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
        )

    assert seen == [(str(repo), base_sha, head_sha)]


def test_runner_delegates_the_back_half_instead_of_reimplementing_it(
    tmp_path: Path,
) -> None:
    """Anti-``M_BYPASS_RUN_SYNTHETIC_BACKHALF``: the operational wrapper must
    reach readiness THROUGH the existing back half, not via a second
    orchestration path."""

    from app.agent_review import operational_run_v2 as module

    captured: list = []
    calls: list = []
    real = module.run_synthetic_review_v2

    def _spy(**kwargs):
        calls.append(sorted(kwargs))
        return real(**kwargs)

    with mock.patch.object(module, "run_synthetic_review_v2", _spy):
        _run(tmp_path, captured=captured)

    assert len(calls) == 1
    for required in ("content", "manifest", "payload_by_chunk_id", "transport", "policies"):
        assert required in calls[0]


def test_policies_are_derived_from_the_loaded_profile(tmp_path: Path) -> None:
    """Anti-self-issued-authority: ``policies`` must come from the profile the
    front half loaded, never from a caller-supplied or invented value."""

    from app.agent_review import operational_run_v2 as module

    captured: list = []
    seen: list = []
    real = module.run_synthetic_review_v2

    def _spy(**kwargs):
        seen.append(kwargs["policies"])
        return real(**kwargs)

    with mock.patch.object(module, "run_synthetic_review_v2", _spy):
        outcome, _, _, _ = _run(tmp_path, captured=captured)

    assert seen == [outcome.prepared.profile.policies]


# -- the CLI is thin: it selects transports and serializes, nothing else ----


def _run_cli(argv: list[str]) -> tuple[int, str, str, object]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "aiops_review_run_v2",
        Path(__file__).resolve().parents[2] / "scripts" / "aiops-review-run-v2.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_router_mode_reads_the_credential_from_the_environment_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Router credential must never be an argv value: argv is visible in
    process listings and CI logs. A missing key is not a CLI error either --
    it degrades through the transport's own ``router_disabled`` semantics."""

    module = _run_cli([])
    args = module._parse_args(
        [
            "--contract-version", "v2", "--repo-root", ".", "--target-profile", ".",
            "--grouping-policy", "g.json", "--base-sha", "a" * 40, "--head-sha", "b" * 40,
            "--tested-merge-sha", "c" * 40, "--pr-number", "1", "--toolrepo-sha", "d" * 40,
            "--evidence-hash", "e" * 64, "--max-lines-per-chunk", "1000",
            "--pr-state", "open", "--run-origin", "o.json", "--checks-snapshot", "s.json",
            "--toolchain-digest", "f" * 64, "--transport", "router",
            "--router-base-url", "https://router.example", "--router-model", "review:code",
            "--output", "out.json",
        ]
    )
    assert not any("api-key" in a and "env" not in a for a in vars(args))

    # With no key in the environment the EXISTING transport refuses at
    # construction with the established `router_disabled` code. The CLI adds
    # no second opinion and invents no synonym.
    from app.agent_review.review_transport_v2 import ChunkTransportError

    monkeypatch.delenv("AGENT_ROUTER_API_KEY", raising=False)
    with pytest.raises(ChunkTransportError) as excinfo:
        module._build_transport(args)
    assert excinfo.value.reason_code == "router_disabled"

    # and with a key present it constructs, without the key entering argv
    monkeypatch.setenv("AGENT_ROUTER_API_KEY", "env-only-secret")
    assert callable(module._build_transport(args))
    assert "env-only-secret" not in " ".join(str(v) for v in vars(args).values())


def test_cli_offline_mode_requires_its_responses_dir(tmp_path: Path) -> None:
    module = _run_cli([])
    args = module._parse_args(
        [
            "--contract-version", "v2", "--repo-root", ".", "--target-profile", ".",
            "--grouping-policy", "g.json", "--base-sha", "a" * 40, "--head-sha", "b" * 40,
            "--tested-merge-sha", "c" * 40, "--pr-number", "1", "--toolrepo-sha", "d" * 40,
            "--evidence-hash", "e" * 64, "--max-lines-per-chunk", "1000",
            "--pr-state", "open", "--run-origin", "o.json", "--checks-snapshot", "s.json",
            "--toolchain-digest", "f" * 64, "--transport", "offline",
            "--output", "out.json",
        ]
    )
    with pytest.raises(module.RunCliError) as excinfo:
        module._build_transport(args)
    assert excinfo.value.reason_code == module.TRANSPORT_MODE_INVALID_REASON_V2


def test_cli_holds_no_pipeline_semantics(tmp_path: Path) -> None:
    """Structural guard: if pipeline logic ever migrates into the CLI, this
    dies. The CLI may name the composition authority and the two transports;
    it must not name the stages they own."""

    source = (
        Path(__file__).resolve().parents[2] / "scripts" / "aiops-review-run-v2.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "acquire_authoritative_diff_v2",
        "assemble_manifest_from_diff_v2",
        "build_chunk_payloads_from_profile_v2",
        "extract_review_content_v2",
        "run_synthetic_review_v2",
        "execute_chunk_review_v2",
        "parse_bound_chunk_response_v2",
        "synthesize_chunk_results_v2",
        "compute_readiness_decision_v2",
    ):
        assert forbidden not in source, f"CLI reimplements/steers {forbidden}"

    assert "run_operational_review_v2" in source


def test_cli_never_persists_the_credential_or_transport_material(tmp_path: Path) -> None:
    """The CLI's only persisted artifact is the existing ReviewReadinessV2."""

    source = (
        Path(__file__).resolve().parents[2] / "scripts" / "aiops-review-run-v2.py"
    ).read_text(encoding="utf-8")
    # exactly one write_text, and it serializes readiness
    assert source.count("write_text(") == 1
    assert "readiness.model_dump_json" in source
    # Scope the check to executable code: the module docstring legitimately
    # NAMES the things it must not persist, and a substring check over the
    # whole file would trip on its own prose.
    import ast

    code_only = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    executable = ast.dump(tree)

    for forbidden in ("content.model_dump", "messages", "assistant"):
        assert forbidden not in executable, f"CLI may persist/expose {forbidden}"
    assert "api_key=os.environ.get" in code_only


# -- the runner itself establishes closure, and does so before transport ----


def test_runner_establishes_preparation_closure_before_the_back_half(
    tmp_path: Path,
) -> None:
    """Anti-``M_SKIP_PAYLOAD_CONTENT_SET_BINDING``.

    Testing the closure function in isolation proves it works, not that the
    runner uses it. This asserts the call actually happens inside
    ``prepare_operational_review_v2`` -- and, in the full run, strictly
    before any transport invocation.
    """

    from app.agent_review import operational_run_v2 as module

    order: list[str] = []
    real_closure = module._establish_preparation_closure_v2

    def _closure_spy(**kwargs):
        order.append("closure")
        return real_closure(**kwargs)

    captured: list = []

    def _tracking_router(http_request, timeout):
        order.append("transport")
        return _mocked_router(captured)(http_request, timeout)

    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    authority = _empty_authority(repo)

    prepared = module.prepare_operational_review_v2(
        repo_root=repo, target_profile_root=repo, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
    )
    _CAPTURED_RESULT_DOCUMENT[0] = json.dumps(
        _router_result_document(prepared.payload_by_chunk_id[prepared.content.chunks[0].chunk_id])
    )

    with mock.patch.object(module, "_establish_preparation_closure_v2", _closure_spy):
        with mock.patch(
            "app.agent_review.review_transport_v2._open_agent_router_request_v2",
            side_effect=_tracking_router,
        ):
            module.run_operational_review_v2(
                repo_root=repo, target_profile_root=repo,
                grouping_policy=_grouping_policy(), base_sha=base_sha, head_sha=head_sha,
                tested_merge_sha=head_sha, pr_number=1, toolrepo_sha="b" * 40,
                evidence_hash="c" * 64,
                transport=agent_router_transport_v2(
                    base_url="https://router.example/", api_key="secret-token",
                    model="review:code",
                ),
                max_lines_per_chunk=1000, **authority,
            )

    assert "closure" in order, "the runner skipped its own preparation closure"
    assert order.index("closure") < order.index("transport")


def test_extraction_receives_the_target_profile_root_profile(tmp_path: Path) -> None:
    """Anti-``M_SKIP_PROFILE_HASH_BINDING``.

    The profile handed to extraction must be the one loaded from
    ``target_profile_root`` -- the TRUSTED base checkout -- not one re-read
    from ``repo_root``, which is the tree under review and may disagree.
    """

    from app.agent_review import operational_run_v2 as module

    # The trusted root's profile is deliberately DISTINGUISHABLE from the one
    # committed inside the reviewed tree, so "which root did extraction get?"
    # is answerable. Identical profiles would make this test unable to fail.
    repo, base_sha, head_sha = _build_real_repo(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    _write_profile_root(trusted)
    trusted_profile_path = trusted / ".aiops" / "target-profile.v2.yaml"
    trusted_profile_path.write_text(
        # a benign budget difference: changes the profile hash without
        # changing behaviour for this one-chunk run
        _profile_yaml().replace("max_chunks: 32", "max_chunks: 31"),
        encoding="utf-8",
    )

    seen: list = []
    real = module.extract_review_content_v2

    def _spy(**kwargs):
        seen.append(kwargs["target_profile"])
        return real(**kwargs)

    from app.agent_review.profile_loader_v2 import (
        compute_profile_hash_v2,
        load_target_profile_v2,
    )

    with mock.patch.object(module, "extract_review_content_v2", _spy):
        module.prepare_operational_review_v2(
            repo_root=repo, target_profile_root=trusted,
            grouping_policy=_grouping_policy(), base_sha=base_sha, head_sha=head_sha,
            tested_merge_sha=head_sha, pr_number=1, toolrepo_sha="b" * 40,
            evidence_hash="c" * 64, max_lines_per_chunk=1000,
        )

    assert len(seen) == 1
    assert compute_profile_hash_v2(seen[0]) == compute_profile_hash_v2(
        load_target_profile_v2(trusted)
    )
    # and it is NOT the reviewed tree's own profile
    assert compute_profile_hash_v2(seen[0]) != compute_profile_hash_v2(
        load_target_profile_v2(repo)
    )
    assert seen[0].budgets.max_chunks == 31


def test_policies_are_not_self_issued(tmp_path: Path) -> None:
    """Anti-``M_HARDCODE_POLICIES``: the policies governing readiness must be
    the loaded profile's OWN object, not a value this module composed.

    Identity (``is``), not equality: an invented policy set that happened to
    compare equal would still be self-issued authority.
    """

    from app.agent_review import operational_run_v2 as module

    captured: list = []
    seen: list = []
    real = module.run_synthetic_review_v2

    def _spy(**kwargs):
        seen.append(kwargs["policies"])
        return real(**kwargs)

    with mock.patch.object(module, "run_synthetic_review_v2", _spy):
        outcome, _, _, _ = _run(tmp_path, captured=captured)

    assert seen[0] is outcome.prepared.profile.policies
