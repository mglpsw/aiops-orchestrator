"""Host-owned CI acquirer (`#201-C0`, C0-6).

Driven entirely from recorded GitHub payloads. The acquirer's real-API path is
deliberately not a merge gate for C0 -- see the PR description -- so these tests
prove the mapping, the local-git parentage observation, and the fail-closed
behaviour, without a token or a network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-acquire-authoritative-checks-v2.py"

REPO = "mglpsw/aiops-orchestrator"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def merge_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A real repository with a real merge commit, so parentage is observed
    rather than asserted."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-qm", "feature")
    head = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "synthetic merge")
    merge = _git(repo, "rev-parse", "HEAD")
    return repo, base, head, merge


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "check_runs": [
            {
                "id": 100,
                "name": "Validate repository",
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "check_suite": {"id": 55},
            }
        ],
        "workflow_runs": [
            {
                "id": 900,
                "check_suite_id": 55,
                "path": ".github/workflows/ci.yml",
                "event": "pull_request",
                "head_branch": "feature",
                # The commit the workflow DEFINITION was loaded from, and the
                # repository it belongs to. GitHub reports both on every run.
                "head_sha": "a" * 40,
                "repository": {"full_name": REPO},
                "run_attempt": 2,
                "run_started_at": "2026-08-11T10:00:00Z",
                "referenced_workflows": [
                    {"path": "mglpsw/aiops-orchestrator/.github/workflows/authoritative-checks.reusable.yml", "sha": "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96", "ref": "refs/heads/master"}
                ],
                "pull_requests": [
                    {"number": 7, "base": {"ref": "master", "sha": "b" * 40}, "head": {"sha": "a" * 40}}
                ],
            }
        ],
    }
    payload["producer_attestations"] = {"900": _attestation_for(payload)}
    payload.update(overrides)
    return payload


def _attestation_for(payload: dict) -> dict:
    """The producer's checkout-free attestation, keyed by workflow run id."""

    from app.agent_review.authoritative_producer_evidence_v2 import (
        ProducerAttestationV2,
        compute_producer_attestation_digest_v2,
    )

    fields: dict = {
        "schema_id": "agent-review.producer-attestation.v2",
        "schema_version": 2,
        "source": "aiops-authoritative-check-producer",
        "repository": REPO,
        "pr_number": 7,
        "base_sha": "b" * 40,
        "head_sha": "a" * 40,
        "executed_sha": "d" * 40,
        "workflow_run_id": "900",
        "run_attempt": 2,
        "test_outcome": "success",
        "check_execution_mode": "reexecuted_in_producer_run",
        "executed_sha_derivation": "verified_checkout_rev_parse",
        "policy_digest": "5" * 64,
        "toolchain_digest": "6" * 64,
    }
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**fields, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**fields, attestation_digest=digest).model_dump(mode="json")


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _acquire(tmp_path: Path, merge_repo, payload: dict) -> subprocess.CompletedProcess[str]:
    repo, _base, head, merge = merge_repo
    observations = tmp_path / "payload.json"
    observations.write_text(json.dumps(payload), encoding="utf-8")
    return _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", merge,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(tmp_path / "snapshot.json"),
        ]
    )


def test_acquires_a_parseable_snapshot(tmp_path: Path, merge_repo) -> None:
    repo, base, head, merge = merge_repo
    result = _acquire(tmp_path, merge_repo, _payload())
    assert result.returncode == 0, result.stderr

    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.acquisition.repository == REPO
    assert snapshot.acquisition.head_sha == head
    assert snapshot.tested_merge_sha == merge
    assert len(snapshot.observations) == 1
    assert snapshot.observations[0].run_attempt == 2


def test_parentage_is_observed_from_real_git_not_from_the_api(tmp_path: Path, merge_repo) -> None:
    """The API reported nothing about parentage. Asking the same service that
    reported the check to also vouch for the tree would be circular."""

    repo, base, head, merge = merge_repo
    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert list(snapshot.tested_merge_parents) == [base, head]


def test_acquisition_identity_is_recorded(tmp_path: Path, merge_repo) -> None:
    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.acquisition.acquired_by == "aiops-acquire-authoritative-checks-v2"
    assert snapshot.acquisition.api_host == "api.github.com"


def test_pull_request_runs_report_a_pull_ref_not_a_base_ref(tmp_path: Path, merge_repo) -> None:
    """The KNOWN LIMITATION, pinned as a test so it cannot be quietly
    "fixed" by asserting a base-owned origin GitHub never reported."""

    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].workflow_execution_ref == "refs/pull/7/merge"


def test_branch_runs_report_a_branch_ref(tmp_path: Path, merge_repo) -> None:
    payload = _payload()
    payload["workflow_runs"][0].update({"event": "push", "pull_requests": []})
    _acquire(tmp_path, merge_repo, payload)
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].workflow_execution_ref == "refs/heads/feature"


def test_a_check_run_with_no_matching_workflow_run_is_dropped(tmp_path: Path, merge_repo) -> None:
    """Dropped rather than recorded with invented workflow fields. Safe:
    a required check that ends up absent fails closed downstream."""

    payload = _payload()
    payload["check_runs"].append(
        {
            "id": 101,
            "name": "orphan",
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
            "check_suite": {"id": 999},
        }
    )
    _acquire(tmp_path, merge_repo, payload)
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert [obs.check_run_name for obs in snapshot.observations] == ["Validate repository"]


def test_a_non_merge_tested_commit_is_refused(tmp_path: Path, merge_repo) -> None:
    repo, base, head, _merge = merge_repo
    observations = tmp_path / "payload.json"
    observations.write_text(json.dumps(_payload()), encoding="utf-8")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")

    result = _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", root,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(tmp_path / "snapshot.json"),
        ]
    )

    assert result.returncode != 0
    assert "git_observation_failed" in result.stderr


def test_output_is_canonical_and_reproducible(tmp_path: Path, merge_repo) -> None:
    _acquire(tmp_path, merge_repo, _payload())
    first = (tmp_path / "snapshot.json").read_bytes()
    _acquire(tmp_path, merge_repo, _payload())
    assert (tmp_path / "snapshot.json").read_bytes() == first


def test_an_unparseable_snapshot_is_never_written(tmp_path: Path, merge_repo) -> None:
    """Writing something the offline parser would reject just moves the
    failure downstream."""

    payload = _payload()
    payload["check_runs"][0]["conclusion"] = "exploded"
    result = _acquire(tmp_path, merge_repo, payload)

    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()


def test_pull_request_target_records_the_base_ref_not_a_pull_ref(tmp_path: Path, merge_repo) -> None:
    """Codex finding 3. `pull_request_target` loads its workflow from the BASE
    branch -- that is the event's defining property. Recording a pull ref for it
    was factually wrong and made every otherwise-authorised
    `pull_request_target` run permanently unauthorisable, since policy only
    admits the default branch."""

    payload = _payload()
    payload["workflow_runs"][0]["event"] = "pull_request_target"
    _acquire(tmp_path, merge_repo, payload)
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].workflow_execution_ref == "refs/heads/master"


def test_the_runs_own_base_and_head_are_recorded(tmp_path: Path, merge_repo) -> None:
    """Codex finding 2: without these, a run cannot be bound to a base/head
    pair and a stale green is indistinguishable from a current one."""

    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].run_base_sha == "b" * 40
    assert snapshot.observations[0].run_head_sha == "a" * 40


def test_an_unrecognised_trigger_is_refused(tmp_path: Path, merge_repo) -> None:
    """An unknown event cannot be reasoned about, so it is refused rather than
    bucketed into a catch-all that later code would have to guess at."""

    payload = _payload()
    payload["workflow_runs"][0]["event"] = "repository_dispatch"
    result = _acquire(tmp_path, merge_repo, payload)
    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()


def test_a_pull_request_run_missing_its_base_is_refused(tmp_path: Path, merge_repo) -> None:
    payload = _payload()
    payload["workflow_runs"][0]["pull_requests"] = [{"number": 7}]
    result = _acquire(tmp_path, merge_repo, payload)
    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()


def test_run_started_at_is_recorded(tmp_path: Path, merge_repo) -> None:
    """Codex round 3: without it, distinct workflow runs cannot be ordered and
    an old rerun outranks a newer run."""

    _acquire(tmp_path, merge_repo, _payload())
    snapshot = parse_authoritative_ci_snapshot_v2((tmp_path / "snapshot.json").read_bytes())
    assert snapshot.observations[0].run_started_at == "2026-08-11T10:00:00Z"


def test_a_run_without_a_start_time_is_refused(tmp_path: Path, merge_repo) -> None:
    """Guessing an ordering is how a stale green outranks a current red."""

    payload = _payload()
    payload["workflow_runs"][0].pop("run_started_at", None)
    payload["workflow_runs"][0].pop("created_at", None)
    result = _acquire(tmp_path, merge_repo, payload)
    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()


# ---------------------------------------------------------------------------
# Codex round 5 -- the live path must fetch attestations, not defer them
# ---------------------------------------------------------------------------


def _acquirer_module():
    """Load the CLI as a module so its pure helpers can be tested directly."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("_acq", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _assert_attestation_refused(raw: bytes) -> None:
    """Assert the exact refusal, not merely "something raised".

    A blind `pytest.raises(Exception)` would also pass on a typo in the test
    itself, which is precisely the class of vacuous assertion this PR exists to
    keep out of the provenance path."""

    acq = _acquirer_module()
    with pytest.raises(acq.AcquisitionError) as exc:
        acq.extract_attestation_from_zip_v2(raw)
    assert acq.ATTESTATION_INVALID_REASON in str(exc.value)


def test_the_live_payload_includes_producer_attestations() -> None:
    """The defect Codex round 5 found: `_fetch_payload` built only check_runs
    and workflow_runs, so every live observation carried no attestation and
    every required check was refused. Only the fixture path could promote."""

    acq = _acquirer_module()
    attestation = {"schema_id": "agent-review.producer-attestation.v2", "executed_sha": "d" * 40}

    collected = acq.collect_attestations_v2(
        workflow_runs=[{"id": 900}, {"id": 901}],
        list_artifacts=lambda run_id: (
            [{"id": 7, "name": acq.ATTESTATION_ARTIFACT_NAME, "expired": False}] if run_id == 900 else []
        ),
        download_artifact=lambda artifact_id: _zip_bytes(
            {acq.ATTESTATION_MEMBER_NAME: json.dumps(attestation).encode()}
        ),
    )

    assert collected == {"900": attestation}


def test_an_expired_artifact_is_ignored() -> None:
    acq = _acquirer_module()
    collected = acq.collect_attestations_v2(
        workflow_runs=[{"id": 900}],
        list_artifacts=lambda run_id: [
            {"id": 7, "name": acq.ATTESTATION_ARTIFACT_NAME, "expired": True}
        ],
        download_artifact=lambda artifact_id: b"never reached",
    )
    assert collected == {}


def test_a_differently_named_artifact_is_ignored() -> None:
    acq = _acquirer_module()
    collected = acq.collect_attestations_v2(
        workflow_runs=[{"id": 900}],
        list_artifacts=lambda run_id: [{"id": 7, "name": "coverage", "expired": False}],
        download_artifact=lambda artifact_id: b"never reached",
    )
    assert collected == {}


def test_only_the_expected_member_is_read() -> None:
    """Reading "whatever single file is inside" would let an attacker choose
    the payload by choosing the filename."""

    _assert_attestation_refused(_zip_bytes({"something-else.json": b'{"executed_sha": "evil"}'}))


def test_a_malformed_zip_is_refused() -> None:
    _assert_attestation_refused(b"not a zip at all")


def test_an_oversized_zip_is_refused_before_parsing() -> None:
    acq = _acquirer_module()
    _assert_attestation_refused(b"x" * (acq.MAX_ATTESTATION_ZIP_BYTES + 1))


def test_a_zip_with_too_many_entries_is_refused() -> None:
    acq = _acquirer_module()
    _assert_attestation_refused(
        _zip_bytes({f"f{i}.json": b"{}" for i in range(acq.MAX_ATTESTATION_ZIP_ENTRIES + 1)})
    )


def test_the_attestation_member_is_parsed_strictly() -> None:
    """Duplicate keys in the artifact are refused, like every other input."""

    acq = _acquirer_module()
    _assert_attestation_refused(_zip_bytes({acq.ATTESTATION_MEMBER_NAME: b'{"a": 1, "a": 2}'}))


def test_a_non_object_attestation_is_refused() -> None:
    acq = _acquirer_module()
    _assert_attestation_refused(_zip_bytes({acq.ATTESTATION_MEMBER_NAME: b"[1, 2, 3]"}))


def _paging_get_json(pages: dict[int, list], key: str, seen: list[str] | None = None):
    """A fake `get_json` that serves a recorded page map and records the paths."""

    def get_json(path: str):
        if seen is not None:
            seen.append(path)
        page = int(path.rsplit("page=", 1)[1])
        return {"total_count": sum(len(p) for p in pages.values()), key: pages.get(page, [])}

    return get_json


def test_every_page_of_a_list_endpoint_is_read() -> None:
    """Reading page 1 only reports a producer that genuinely ran as MISSING,
    and "missing" is not a neutral outcome here -- it is evidence the gate
    acts on."""

    acq = _acquirer_module()
    seen: list[str] = []
    pages = {
        1: [{"id": i} for i in range(acq.ACQUISITION_PAGE_SIZE)],
        2: [{"id": 1000}, {"id": 1001}],
    }
    items = acq.paginate_envelope_v2(
        get_json=_paging_get_json(pages, "workflow_runs", seen),
        path="/repos/o/r/actions/runs?head_sha=abc",
        key="workflow_runs",
    )

    assert len(items) == acq.ACQUISITION_PAGE_SIZE + 2
    assert items[-1] == {"id": 1001}
    assert len(seen) == 2
    assert f"per_page={acq.ACQUISITION_PAGE_SIZE}" in seen[0]
    # The endpoint already carries a query string; paging must extend it, not
    # start a second one.
    assert seen[0].count("?") == 1


def test_a_short_page_stops_the_walk() -> None:
    acq = _acquirer_module()
    seen: list[str] = []
    items = acq.paginate_envelope_v2(
        get_json=_paging_get_json({1: [{"id": 1}, {"id": 2}]}, "check_runs", seen),
        path="/repos/o/r/commits/abc/check-runs",
        key="check_runs",
    )
    assert items == [{"id": 1}, {"id": 2}]
    assert len(seen) == 1


def test_a_list_that_never_ends_is_refused_rather_than_truncated() -> None:
    """A silently short list is indistinguishable from "the producer did not
    run", and one of those is a lie."""

    acq = _acquirer_module()
    full_page = [{"id": i} for i in range(acq.ACQUISITION_PAGE_SIZE)]
    with pytest.raises(acq.AcquisitionError) as exc:
        acq.paginate_envelope_v2(
            get_json=lambda path: {"workflow_runs": full_page},
            path="/repos/o/r/actions/runs",
            key="workflow_runs",
        )
    assert exc.value.reason_code == acq.ACQUISITION_FAILED_REASON


def test_a_non_envelope_response_is_refused() -> None:
    acq = _acquirer_module()
    with pytest.raises(acq.AcquisitionError) as exc:
        acq.paginate_envelope_v2(
            get_json=lambda path: [{"id": 1}],
            path="/repos/o/r/actions/runs",
            key="workflow_runs",
        )
    assert exc.value.reason_code == acq.ACQUISITION_FAILED_REASON


def test_a_missing_envelope_key_is_refused_not_defaulted() -> None:
    """`.get(key, [])` would turn a changed or errored payload into "no runs",
    which the assembler then reports as a missing required check."""

    acq = _acquirer_module()
    with pytest.raises(acq.AcquisitionError) as exc:
        acq.paginate_envelope_v2(
            get_json=lambda path: {"message": "Not Found"},
            path="/repos/o/r/actions/runs",
            key="workflow_runs",
        )
    assert exc.value.reason_code == acq.ACQUISITION_FAILED_REASON


def test_an_output_that_aliases_the_observations_input_is_refused(tmp_path: Path, merge_repo) -> None:
    """The recorded GitHub payload is the evidence a later audit or rerun needs.
    Reading it and then overwriting it with the derived snapshot -- while
    returning success -- destroys exactly that. The quality gate already
    refuses this class twice over (#145, #156); the acquirer must too."""

    repo, _base, head, merge = merge_repo
    observations = tmp_path / "payload.json"
    original = json.dumps(_payload())
    observations.write_text(original, encoding="utf-8")

    result = _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", merge,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(observations),
        ]
    )

    assert result.returncode == 1
    assert "authoritative_check_output_overwrites_input" in result.stderr
    # The refusal has to happen BEFORE the write, not merely be reported after.
    assert observations.read_text(encoding="utf-8") == original


def test_an_output_aliasing_the_input_by_a_different_path_spelling_is_refused(
    tmp_path: Path, merge_repo
) -> None:
    """Resolved paths, not string equality: `./x` and `x` are the same file."""

    repo, _base, head, merge = merge_repo
    observations = tmp_path / "payload.json"
    original = json.dumps(_payload())
    observations.write_text(original, encoding="utf-8")

    result = _run(
        [
            "--repository", REPO,
            "--head-sha", head,
            "--tested-merge-sha", merge,
            "--git-dir", str(repo),
            "--observations", str(observations),
            "--output", str(tmp_path / "." / "payload.json"),
        ]
    )

    assert result.returncode == 1
    assert "authoritative_check_output_overwrites_input" in result.stderr
    assert observations.read_text(encoding="utf-8") == original


def test_two_attestation_artifacts_in_one_run_are_refused() -> None:
    """Taking the first match lets an attacker win by uploading a second
    artifact under the same name. Two candidates is a contradiction about which
    one speaks for the run, and there is no safe way to pick."""

    acq = _acquirer_module()
    attestation = {"schema_id": "agent-review.producer-attestation.v2", "executed_sha": "d" * 40}
    with pytest.raises(acq.AcquisitionError) as exc:
        acq.collect_attestations_v2(
            workflow_runs=[{"id": 900}],
            list_artifacts=lambda run_id: [
                {"id": 7, "name": acq.ATTESTATION_ARTIFACT_NAME, "expired": False},
                {"id": 8, "name": acq.ATTESTATION_ARTIFACT_NAME, "expired": False},
            ],
            download_artifact=lambda artifact_id: _zip_bytes(
                {acq.ATTESTATION_MEMBER_NAME: json.dumps(attestation).encode()}
            ),
        )
    assert exc.value.reason_code == acq.ATTESTATION_AMBIGUOUS_REASON


# =============================================================================
# Independent audit (2026-08-12) -- security findings
# =============================================================================
#
# Confirmed by an independent read-only audit of HEAD 8b7e94c, verified
# separately before being accepted: two real security defects in the
# acquirer, unrelated to the architectural questions raised in the same
# audit. Fixed here, red-first, ahead of and independent of any
# architectural decision.


def test_authorization_is_stripped_on_redirect() -> None:
    """GitHub's artifact-download endpoint answers with a 302 to a pre-signed
    blob-storage URL that authenticates via the URL itself, not a bearer
    token. `urllib`'s default redirect handling replays every request header
    except `Content-Length`/`Content-Type` -- verified against this
    interpreter's own `HTTPRedirectHandler.redirect_request` source, which
    lists only those two as stripped. Left unmodified, a live GitHub token
    would be handed to whatever host the redirect names."""

    acq = _acquirer_module()
    handler = acq._StripAuthOnRedirectHandler()
    original = acq.urllib.request.Request(
        "https://api.github.com/repos/o/r/actions/artifacts/1/zip",
        headers={
            "Authorization": "Bearer super-secret-token",
            "Accept": "application/vnd.github+json",
        },
    )

    redirected = handler.redirect_request(
        original, None, 302, "Found", {}, "https://blob.example.com/signed?sig=abc"
    )

    assert redirected is not None
    assert not redirected.has_header("Authorization")
    # Ordinary headers still make the hop -- only the credential is stripped.
    assert redirected.has_header("Accept")


def test_authorization_is_stripped_on_every_redirect_status() -> None:
    """303 and 307 are both real GitHub Actions API redirect codes; the
    stripping must not be coded against 302 alone."""

    acq = _acquirer_module()
    handler = acq._StripAuthOnRedirectHandler()
    for code in (301, 302, 303, 307, 308):
        original = acq.urllib.request.Request(
            "https://api.github.com/x", headers={"Authorization": "Bearer t"}, method="GET"
        )
        redirected = handler.redirect_request(
            original, None, code, "", {}, "https://blob.example.com/y"
        )
        assert redirected is not None
        assert not redirected.has_header("Authorization"), code


def test_a_check_run_missing_an_id_is_refused(tmp_path: Path, merge_repo) -> None:
    """`str(None)` is the literal string `"None"`, and `"None"` satisfies
    `SafeIdentifier`'s regex -- it is not rejected by schema validation, unlike
    every other field this function builds. Two distinct check runs each
    missing `id` would otherwise collapse onto the identical identity
    `"None"`, defeating the run-before-attempt ordering the selector depends
    on. Confirmed and reproduced end-to-end by an independent audit."""

    payload = _payload()
    del payload["check_runs"][0]["id"]
    result = _acquire(tmp_path, merge_repo, payload)

    assert result.returncode != 0
    assert "authoritative_check_acquisition_failed" in result.stderr
    assert not (tmp_path / "snapshot.json").exists()


def test_a_workflow_run_missing_an_id_is_refused(tmp_path: Path, merge_repo) -> None:
    payload = _payload()
    del payload["workflow_runs"][0]["id"]
    result = _acquire(tmp_path, merge_repo, payload)

    assert result.returncode != 0
    assert "authoritative_check_acquisition_failed" in result.stderr
    assert not (tmp_path / "snapshot.json").exists()


def test_two_runs_each_missing_an_id_do_not_collapse_onto_one_identity(
    tmp_path: Path, merge_repo
) -> None:
    """The end-to-end reproduction: without the fix, both runs would silently
    become `workflow_run_id="None"`, letting an old rerun (attempt 3) outrank
    a newer first attempt -- a stale green over a current red. With the fix,
    acquisition refuses outright rather than fabricating a shared identity."""

    payload = _payload()
    payload["check_runs"] = [
        {**payload["check_runs"][0], "check_suite": {"id": 55}},
        {**payload["check_runs"][0], "check_suite": {"id": 56}},
    ]
    del payload["check_runs"][0]["id"]
    del payload["check_runs"][1]["id"]
    payload["workflow_runs"] = [
        {**payload["workflow_runs"][0], "check_suite_id": 55, "run_attempt": 3},
        {**payload["workflow_runs"][0], "check_suite_id": 56, "run_attempt": 1},
    ]
    del payload["workflow_runs"][0]["id"]
    del payload["workflow_runs"][1]["id"]
    payload["producer_attestations"] = {}

    result = _acquire(tmp_path, merge_repo, payload)

    assert result.returncode != 0
    assert not (tmp_path / "snapshot.json").exists()
