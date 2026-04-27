#!/usr/bin/env python3
"""Deterministic on-demand PR review for `/agent review`."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_API_BASE_URL = "https://api.github.com"
AGENT_COMMAND = "/agent review"
ALLOWED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAX_RECENT_COMMENTS = 10

P1_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(?i)\bpull_request_target\b"), "pull_request_target", "Use `pull_request` instead of `pull_request_target` in this on-demand review path."),
    (re.compile(r"(?i)\bdocker\s+compose\s+(?:-f\s+\S+\s+)*down\b"), "docker compose down", "Remove destructive Docker commands from automation."),
    (re.compile(r"(?i)\bdocker\s+compose\s+(?:-f\s+\S+\s+)*restart\b"), "docker compose restart", "Keep the review path read-only."),
    (re.compile(r"(?i)\bdocker\s+stop\b"), "docker stop", "Do not stop containers automatically."),
    (re.compile(r"(?i)\bdocker\s+rm\b"), "docker rm", "Do not remove containers automatically."),
    (re.compile(r"(?i)\bdocker\s+kill\b"), "docker kill", "Do not kill containers automatically."),
    (re.compile(r"(?i)\bdocker\s+exec\b"), "docker exec", "Do not execute inside containers from this workflow."),
    (re.compile(r"(?i)\bsystemctl\s+restart\b"), "systemctl restart", "Do not restart services automatically."),
    (re.compile(r"(?i)\bservice\s+docker\b"), "service docker", "Do not manage the Docker daemon here."),
    (re.compile(r"(?i)\bgit\s+push\b"), "git push", "Do not publish changes from automation."),
    (re.compile(r"(?i)\bgit\s+pull\b"), "git pull", "Do not mutate repository state from automation."),
    (re.compile(r"(?i)\brm\s+-rf\b"), "rm -rf", "Remove destructive filesystem commands."),
    (re.compile(r"(?i)\bchmod\s+777\b"), "chmod 777", "Avoid world-writable permissions."),
    (re.compile(r"(?i)\bcurl\b.*\|\s*(?:bash|sh|zsh)\b"), "curl | bash", "Replace with explicit download and verification steps."),
    (re.compile(r"(?i)\bssh\s+root\b"), "ssh root", "Do not add privileged remote shell access."),
)

SECRET_RULES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9-]{8,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|pwd|client_secret)\b\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
)


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None


@dataclass(frozen=True)
class CheckSummary:
    name: str
    conclusion: str | None
    status: str | None
    url: str | None = None


@dataclass(frozen=True)
class Finding:
    severity: str
    file: str
    evidence: str
    risk: str
    recommendation: str


@dataclass
class ReviewContext:
    owner: str
    repo: str
    issue_number: int
    author: str
    association: str | None
    pr_number: int | None
    title: str | None
    body: str | None
    base_ref: str | None
    head_ref: str | None
    head_sha: str | None
    html_url: str | None
    files: list[FileChange] = field(default_factory=list)
    checks: list[CheckSummary] = field(default_factory=list)
    recent_comments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReviewReport:
    status: str
    pr_number: int | None
    author: str
    base_ref: str | None
    head_ref: str | None
    commit: str | None
    summary: str
    findings: list[Finding]
    checks: list[CheckSummary]
    files: list[FileChange]
    pr_executed: bool = False

    def to_markdown(self) -> str:
        lines = [
            "# Agent Review",
            "",
            "## Resultado",
            f"- Status: {self.status}",
            f"- PR: {f'#{self.pr_number}' if self.pr_number is not None else 'n/a'}",
            f"- Autor: {self.author}",
            f"- Base: {self.base_ref or 'n/a'}",
            f"- Head: {self.head_ref or 'n/a'}",
            f"- Commit analisado: {self.commit or 'n/a'}",
            "- Modo: deterministic review",
            f"- Código do PR executado: {'sim' if self.pr_executed else 'não'}",
            "",
            "## Resumo",
            self.summary or "Sem resumo disponível.",
            "",
            "## Achados",
            "",
            "### P1 — Bloqueadores",
            self._render_findings("P1"),
            "",
            "### P2 — Importantes",
            self._render_findings("P2"),
            "",
            "### P3 — Sugestões",
            self._render_findings("P3"),
            "",
            "## CI / Checks",
            self._render_checks(),
            "",
            "## Arquivos analisados",
            self._render_files_table(),
            "",
            "## Nota de segurança",
            "Este agent analisou metadados e diff via GitHub API. Ele não executou código do PR, não fez checkout da branch do PR para execução e não teve permissão de deploy.",
        ]
        return "\n".join(lines).rstrip() + "\n"

    def _render_findings(self, severity: str) -> str:
        items = [finding for finding in self.findings if finding.severity == severity]
        if not items:
            return f"- Nenhum {severity} encontrado."
        lines: list[str] = []
        for finding in items:
            lines.extend(
                [
                    f"- Severidade: {finding.severity}",
                    f"  Arquivo: {finding.file}",
                    f"  Evidência: {finding.evidence}",
                    f"  Risco: {finding.risk}",
                    f"  Recomendação: {finding.recommendation}",
                ]
            )
        return "\n".join(lines)

    def _render_checks(self) -> str:
        if not self.checks:
            return "- Nenhum check encontrado."
        lines: list[str] = []
        for check in self.checks:
            state = check.conclusion or check.status or "unknown"
            link = f" ({check.url})" if check.url else ""
            lines.append(f"- {check.name}: {state}{link}")
        return "\n".join(lines)

    def _render_files_table(self) -> str:
        rows = ["| Arquivo | Status | + | - | Observação |", "|---|---|---:|---:|---|"]
        for file_change in self.files:
            note = "patch indisponível" if not file_change.patch else "patch analisado"
            rows.append(
                f"| {file_change.path} | {file_change.status} | {file_change.additions} | {file_change.deletions} | {note} |"
            )
        return "\n".join(rows)


class GitHubClient:
    def __init__(self, token: str, repository: str, api_base_url: str | None = None) -> None:
        self.token = token
        self.repository = repository
        self.api_base_url = (api_base_url or os.getenv("GITHUB_API_URL") or DEFAULT_API_BASE_URL).rstrip("/")

    def get_json(self, path: str) -> Any:
        return self._request("GET", path)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aiops-orchestrator-agent-review/1.0",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"GitHub API request failed: {method} {path} -> {exc.code} {body}") from exc


def _load_event_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _command_lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def is_agent_review_command(body: str) -> bool:
    return any(re.match(r"^/agent review(?:\s+.*)?$", line) for line in _command_lines(body))


def _allowed_users_from_env(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_authorized(association: str | None, login: str | None, allowed_users: str | None) -> bool:
    if association and association.upper() in ALLOWED_ASSOCIATIONS:
        return True
    return bool(login and login.lower() in _allowed_users_from_env(allowed_users))


def is_pull_request_payload(payload: dict[str, Any]) -> bool:
    issue = payload.get("issue")
    return isinstance(issue, dict) and isinstance(issue.get("pull_request"), dict)


def _repo_parts(repository: str) -> tuple[str, str]:
    if "/" not in repository:
        raise ValueError("GITHUB_REPOSITORY must be in owner/repo form")
    return repository.split("/", 1)


def _paginate(client: GitHubClient, path: str) -> list[dict[str, Any]]:
    page = 1
    items: list[dict[str, Any]] = []
    while True:
        separator = "&" if "?" in path else "?"
        response = client.get_json(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(response, list):
            break
        items.extend(item for item in response if isinstance(item, dict))
        if len(response) < 100:
            break
        page += 1
    return items


def fetch_review_context(client: GitHubClient, payload: dict[str, Any]) -> ReviewContext:
    owner, repo = _repo_parts(client.repository)
    issue = payload["issue"]
    comment = payload["comment"]
    issue_number = int(issue["number"])
    author = str(comment.get("user", {}).get("login", "unknown"))
    association = comment.get("author_association")

    if not is_pull_request_payload(payload):
        return ReviewContext(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            author=author,
            association=association,
            pr_number=None,
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            base_ref=None,
            head_ref=None,
            head_sha=None,
            html_url=str(issue.get("html_url") or ""),
        )

    pull_number = issue_number
    pr = client.get_json(f"/repos/{owner}/{repo}/pulls/{pull_number}")
    if not isinstance(pr, dict):
        raise RuntimeError("Unexpected PR payload from GitHub API")
    files = [
        FileChange(
            path=str(item.get("filename") or item.get("path") or ""),
            status=str(item.get("status") or "unknown"),
            additions=int(item.get("additions") or 0),
            deletions=int(item.get("deletions") or 0),
            patch=item.get("patch"),
        )
        for item in _paginate(client, f"/repos/{owner}/{repo}/pulls/{pull_number}/files")
    ]
    checks: list[CheckSummary] = []
    try:
        head_sha = str(pr.get("head", {}).get("sha") or "")
        check_runs = client.get_json(f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs")
        if isinstance(check_runs, dict):
            for item in check_runs.get("check_runs") or []:
                if not isinstance(item, dict):
                    continue
                checks.append(
                    CheckSummary(
                        name=str(item.get("name") or "check"),
                        conclusion=item.get("conclusion"),
                        status=item.get("status"),
                        url=item.get("html_url"),
                    )
                )
    except RuntimeError:
        pass
    recent_comments = _paginate(client, f"/repos/{owner}/{repo}/issues/{issue_number}/comments")[-MAX_RECENT_COMMENTS:]
    return ReviewContext(
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        author=author,
        association=association,
        pr_number=int(pr.get("number") or pull_number),
        title=str(pr.get("title") or issue.get("title") or ""),
        body=str(pr.get("body") or issue.get("body") or ""),
        base_ref=str(pr.get("base", {}).get("ref") or ""),
        head_ref=str(pr.get("head", {}).get("ref") or ""),
        head_sha=str(pr.get("head", {}).get("sha") or ""),
        html_url=str(pr.get("html_url") or issue.get("html_url") or ""),
        files=files,
        checks=checks,
        recent_comments=recent_comments,
    )


def _patch_evidence(patch: str, pattern: re.Pattern[str]) -> str:
    for line in patch.splitlines():
        if pattern.search(line):
            return line.strip()
    return pattern.pattern


def _is_p2_path_hardcoded(file_change: FileChange) -> bool:
    return file_change.path.startswith("tests/") or file_change.path.startswith(".github/")


def classify_findings(files: list[FileChange]) -> list[Finding]:
    findings: list[Finding] = []
    has_tests = any(file.path.startswith("tests/") for file in files)
    has_docs = any(file.path.startswith("docs/") for file in files)
    for file_change in files:
        patch = file_change.patch or ""
        patch_lower = patch.lower()
        path_lower = file_change.path.lower()

        for pattern, label, recommendation in P1_RULES:
            if pattern.search(patch):
                findings.append(
                    Finding(
                        severity="P1",
                        file=file_change.path,
                        evidence=f"{label}: {_patch_evidence(patch, pattern)}",
                        risk="A alteração pode executar, destruir ou ampliar o blast radius do fluxo automatizado.",
                        recommendation=recommendation,
                    )
                )

        if any(pattern.search(patch) for pattern in SECRET_RULES):
            findings.append(
                Finding(
                    severity="P1",
                    file=file_change.path,
                    evidence="Secret hardcoded detectado no patch.",
                    risk="Credenciais embutidas podem vazar em diffs, logs ou artefatos.",
                    recommendation="Remova o segredo do código e use secrets do ambiente ou um secret manager.",
                )
            )

        if "/opt/aiops-orchestrator" in patch and _is_p2_path_hardcoded(file_change):
            findings.append(
                Finding(
                    severity="P2",
                    file=file_change.path,
                    evidence="Path hardcoded `/opt/aiops-orchestrator` em teste/CI.",
                    risk="Paths fixos quebram portabilidade e causam regressão em CI/GitHub Actions.",
                    recommendation="Use o helper de settings ou o root configurado pelo ambiente.",
                )
            )

        if ("subprocess.run(" in patch or "subprocess.call(" in patch) and "timeout=" not in patch:
            findings.append(
                Finding(
                    severity="P2",
                    file=file_change.path,
                    evidence="subprocess sem timeout no patch.",
                    risk="Chamadas sem timeout podem travar o workflow on-demand ou a suíte de testes.",
                    recommendation="Defina timeout explícito e trate a falha de forma fail-closed.",
                )
            )

        if file_change.path.startswith(".github/workflows/"):
            if "pull_request_target" in patch_lower:
                findings.append(
                    Finding(
                        severity="P1",
                        file=file_change.path,
                        evidence="Workflow usa `pull_request_target`.",
                        risk="Esse gatilho aumenta o blast radius do token do workflow.",
                        recommendation="Prefira `pull_request` e mantenha a execução fora do código do PR.",
                    )
                )
            if not has_tests and not has_docs:
                findings.append(
                    Finding(
                        severity="P2",
                        file=file_change.path,
                        evidence="Workflow alterado sem cobertura visível em tests/ ou docs/ no mesmo PR.",
                        risk="Mudanças em CI sem validação/documentação aumentam chance de regressão silenciosa.",
                        recommendation="Adicione testes ou documentação que cubram o novo contrato.",
                    )
                )

        if path_lower.endswith((".yml", ".yaml")) and "permissions:" in patch_lower and "write-all" in patch_lower:
            findings.append(
                Finding(
                    severity="P1",
                    file=file_change.path,
                    evidence="Workflow permissive permissions detected.",
                    risk="Permissões amplas em workflow podem expor secrets e elevar privilégio desnecessariamente.",
                    recommendation="Reduza as permissões ao mínimo necessário.",
                )
            )

    return findings


def _check_is_failing(check: CheckSummary) -> bool:
    status = (check.conclusion or check.status or "").lower()
    return status in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}


def _review_status(findings: list[Finding], checks: list[CheckSummary]) -> str:
    if any(_check_is_failing(check) for check in checks) or any(finding.severity == "P1" for finding in findings):
        return "changes_requested"
    if any(finding.severity == "P2" for finding in findings):
        return "attention_needed"
    return "approved"


def _summary_from_context(context: ReviewContext) -> str:
    files = ", ".join(file.path for file in context.files[:5]) or "sem arquivos identificados"
    title = context.title or "PR sem título"
    return f"{title}. Arquivos principais: {files}."


def build_review_report(context: ReviewContext) -> ReviewReport:
    findings = classify_findings(context.files)
    return ReviewReport(
        status=_review_status(findings, context.checks),
        pr_number=context.pr_number,
        author=context.author,
        base_ref=context.base_ref,
        head_ref=context.head_ref,
        commit=context.head_sha,
        summary=_summary_from_context(context),
        findings=findings,
        checks=context.checks,
        files=context.files,
        pr_executed=False,
    )


def _repo_path(repository: str, issue_number: int) -> str:
    owner, repo = _repo_parts(repository)
    return f"/repos/{owner}/{repo}/issues/{issue_number}/comments"


def _post_comment(client: GitHubClient, issue_number: int, body: str) -> None:
    client.post_json(_repo_path(client.repository, issue_number), {"body": body})


def _build_unauthorized_message() -> str:
    return "Agent review não foi executado por falta de autorização."


def _build_issue_message() -> str:
    return "Esta primeira versão do agent review suporta apenas Pull Requests. Futuramente, podemos adicionar `/agent summarize` para issues comuns."


def main() -> int:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    event_path = os.getenv("GITHUB_EVENT_PATH", "").strip()
    allowed_users = os.getenv("AGENT_ALLOWED_USERS", "")
    if not token or not repository or not event_path:
        raise SystemExit("Missing required GitHub environment variables.")

    payload = _load_event_payload(event_path)
    comment = payload.get("comment") or {}
    body = str(comment.get("body") or "")
    if not is_agent_review_command(body):
        return 0

    issue = payload.get("issue") or {}
    issue_number = int(issue.get("number") or 0)
    user = comment.get("user") or {}
    association = comment.get("author_association")
    login = str(user.get("login") or "")
    client = GitHubClient(token=token, repository=repository)

    if not is_authorized(association, login, allowed_users):
        _post_comment(client, issue_number, _build_unauthorized_message())
        return 0

    if not is_pull_request_payload(payload):
        _post_comment(client, issue_number, _build_issue_message())
        return 0

    context = fetch_review_context(client, payload)
    report = build_review_report(context)
    _post_comment(client, issue_number, report.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
