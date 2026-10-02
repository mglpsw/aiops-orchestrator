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
        if route.endswith("/pulls/370"):
            self.pr_reads += 1
            result = deepcopy(self.pr)
            if self.drift and self.pr_reads > 1:
                result["head"]["sha"] = OLD
            return result
        if "/contents/" in route:
            return {"type": "file", "sha": self.blobs[params["ref"][0]]}
        if "/compare/" in route:
            return {"status": "identical" if route.endswith(HEAD + "..." + HEAD) else "ahead"}
        if route.endswith("/check-runs"):
            page = int(params["page"][0])
            return {"total_count": len(self.checks), "check_runs": deepcopy(self.checks[(page-1)*100:page*100])}
        if "/commits/" in route:
            return {"sha": self.resolved}
        collection = None
        if route.endswith("/pulls/370/reviews"):
            collection = self.reviews
        elif route.endswith("/pulls/370/comments"):
            collection = self.inline
        elif route.endswith("/issues/370/comments"):
            collection = self.comments
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
