"""Synthetic transport fixtures, never captured historical GitHub responses."""
from __future__ import annotations

import io
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import github_codex_post_ready_guard as guard

HEAD = "661422837a3099bb8d989907d42314e26e410aa7"
BASE = "6bbd2f949989da3e90e1d9c527e37059c0b628ff"
OLD = "d3f5946c4d0513def9f7c2018b63703a53df1cc7"
REPO = "mglpsw/aiops-orchestrator"
BOT = {"login": "chatgpt-codex-connector[bot]"}
CHECKS = ("Validate repository", "AgentReview release gates")


def summary(ref=HEAD[:7], completed="2026-10-01T16:21:23Z", trigger="Draft marked ready"):
    return ("## Codex Review Summary\n"
            "| Review | Status | Commit | Review trigger |\n"
            "| --- | --- | --- | --- |\n"
            "| 📝 **Code Review** | ✅ **Completed** "
            f'<relative-time datetime="{completed}">{completed}</relative-time> | '
            + chr(96) + ref + chr(96) + f" | {trigger} |\n")


def disposition(finding, author="mglpsw", state="FIXED", subject=HEAD):
    return {"id": 71, "user": {"login": author}, "created_at": "2026-10-01T16:21:30Z",
            "updated_at": "2026-10-01T16:21:30Z",
            "body": f"Guard-Disposition: {finding} {state}\nSubject-Head: {subject}\n"
                    f"Repair-Commit: {HEAD}\nEvidence: causal regression test passes on repair HEAD"}


class FakeGitHub(guard.GitHubReadOnlyClient):
    """Exercise production pagination/normalization/binding using an offline transport."""

    def __init__(self):
        super().__init__("synthetic-test-token")
        self.pr = {"number": 370, "state": "open", "draft": False, "merged": False,
                   "head": {"sha": HEAD}, "base": {"sha": BASE, "repo": {"full_name": REPO}}}
        self.reviews = [{"id": 41, "user": BOT, "state": "COMMENTED", "body": "",
                         "commit_id": HEAD, "submitted_at": "2026-10-01T16:21:19Z"}]
        self.inline = []
        self.comments = [{"id": 51, "user": BOT, "body": summary(), "created_at": "2026-10-01T16:16:41Z",
                          "updated_at": "2026-10-01T16:21:24Z"}]
        self.timeline = [{"id": 31, "event": "ready_for_review", "created_at": "2026-10-01T16:16:00Z"}]
        self.checks = [{"id": index + 1, "name": name, "status": "completed", "conclusion": "success",
                        "head_sha": HEAD, "app": {"slug": "github-actions"},
                        "check_suite": {"id": 800},
                        "details_url": f"https://github.com/{REPO}/actions/runs/700/job/{index+1}"}
                       for index, name in enumerate(CHECKS)]
        self.run = {"id": 700, "workflow_id": 600, "path": ".github/workflows/ci.yml",
                    "event": "pull_request", "head_sha": HEAD, "run_attempt": 1,
                    "check_suite_id": 800, "repository": {"full_name": REPO},
                    "status": "completed", "conclusion": "success",
                    "pull_requests": [{"number": 370, "head": {"sha": HEAD}, "base": {"sha": BASE},
                                       "url": f"https://api.github.com/repos/{REPO}/pulls/370"}]}
        self.runs = [self.run]
        self.jobs = [dict(check, run_id=700, run_attempt=1) for check in self.checks]
        self.resolved = HEAD
        self.blobs = {OLD: "a" * 40, HEAD: "b" * 40}
        self.comparisons = {(HEAD, HEAD): "identical", (OLD, HEAD): "ahead", (OLD, OLD): "identical"}
        self.reactions = []
        self.paths = []
        self.drift = False
        self.pr_reads = 0

    def _request(self, method, path, payload=None):
        self.paths.append(path)
        parsed = urlsplit(path)
        route = parsed.path
        params = parse_qs(parsed.query)
        page = int(params.get("page", ["1"])[0])
        if route.endswith("/actions/runs"):
            return {"total_count": len(self.runs), "workflow_runs": deepcopy(self.runs[(page-1)*100:page*100])}
        if route.endswith("/actions/runs/700"):
            return deepcopy(self.run)
        if route.endswith("/actions/runs/700/attempts/1/jobs"):
            return {"total_count": len(self.jobs), "jobs": deepcopy(self.jobs[(page-1)*100:page*100])}
        if route.endswith(f"/pulls/{self.pr['number']}"):
            self.pr_reads += 1
            result = deepcopy(self.pr)
            if self.drift and self.pr_reads > 1:
                result["head"]["sha"] = OLD
            return result
        if "/contents/" in route:
            return {"type": "file", "sha": self.blobs[params["ref"][0]]}
        if "/compare/" in route:
            pair = tuple(route.rsplit("/", 1)[1].split("..."))
            assert pair in self.comparisons, f"undeclared commit relation: {pair}"
            status = self.comparisons[pair]
            if isinstance(status, Exception):
                raise status
            return {"status": status}
        if route.endswith("/check-runs"):
            page = int(params["page"][0])
            return {"total_count": len(self.checks), "check_runs": deepcopy(self.checks[(page-1)*100:page*100])}
        if "/commits/" in route:
            return {"sha": self.resolved}
        collection = None
        if route.endswith(f"/pulls/{self.pr['number']}/reviews"):
            collection = self.reviews
        elif route.endswith(f"/pulls/{self.pr['number']}/comments"):
            collection = self.inline
        elif route.endswith(f"/issues/{self.pr['number']}/comments"):
            collection = self.comments
        elif route.endswith(f"/issues/{self.pr['number']}/reactions"):
            collection = self.reactions
        elif route.endswith("/events"):
            collection = self.timeline
        if collection is None:
            raise AssertionError(f"unexpected request: {path}")
        page = int(params["page"][0])
        return deepcopy(collection[(page-1)*100:page*100])

    def predecessor(self, body=False):
        self.reviews.insert(0, {"id": 40, "user": BOT, "state": "COMMENTED",
                               "commit_id": OLD, "submitted_at": "2026-10-01T16:00:00Z",
                               "body": "A material general finding" if body else ""})
        if not body:
            self.inline = [{"id": 61, "user": BOT, "body": "material finding",
                            "path": "scripts/example.py", "pull_request_review_id": 40,
                            "commit_id": HEAD}]  # GitHub may relocate the comment; review owns original SHA.


def evaluate(evidence):
    return guard.evaluate_evidence(evidence, expected_repo=REPO, expected_pr=370,
                                   expected_head=HEAD, expected_base=BASE,
                                   required_checks=CHECKS, trusted_adjudicators=("mglpsw",))


class PostReadyGuardTests(unittest.TestCase):
    def test_ci_wrong_workflow_preserves_original_counterexample(self):
        client = FakeGitHub()
        client.run["path"] = ".github/workflows/lookalike.yml"
        self.assertIn(evaluate(client.collect(REPO, 370)).state,
                      {"HELD_PENDING_REQUIRED_CI", "HELD_CODEX_UNAVAILABLE"})

    def test_ci_wrong_pr(self):
        client = FakeGitHub()
        client.run["pull_requests"][0]["number"] = 371
        self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_stale_base(self):
        client = FakeGitHub()
        client.run["pull_requests"][0]["base"]["sha"] = OLD
        self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_wrong_job_membership(self):
        client = FakeGitHub()
        client.jobs[0]["id"] = 999
        self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_suite_mismatch(self):
        client = FakeGitHub()
        client.checks[0]["check_suite"]["id"] = 999
        self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_ambiguous_canonical_runs(self):
        client = FakeGitHub()
        client.runs.append(dict(client.run, id=701))
        self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_real_shaped_positive_subgate(self):
        live = FakeGitHub().collect(REPO, 370)
        self.assertEqual(evaluate(live).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        self.assertFalse(evaluate(live).merge_authorized)

    def test_ci_details_url_is_not_authority(self):
        client = FakeGitHub()
        for check in client.checks:
            check["details_url"] = "https://untrusted.invalid/actions/runs/999"
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_wrong_repository_or_pr_reference_or_event_or_head(self):
        for field in ("repository", "pr_url", "event", "head", "pr_head"):
            client = FakeGitHub()
            if field == "repository": client.run["repository"]["full_name"] = "wrong/repository"
            if field == "pr_url": client.run["pull_requests"][0]["url"] = "https://api.github.com/repos/wrong/repository/pulls/370"
            if field == "event": client.run["event"] = "push"
            if field == "head": client.run["head_sha"] = OLD
            if field == "pr_head": client.run["pull_requests"][0]["head"]["sha"] = OLD
            with self.subTest(field=field):
                self.assertNotEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_ci_failed_or_pending_run_cannot_be_replaced_by_green_jobs(self):
        for status, conclusion in (("completed", "failure"), ("in_progress", None)):
            client = FakeGitHub()
            client.run.update(status=status, conclusion=conclusion)
            self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_REQUIRED_CI")

    def test_ci_wrong_job_attempt_run_head_or_status(self):
        for key, value in (("run_attempt", 2), ("run_id", 701), ("head_sha", OLD),
                           ("status", "in_progress"), ("conclusion", "failure")):
            client = FakeGitHub()
            client.jobs[0][key] = value
            with self.subTest(key=key):
                self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_REQUIRED_CI")

    def test_ci_jobs_later_page_and_duplicate_names(self):
        client = FakeGitHub()
        client.jobs = [dict(client.jobs[0], id=100+i, name=f"unrelated-{i}") for i in range(100)] + client.jobs
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        self.assertTrue(any("attempts/1/jobs?per_page=100&page=2" in path for path in client.paths))
        client.jobs.append(dict(client.jobs[-1], id=999))
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_REQUIRED_CI")

    def test_ci_collection_shape_completeness_and_mutable_run_drift(self):
        for defect in ("null_run", "null_pull", "null_job", "incomplete", "drift"):
            client = FakeGitHub()
            if defect == "null_run": client.runs = [None]
            if defect == "null_pull": client.run["pull_requests"] = [None]
            if defect == "null_job": client.jobs = [None]
            original = client._request
            reads = []
            def request(method, path, payload=None):
                result = original(method, path, payload)
                if defect == "incomplete" and "/jobs?" in path: result["total_count"] += 1
                if defect == "drift" and path.endswith("/actions/runs/700"):
                    reads.append(path)
                    if len(reads) > 1: result["run_attempt"] = 2
                return result
            client._request = request
            with self.subTest(defect=defect):
                self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")
    def test_live_path_positive_and_no_merge_authority(self):
        result = evaluate(FakeGitHub().collect(REPO, 370))
        self.assertEqual(result.state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        self.assertFalse(result.merge_authorized)

    def test_offline_fixture_cannot_promote_even_with_forged_live_flags(self):
        evidence = FakeGitHub().collect(REPO, 370).data
        evidence.update(collection_mode="live", live_authenticated=True)
        self.assertEqual(evaluate(evidence).state, "HELD_CODEX_UNAVAILABLE")
        # Causal control: bypassing the provenance guard exposes the old escape.
        policy = dict(expected_repo=REPO, expected_pr=370, expected_head=HEAD, expected_base=BASE,
                      required_checks=CHECKS, trusted_adjudicators=("mglpsw",),
                      trusted_check_producers=("github-actions",))
        self.assertEqual(guard._evaluate_live(evidence, **policy).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_evidence_json_cli_is_always_non_positive(self):
        evidence = FakeGitHub().collect(REPO, 370).data
        args = ["--repo", REPO, "--pr", "370", "--expected-head", HEAD, "--expected-base", BASE,
                "--required-check", CHECKS[0], "--required-check", CHECKS[1], "--evidence-json", "fixture.json"]
        with patch.object(guard.Path, "read_text", return_value=json.dumps(evidence)), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(guard.main(args), 2)
            self.assertNotEqual(json.loads(output.getvalue())["state"], "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_old_summary_same_short_prefix_does_not_bind_new_head(self):
        client = FakeGitHub()
        client.resolved = HEAD[:7] + "0" * 33
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_earlier_cycle_summary_cannot_mix_with_new_review(self):
        client = FakeGitHub()
        client.comments[0]["body"] = summary(completed="2026-10-01T16:10:00Z")
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_later_manual_request_invalidates_old_terminal_row(self):
        client = FakeGitHub()
        client.comments[0]["body"] = summary(trigger="Manual request")
        client.comments.append({"id": 52, "user": {"login": "mglpsw"}, "body": "@codex review",
                                "created_at": "2026-10-01T16:25:00Z", "updated_at": "2026-10-01T16:25:00Z"})
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_manual_request_supersedes_completed_ready_trigger(self):
        client = FakeGitHub()
        client.comments.append({"id": 52, "user": {"login": "mglpsw"}, "body": "@codex review",
                                "created_at": "2026-10-01T16:25:00Z", "updated_at": "2026-10-01T16:25:00Z"})
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_ambiguous_terminal_reviews_cannot_bind_summary(self):
        client = FakeGitHub()
        client.reviews.append(dict(client.reviews[0], id=42))
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_known_boilerplate_only_is_not_material_but_hidden_finding_is(self):
        client = FakeGitHub()
        client.reviews[0]["body"] = guard.CODEX_BOILERPLATE
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        client.reviews[0]["body"] += "<details><summary>Finding</summary>Material defect</details>"
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_WITH_MATERIAL_FINDINGS")

    def test_missing_authentication_or_transport_failure_is_unavailable(self):
        client = FakeGitHub()
        client.token = ""
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")
        client.token = "synthetic-test-token"
        with patch.object(client, "_request", side_effect=RuntimeError("unauthorized")):
            self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_raw_malformed_collections_are_unavailable(self):
        for channel in ("reviews", "inline", "comments", "timeline", "checks"):
            for member in (None, {}):
                client = FakeGitHub()
                setattr(client, channel, [member])
                with self.subTest(channel=channel, member=member):
                    self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_duplicate_check_run_identity_is_unavailable(self):
        client = FakeGitHub()
        client.checks[1]["id"] = client.checks[0]["id"]
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_check_run_later_page_is_collected(self):
        client = FakeGitHub()
        client.checks = [dict(client.checks[0], id=i+100, name=f"unrelated-{i}") for i in range(100)] + client.checks
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        self.assertTrue(any("check-runs?per_page=100&page=2" in path for path in client.paths))

    def test_check_completeness_mismatch_is_unavailable(self):
        client = FakeGitHub()
        original = client._request
        def truncated(method, path, payload=None):
            result = original(method, path, payload)
            if "/check-runs?" in path:
                result["total_count"] += 1
            return result
        client._request = truncated
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_ready_event_null_member_is_structured_unavailable(self):
        live = FakeGitHub().collect(REPO, 370)
        live.data["ready_events"] = [None]
        self.assertEqual(evaluate(live).state, "HELD_CODEX_UNAVAILABLE")

    def test_all_collection_null_or_non_object_members_are_unavailable(self):
        for key in guard.EVIDENCE_COLLECTIONS:
            for malformed in (None, [None], [{}]):
                live = FakeGitHub().collect(REPO, 370)
                live.data[key] = malformed
                with self.subTest(collection=key, value=malformed):
                    self.assertEqual(evaluate(live).state, "HELD_CODEX_UNAVAILABLE")

    def test_raw_null_nested_user_or_app_is_structured_unavailable(self):
        for channel in ("reviews", "comments", "checks"):
            client = FakeGitHub()
            getattr(client, channel)[0]["app" if channel == "checks" else "user"] = None
            self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_body_only_finding_has_stable_id_and_authenticated_channel(self):
        client = FakeGitHub()
        client.reviews[0]["body"] = "A material general finding"
        first = client.collect(REPO, 370)
        finding_id = first.data["findings"][0]["id"]
        self.assertTrue(finding_id.startswith("review-body:41:"))
        self.assertEqual(evaluate(first).state, "HELD_WITH_MATERIAL_FINDINGS")
        client.comments.append(disposition(finding_id, state="DISMISSED"))
        self.assertEqual(evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",))).state,
                         "READY_FOR_HUMAN_INTEGRATION_DECISION")

    def test_untrusted_or_quoted_body_disposition_is_not_accepted(self):
        for untrusted in (True, False):
            client = FakeGitHub()
            client.predecessor(body=True)
            finding = client.collect(REPO, 370).data["findings"][0]["id"]
            comment = disposition(finding, author="random-user" if untrusted else "mglpsw", state="DISMISSED")
            if not untrusted:
                comment["body"] = "> " + comment["body"]
            client.comments.append(comment)
            self.assertEqual(evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",))).state,
                             "HELD_WITH_MATERIAL_FINDINGS")

    def test_predecessor_identical_bytes_remain_blocking(self):
        client = FakeGitHub()
        client.predecessor()
        client.blobs[HEAD] = client.blobs[OLD]
        result = evaluate(client.collect(REPO, 370))
        self.assertEqual(result.state, "HELD_WITH_MATERIAL_FINDINGS")
        self.assertEqual(result.evidence["open_findings"][0]["applicability"], "byte_identical")

    def test_predecessor_changed_bytes_alone_do_not_prove_repair(self):
        client = FakeGitHub()
        client.predecessor()
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_WITH_MATERIAL_FINDINGS")

    def test_causal_repair_with_authorized_evidence_and_new_review_closes_predecessor(self):
        client = FakeGitHub()
        client.predecessor()
        client.comments.append(disposition("61"))
        result = evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",)))
        self.assertEqual(result.state, "READY_FOR_HUMAN_INTEGRATION_DECISION")
        client.blobs[HEAD] = client.blobs[OLD]  # removing the causal byte change restores the blocker
        self.assertEqual(evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",))).state,
                         "HELD_WITH_MATERIAL_FINDINGS")

    def test_repair_without_new_exact_head_review_is_pending(self):
        client = FakeGitHub()
        client.predecessor()
        client.reviews.pop()
        client.comments.append(disposition("61"))
        self.assertEqual(evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",))).state, "HELD_PENDING_CODEX")

    def test_collection_race_is_unavailable(self):
        client = FakeGitHub()
        client.drift = True
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_CODEX_UNAVAILABLE")

    def test_running_summary_is_pending(self):
        client = FakeGitHub()
        client.comments[0]["body"] = summary().replace("✅ **Completed**", "🔄 **Running**")
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_ci_green_without_terminal_review_is_pending(self):
        client = FakeGitHub()
        client.reviews = []
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_CODEX")

    def test_merged_or_draft_lifecycle_never_ready(self):
        for field, value, expected in (("merged", True, "HELD_STALE"), ("draft", True, "HELD_PENDING_CODEX")):
            client = FakeGitHub()
            client.pr[field] = value
            self.assertEqual(evaluate(client.collect(REPO, 370)).state, expected)

    def test_later_ready_cycle_invalidates_previous_review(self):
        client = FakeGitHub()
        client.timeline.append({"id": 32, "event": "ready_for_review", "created_at": "2026-10-01T16:25:00Z"})
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_STALE")

    def test_checks_wrong_producer_or_duplicate_are_not_ready(self):
        client = FakeGitHub()
        client.checks[0]["app"]["slug"] = "untrusted-app"
        self.assertEqual(evaluate(client.collect(REPO, 370)).state, "HELD_PENDING_REQUIRED_CI")

    def test_old_disposition_subject_does_not_close_current_finding(self):
        client = FakeGitHub()
        client.predecessor()
        client.comments.append(disposition("61", subject=OLD))
        self.assertEqual(evaluate(client.collect(REPO, 370, trusted_adjudicators=("mglpsw",))).state,
                         "HELD_WITH_MATERIAL_FINDINGS")


def _content_error(client, *, ref=HEAD, status=404):
    """Exercise production HTTP classification through the synthetic transport."""
    from urllib.error import HTTPError
    request = client._request

    def missing(method, path, payload=None):
        if '/contents/' in path and parse_qs(urlsplit(path).query)['ref'][0] == ref:
            with patch.object(guard, 'urlopen', side_effect=HTTPError(
                    'https://api.github.com' + path, status, 'content unavailable', {}, None)):
                return guard.GitHubReadOnlyClient._request(client, method, path, payload)
        return request(method, path, payload)

    client._request = missing
    return client


def test_deleted_or_renamed_predecessor_path_allows_authorized_disposition():
    for state in ('FIXED', 'DISMISSED', 'SUPERSEDED'):
        client = FakeGitHub()
        client.predecessor()
        client.comments.append(disposition('61', state=state))
        live = _content_error(client).collect(REPO, 370, trusted_adjudicators=('mglpsw',))
        assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
        finding = live.data['findings'][0]
        assert finding['affected_blob_current'] is None
        assert finding['applicability'] == 'changed_requires_disposition'
        assert finding['disposition_verified'] is True


def test_missing_current_path_remains_material_without_disposition():
    client = FakeGitHub()
    client.predecessor()
    assert evaluate(_content_error(client).collect(REPO, 370)).state == 'HELD_WITH_MATERIAL_FINDINGS'


def test_path_auth_failure_or_missing_finding_time_blob_is_unavailable():
    for ref, status in ((HEAD, 403), (OLD, 404)):
        client = FakeGitHub()
        client.predecessor()
        client.comments.append(disposition('61', state='DISMISSED'))
        live = _content_error(client, ref=ref, status=status).collect(REPO, 370, trusted_adjudicators=('mglpsw',))
        assert evaluate(live).state == 'HELD_CODEX_UNAVAILABLE'


def test_bot_reply_is_not_a_finding_and_replies_still_carry_dispositions():
    client = FakeGitHub()
    client.predecessor()
    client.inline.append({'id': 62, 'user': BOT, 'body': 'Acknowledged, thanks',
                          'path': 'scripts/example.py', 'pull_request_review_id': 41,
                          'commit_id': HEAD, 'in_reply_to_id': 61})
    live = client.collect(REPO, 370, trusted_adjudicators=('mglpsw',))
    assert [f['id'] for f in live.data['findings']] == [61]
    assert evaluate(live).state == 'HELD_WITH_MATERIAL_FINDINGS'
    reply = disposition('61')
    reply['in_reply_to_id'] = 61
    client.inline.append(reply)
    assert evaluate(client.collect(REPO, 370, trusted_adjudicators=('mglpsw',))).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    client.inline.append({'id': 63, 'user': BOT, 'body': 'A new root finding',
                          'path': 'scripts/example.py', 'pull_request_review_id': 41,
                          'commit_id': HEAD})
    held = evaluate(client.collect(REPO, 370, trusted_adjudicators=('mglpsw',)))
    assert held.state == 'HELD_WITH_MATERIAL_FINDINGS'
    assert [f['id'] for f in held.evidence['open_findings']] == [63]


def test_untrusted_manual_command_cannot_supersede_with_explicit_requester_policy():
    client = FakeGitHub()
    client.comments.append({'id': 52, 'user': {'login': 'untrusted-participant'},
                            'body': '@codex review', 'created_at': '2026-10-01T16:25:00Z',
                            'updated_at': '2026-10-01T16:25:00Z'})
    assert evaluate(client.collect(REPO, 370, trusted_requesters=('mglpsw',))).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    # Unknown requester policy is held, rather than borrowing adjudicator authority.
    assert evaluate(client.collect(REPO, 370, trusted_adjudicators=('mglpsw',))).state == 'HELD_PENDING_CODEX'


def test_authorized_manual_request_supersedes_and_requires_its_own_terminal():
    client = FakeGitHub()
    client.comments.append({'id': 52, 'user': {'login': 'mglpsw'}, 'body': '@codex review',
                            'created_at': '2026-10-01T16:25:00Z', 'updated_at': '2026-10-01T16:25:00Z'})
    assert evaluate(client.collect(REPO, 370, trusted_requesters=('mglpsw',))).state == 'HELD_PENDING_CODEX'
    client.comments[0]['body'] = summary(completed='2026-10-01T16:26:04Z', trigger='Manual request')
    client.comments[0]['updated_at'] = '2026-10-01T16:26:05Z'
    client.reviews[0]['submitted_at'] = '2026-10-01T16:26:01Z'
    assert evaluate(client.collect(REPO, 370, trusted_requesters=('mglpsw',))).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_requester_authority_does_not_authorize_finding_disposition():
    client = FakeGitHub()
    client.predecessor()
    client.comments[0]['body'] = summary(trigger='Manual request')
    client.comments.extend([
        {'id': 52, 'user': {'login': 'operator'}, 'body': '@codex review',
         'created_at': '2026-10-01T16:17:00Z', 'updated_at': '2026-10-01T16:17:00Z'},
        disposition('61', author='operator')])
    live = client.collect(REPO, 370, trusted_requesters=('operator',), trusted_adjudicators=('mglpsw',))
    assert evaluate(live).state == 'HELD_WITH_MATERIAL_FINDINGS'
    client.comments.append(dict(disposition('61'), id=72))
    live = client.collect(REPO, 370, trusted_requesters=('operator',), trusted_adjudicators=('mglpsw',))
    assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_delayed_terminal_summary_requires_unique_review_inside_current_cycle():
    client = FakeGitHub()
    client.comments[0]['body'] = summary(completed='2026-10-01T16:25:23Z')
    client.comments[0]['updated_at'] = '2026-10-01T16:25:24Z'
    assert evaluate(client.collect(REPO, 370)).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    client.reviews.append(dict(client.reviews[0], id=42, submitted_at='2026-10-01T16:24:00Z'))
    assert evaluate(client.collect(REPO, 370)).state == 'HELD_PENDING_CODEX'
    client.reviews = [dict(client.reviews[0], submitted_at='2026-10-01T16:00:00Z')]
    assert evaluate(client.collect(REPO, 370)).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def _collect(client):
    return client.collect(REPO, 370, trusted_adjudicators=('mglpsw',), trusted_requesters=('mglpsw',))


def _pending(client, *, bot=False, missing=False):
    review = dict(id=42, state='PENDING', submitted_at=None, commit_id=HEAD,
                  user=BOT if bot else {'login': 'human-reviewer'}, body='Unsubmitted draft finding')
    if missing:
        review.pop('submitted_at')
    client.reviews.append(review)
    client.inline.append({'id': 62, 'user': review['user'], 'body': 'Unsubmitted inline draft',
                          'path': 'scripts/example.py', 'pull_request_review_id': 42,
                          'commit_id': HEAD})


def _clean_client():
    client = FakeGitHub()
    client.reviews = []
    client.reactions = [{'id': 81, 'user': BOT, 'content': '+1',
                         'created_at': '2026-10-01T16:21:24Z'}]
    return client


def _repair_client(*, body=False, repair='1' * 40, relation='ahead', current_relation='ahead'):
    client = FakeGitHub()
    client.predecessor(body=body)
    client.blobs[repair] = 'c' * 40 if repair not in (OLD, HEAD) else client.blobs[repair]
    client.comparisons[(OLD, repair)] = relation
    client.comparisons[(repair, HEAD)] = current_relation
    finding = '61'
    if body:
        residual = guard._review_body_residual(client.reviews[0]['body'])
        finding = 'review-body:40:' + guard.hashlib.sha256(residual.encode()).hexdigest()
    comment = disposition(finding)
    comment['body'] = comment['body'].replace('Repair-Commit: ' + HEAD, 'Repair-Commit: ' + repair)
    client.comments.append(comment)
    return client


def test_pending_human_null_or_missing_timestamp_does_not_invalidate_completed_cycle():
    for missing in (False, True):
        client = FakeGitHub()
        _pending(client, missing=missing)
        live = _collect(client)
        assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
        assert live.data['findings'] == []
        assert live.data['reviews'][-1]['submitted_at'] is None
        assert live.data['revalidated'] is True


def test_pending_codex_is_not_published_and_cannot_inherit_previous_completion():
    for previous in (False, True):
        client = FakeGitHub()
        _pending(client, bot=True, missing=not previous)
        if not previous:
            client.reviews.pop(0)
        live = _collect(client)
        assert live.data['findings'] == []
        assert evaluate(live).state == 'HELD_PENDING_CODEX'


def test_submitted_reviews_still_require_valid_timestamps_in_collector_and_evaluator():
    for timestamp in (None, '', 'bad', '2026-10-01T16:21:19', 5):
        client = FakeGitHub()
        client.reviews[0]['submitted_at'] = timestamp
        assert evaluate(_collect(client)).state == 'HELD_CODEX_UNAVAILABLE'
        live = _collect(FakeGitHub())
        live.data['reviews'][0]['submitted_at'] = timestamp
        assert evaluate(live).state == 'HELD_CODEX_UNAVAILABLE'
    client = FakeGitHub()
    client.reviews[0].pop('submitted_at')
    assert evaluate(_collect(client)).state == 'HELD_CODEX_UNAVAILABLE'


def test_fixed_requires_strict_finding_to_repair_and_repair_to_head_relations():
    for relation, current_relation in (('behind', 'ahead'), ('diverged', 'ahead'),
                                       ('ahead', 'diverged'), ('ahead', 'behind')):
        for body in (False, True):
            client = _repair_client(body=body, relation=relation, current_relation=current_relation)
            assert evaluate(_collect(client)).state == 'HELD_WITH_MATERIAL_FINDINGS'
    for body in (False, True):
        client = _repair_client(body=body, repair=OLD, relation='identical')
        assert evaluate(_collect(client)).state == 'HELD_WITH_MATERIAL_FINDINGS'
        client = _repair_client(body=body, relation=RuntimeError('ancestry unavailable'))
        assert evaluate(_collect(client)).state == 'HELD_CODEX_UNAVAILABLE'


def test_fixed_uses_immutable_review_subject_and_not_publication_chronology():
    for body in (False, True):
        client = _repair_client(body=body)
        client.reviews[0]['submitted_at'] = '2026-10-01T16:19:00Z'
        request = client._request

        def older_repair_date(method, path, payload=None):
            result = request(method, path, payload)
            if '/compare/' in path:
                result['commits'] = [{'commit': {'author': {'date': '2026-10-01T16:18:00Z'}}}]
            return result

        client._request = older_repair_date
        live = _collect(client)
        assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
        assert live.data['findings'][0]['commit_id'] == OLD
        assert any('/compare/' + OLD + '...' + '1' * 40 in p for p in client.paths)


def test_justified_dismissal_does_not_claim_a_post_finding_repair():
    for body in (False, True):
        client = _repair_client(body=body, repair=OLD, relation='identical')
        client.comments[-1]['body'] = client.comments[-1]['body'].replace(' FIXED\n', ' DISMISSED\n')
        client.comments[-1]['body'] += '\nJustification: proposition rejected by the positive control, no causal repair claimed.'
        assert evaluate(_collect(client)).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_fixture_never_invents_undeclared_ancestry():
    client = FakeGitHub()
    with unittest.TestCase().assertRaisesRegex(AssertionError, 'undeclared commit relation'):
        client._request('GET', '/compare/' + '2' * 40 + '...' + HEAD)


def test_foreign_homonymous_checks_do_not_change_canonical_result_or_order():
    for reverse in (False, True):
        client = FakeGitHub()
        foreign = dict(client.checks[0], id=999, check_suite={'id': 801})
        client.checks.append(foreign)
        if reverse:
            client.checks.reverse()
        live = _collect(client)
        assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
        assert len(live.data['checks']) == 3  # retain foreign evidence for diagnostics
        client.checks = [foreign, next(c for c in client.checks if c['name'] == CHECKS[1])]
        assert evaluate(_collect(client)).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_foreign_green_check_cannot_replace_failed_or_pending_canonical_job():
    for status, conclusion in (('completed', 'failure'), ('in_progress', None)):
        client = FakeGitHub()
        client.checks.append(dict(client.checks[0], id=999, check_suite={'id': 801}))
        client.checks[0].update(status=status, conclusion=conclusion)
        client.jobs[0].update(status=status, conclusion=conclusion)
        assert evaluate(_collect(client)).state == 'HELD_PENDING_REQUIRED_CI'


def test_canonical_duplicate_or_incomplete_suite_provenance_remains_held():
    for suite in ({'id': 800}, None, {}, {'id': '801'}):
        client = FakeGitHub()
        client.checks.append(dict(client.checks[0], id=999, check_suite=suite))
        assert evaluate(_collect(client)).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_clean_connector_completion_without_formal_review_collects_and_binds_reaction():
    client = _clean_client()
    live = _collect(client)
    assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    assert live.data['reviews'] == []
    row = live.data['summaries'][0]
    assert row['bound_reaction_id'] == 81
    assert 'bound_review_id' not in row
    assert row['bound_head_sha'] == HEAD and row['bound_ready_event_id'] == 31
    assert sum('/issues/370/reactions?' in p for p in client.paths) == 2


def test_clean_reaction_requires_new_authenticated_current_completion():
    for defect in ('old', 'before_completion', 'foreign', 'other_connector', 'wrong_head',
                   'running', 'missing_summary', 'missing_reaction', 'ambiguous', 'eyes'):
        client = _clean_client()
        if defect == 'old': client.reactions[0]['created_at'] = '2026-10-01T16:15:00Z'
        if defect == 'before_completion': client.reactions[0]['created_at'] = '2026-10-01T16:20:00Z'
        if defect == 'foreign': client.reactions[0]['user'] = {'login': 'mglpsw'}
        if defect == 'other_connector': client.reactions[0]['user'] = {'login': 'openai-codex[bot]'}
        if defect == 'wrong_head': client.resolved = OLD
        if defect == 'running': client.comments[0]['body'] = summary().replace('Completed', 'Running')
        if defect == 'missing_summary': client.comments = []
        if defect == 'missing_reaction': client.reactions = []
        if defect == 'ambiguous': client.reactions.append(dict(client.reactions[0], id=82))
        if defect == 'eyes': client.reactions.append(dict(client.reactions[0], id=82, content='eyes'))
        assert evaluate(_collect(client)).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION', defect


def test_clean_reaction_cannot_close_findings_or_survive_a_new_required_cycle():
    client = _clean_client()
    client.predecessor()
    assert evaluate(_collect(client)).state == 'HELD_WITH_MATERIAL_FINDINGS'
    client.comments.append(disposition('61'))
    assert evaluate(_collect(client)).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    client.comments.append({'id': 52, 'user': {'login': 'mglpsw'}, 'body': '@codex review',
                            'created_at': '2026-10-01T16:22:00Z', 'updated_at': '2026-10-01T16:22:00Z'})
    assert evaluate(_collect(client)).state == 'HELD_PENDING_CODEX'
    client = _clean_client()
    _pending(client, bot=True)
    assert evaluate(_collect(client)).state == 'HELD_PENDING_CODEX'


def test_clean_reaction_target_and_shape_are_checked_and_surface_revalidated():
    for defect in ('foreign_target', 'bad_time', 'drift'):
        client = _clean_client()
        if defect == 'bad_time': client.reactions[0]['created_at'] = None
        if defect == 'drift':
            request = client._request
            reads = []

            def changing(method, path, payload=None):
                result = request(method, path, payload)
                if '/reactions?' in path:
                    reads.append(path)
                    if len(reads) > 1: result = []
                return result

            client._request = changing
        live = _collect(client)
        if defect == 'foreign_target': live.data['reactions'][0]['target_pr'] = 371
        assert evaluate(live).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION'


def test_clean_reaction_later_page_and_reused_summary_container_are_observed():
    client = _clean_client()
    client.reactions = [dict(client.reactions[0], id=1000+i, content='heart',
                             user={'login': 'unrelated'}) for i in range(100)] + client.reactions
    assert evaluate(_collect(client)).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    assert any('/reactions?per_page=100&page=2' in p for p in client.paths)
    client.timeline.append({'id': 32, 'event': 'ready_for_review', 'created_at': '2026-10-01T16:22:00Z'})
    assert evaluate(_collect(client)).state != 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    client.comments[0]['body'] = summary(completed='2026-10-01T16:23:00Z')
    client.comments[0]['updated_at'] = '2026-10-01T16:23:01Z'
    client.reactions[-1]['created_at'] = '2026-10-01T16:23:01Z'
    live = _collect(client)
    assert evaluate(live).state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    assert live.data['summaries'][0]['id'] == 51 and live.data['summaries'][0]['bound_ready_event_id'] == 32


def test_composed_clean_path_requires_completion_ancestry_and_canonical_job():
    for defect in (None, 'conclusion', 'ancestry', 'job'):
        client = _repair_client()
        client.reviews.pop()  # current cycle has no formal review
        client.reactions = _clean_client().reactions
        _pending(client)  # a legitimate unrelated human draft
        client.checks.append(dict(client.checks[0], id=999, check_suite={'id': 801}))
        if defect == 'conclusion': client.comments[0]['body'] = summary().replace('Completed', 'Running')
        if defect == 'ancestry': client.comparisons[(OLD, '1' * 40)] = 'behind'
        if defect == 'job': client.checks.pop(0)
        live = _collect(client)
        state = evaluate(live).state
        assert (state == 'READY_FOR_HUMAN_INTEGRATION_DECISION') == (defect is None), (defect, state)


def test_historical_371_connector_format_with_synthetic_sufficient_lifecycle_and_ci():
    # Historical connector values, synthetic unmerged lifecycle/CI. #371 is merged:
    # this fixture never represents a LIVE-ready replay or promotion of that PR.
    client = _clean_client()
    head = '420bf76c15a76eaf14ee333e4ce52e9ef0d372b0'
    base = 'ea6a6584b7a4735a753e9a2b61f8f96cb4e3494f'
    client.pr.update(number=371, head={'sha': head}, base={'sha': base, 'repo': {'full_name': REPO}})
    client.run['head_sha'] = head
    client.run['pull_requests'] = [{'number': 371, 'head': {'sha': head}, 'base': {'sha': base},
                                    'url': f'https://api.github.com/repos/{REPO}/pulls/371'}]
    for check in client.checks + client.jobs:
        check['head_sha'] = head
    client.resolved = head
    client.timeline = [{'id': 32308791745, 'event': 'ready_for_review',
                        'created_at': '2026-10-02T04:23:12Z'}]
    client.comments[0].update(id=5945118945, body=summary(ref='420bf76', completed='2026-10-02T04:26:55.420911Z'),
                              created_at='2026-10-02T03:33:33Z', updated_at='2026-10-02T04:26:58Z')
    client.reactions[0].update(id=540159791, created_at='2026-10-02T04:27:00Z')
    live = client.collect(REPO, 371, trusted_requesters=('mglpsw',))
    result = guard.evaluate_evidence(live, expected_repo=REPO, expected_pr=371,
                                     expected_head=head, expected_base=base, required_checks=CHECKS)
    assert result.state == 'READY_FOR_HUMAN_INTEGRATION_DECISION'
    assert result.merge_authorized is False
    assert live.data['reviews'] == []
