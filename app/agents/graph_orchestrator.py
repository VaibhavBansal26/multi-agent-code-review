"""LangGraph-based orchestrator for MACRV review pipeline."""
from __future__ import annotations

import concurrent.futures
import operator
from typing import Annotated, TypedDict

import structlog
from langgraph.graph import END, StateGraph

from app.agents.classifier import ClassifierAgent
from app.agents.context_builder import (
    build_convention_hints,
    build_intent_summary,
    build_surrounding_context,
)
from app.agents.file_reviewer import FileFinding, FileReviewAgent, FileReviewResult
from app.agents.security_scanner import SecurityScannerAgent
from app.integrations.email_client import send_critical_finding_email
from app.integrations.github_client import GitHubClient
from app.integrations.slack_client import send_review_notification
from app.integrations.ticket_provider import TicketRequest, get_ticket_provider
from app.reports.generator import generate_report

log = structlog.get_logger(__name__)

_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".otf", ".mp4", ".mp3", ".pdf", ".zip", ".tar",
    ".lock", ".sum",
}
_SKIP_PATHS = {"package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock"}
_MAX_DIFF_CHARS = 12_000
_MAX_WORKERS = 5


class ReviewState(TypedDict):
    repo: str
    pr_number: int
    head_sha: str
    pr_title: str
    author_login: str
    author_email: str | None
    diff_url: str
    files: list
    reviewable_files: list
    pr_detail: dict
    intent_summary: str
    convention_hints: str
    base_ref: str
    all_findings: Annotated[list, operator.add]
    classified_findings: list
    security_scan_done: bool
    decision: str
    report_md: str
    report_json: str
    error: str | None
    status: str


# ── Node functions ─────────────────────────────────────────────────────────────

def fetch_pr(state: ReviewState) -> dict:
    gh = GitHubClient()
    repo, pr_number = state["repo"], state["pr_number"]
    log.info("graph_fetch_pr", repo=repo, pr=pr_number)

    try:
        files = gh.get_pr_files(repo, pr_number)
    except Exception as exc:
        log.error("fetch_files_failed", error=str(exc))
        return {"error": str(exc), "status": "failed", "files": [], "reviewable_files": []}

    reviewable = [
        f for f in files
        if not _should_skip(f.get("filename", "")) and f.get("patch")
    ]

    try:
        pr_detail = gh.get_pr_detail(repo, pr_number)
    except Exception:
        pr_detail = {"title": state["pr_title"], "body": "", "labels": [], "base_branch": "main", "head_branch": ""}

    base_ref = pr_detail.get("base_branch", "main")
    intent_summary = build_intent_summary({**pr_detail, "title": state["pr_title"]})
    all_file_paths = [f.get("filename", "") for f in files]
    convention_hints = build_convention_hints(all_file_paths)

    log.info("graph_files_fetched", total=len(files), reviewable=len(reviewable))
    return {
        "files": files,
        "reviewable_files": reviewable,
        "pr_detail": pr_detail,
        "base_ref": base_ref,
        "intent_summary": intent_summary,
        "convention_hints": convention_hints,
        "all_findings": [],
        "classified_findings": [],
        "security_scan_done": False,
        "error": None,
        "status": "in_progress",
    }


def fan_out_review(state: ReviewState) -> dict:
    """Parallel file review — one FileReviewAgent per file."""
    if state.get("error"):
        return {"all_findings": []}

    reviewable = state["reviewable_files"]
    repo = state["repo"]
    base_ref = state["base_ref"]
    intent_summary = state["intent_summary"]
    convention_hints = state["convention_hints"]
    findings: list[FileFinding] = []

    def _review_one(file_info: dict) -> FileReviewResult:
        filename = file_info.get("filename", "")
        patch = file_info.get("patch", "") or ""
        if len(patch) > _MAX_DIFF_CHARS:
            patch = patch[:_MAX_DIFF_CHARS] + "\n... (diff truncated)"

        context_parts = []
        if intent_summary:
            context_parts.append(f"=== PR Intent ===\n{intent_summary}")
        if convention_hints:
            context_parts.append(f"=== Codebase Conventions ===\n{convention_hints}")
        if repo and base_ref:
            try:
                gh = GitHubClient()
                file_content = gh.get_file_content(repo, base_ref, filename)
                surrounding = build_surrounding_context(file_content, patch)
                if surrounding:
                    context_parts.append(f"=== Surrounding code ===\n{surrounding}")
            except Exception as exc:
                log.debug("context_fetch_failed", file=filename, error=str(exc))

        context = "\n\n".join(context_parts)
        return FileReviewAgent().review(file_path=filename, diff=patch, context=context)

    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_review_one, f): f.get("filename", "") for f in reviewable}
        for future in concurrent.futures.as_completed(futures):
            filename = futures[future]
            try:
                result = future.result()
                findings.extend(result.findings)
                log.info("graph_file_reviewed", file=filename, findings=len(result.findings))
            except Exception as exc:
                log.error("graph_file_review_failed", file=filename, error=str(exc))

    log.info("graph_fan_out_done", total_findings=len(findings))
    return {"all_findings": findings}


def classify(state: ReviewState) -> dict:
    """Classify and rank all accumulated findings."""
    findings = state.get("all_findings", [])
    if not findings:
        return {"classified_findings": [], "decision": "COMMENT"}

    classified = ClassifierAgent().classify(findings)
    critical = [f for f in classified if f.severity == "critical"]
    decision = "REQUEST_CHANGES" if critical else "COMMENT"
    log.info("graph_classified", total=len(classified), critical=len(critical), decision=decision)
    return {"classified_findings": classified, "decision": decision}


def deep_security_scan(state: ReviewState) -> dict:
    """Focused second-pass security scan on files with critical findings."""
    classified = state.get("classified_findings", [])
    critical = [f for f in classified if f.severity == "critical"]
    critical_files = list({f.file_path for f in critical})

    critical_file_infos = [
        f for f in state["reviewable_files"]
        if f.get("filename", "") in critical_files
    ]

    log.info("graph_deep_scan", files=critical_files)
    try:
        scanner = SecurityScannerAgent()
        patches = {f.get("filename", ""): f.get("patch", "") for f in critical_file_infos}
        scan_result = scanner.scan(
            repo=state["repo"],
            pr_number=state["pr_number"],
            head_sha=state["head_sha"],
            files=critical_file_infos,
            patches=patches,
        )
        new_findings = scan_result.get("findings", [])
        log.info("graph_deep_scan_done", new_findings=len(new_findings))
        return {"all_findings": new_findings, "security_scan_done": True}
    except Exception as exc:
        log.error("graph_deep_scan_failed", error=str(exc))
        return {"security_scan_done": True}


def post_review(state: ReviewState) -> dict:
    """Generate report and post GitHub review."""
    if state.get("error"):
        return {}

    classified = state.get("classified_findings", [])
    critical = [f for f in classified if f.severity == "critical"]
    suggestive = [f for f in classified if f.severity == "suggestive"]
    optimisation = [f for f in classified if f.severity == "optimisation"]
    decision = state.get("decision", "COMMENT")

    report_md, report_json = generate_report(
        repo=state["repo"],
        pr_number=state["pr_number"],
        pr_title=state["pr_title"],
        head_sha=state["head_sha"],
        author=state["author_login"],
        decision=decision,
        findings=classified,
    )

    gh = GitHubClient()
    try:
        inline_comments = _build_inline_comments(classified, state["head_sha"])
        gh.create_review(
            repo=state["repo"],
            pr_number=state["pr_number"],
            commit_id=state["head_sha"],
            body=_build_review_body(critical, suggestive, optimisation, report_md),
            event=decision,
            comments=inline_comments or None,
        )
    except Exception as exc:
        log.error("graph_post_review_failed", error=str(exc))

    log.info("graph_review_posted", decision=decision, total=len(classified))
    return {"report_md": report_md, "report_json": report_json, "status": "completed"}


def notify(state: ReviewState) -> dict:
    """Send email, Slack, and ticket notifications."""
    if state.get("error"):
        return {}

    classified = state.get("classified_findings", [])
    critical = [f for f in classified if f.severity == "critical"]
    suggestive = [f for f in classified if f.severity == "suggestive"]
    optimisation = [f for f in classified if f.severity == "optimisation"]
    pr_url = f"https://github.com/{state['repo']}/pull/{state['pr_number']}"

    if critical and state.get("author_email"):
        summary = "\n".join(f"• [{f.file_path}] {f.title}" for f in critical[:10])
        send_critical_finding_email(
            to_email=state["author_email"],
            to_name=state["author_login"],
            repo=state["repo"],
            pr_number=state["pr_number"],
            pr_title=state["pr_title"],
            pr_url=pr_url,
            critical_count=len(critical),
            findings_summary=summary,
        )

    send_review_notification(
        repo=state["repo"],
        pr_number=state["pr_number"],
        pr_title=state["pr_title"],
        pr_url=pr_url,
        author=state["author_login"],
        decision=state.get("decision", "COMMENT"),
        critical=len(critical),
        suggestive=len(suggestive),
        optimisation=len(optimisation),
    )

    if critical:
        try:
            provider = get_ticket_provider()
            if provider:
                findings_text = "\n".join(
                    f"- [{f.severity.upper()}] {f.file_path}: {f.title}" for f in critical
                )
                provider.create_ticket(TicketRequest(
                    title=f"[MACRV] Critical findings in PR #{state['pr_number']}: {state['pr_title']}",
                    description=(
                        f"PR: {pr_url}\nRepo: {state['repo']}\nAuthor: @{state['author_login']}\n\n"
                        f"Critical Findings:\n{findings_text}"
                    ),
                    severity="critical",
                    repo=state["repo"],
                    pr_number=state["pr_number"],
                    file_path="multiple",
                    labels=["code-review", "critical", "automated"],
                ))
        except Exception as exc:
            log.error("graph_ticket_failed", error=str(exc))

    return {}


# ── Conditional edge ───────────────────────────────────────────────────────────

def should_deep_scan(state: ReviewState) -> str:
    """Route to deep_security_scan if critical findings exist and scan not yet done."""
    classified = state.get("classified_findings", [])
    critical = [f for f in classified if f.severity == "critical"]
    if critical and not state.get("security_scan_done", False):
        log.info("graph_routing_deep_scan", critical_count=len(critical))
        return "deep_security_scan"
    return "post_review"


# ── Helper functions ───────────────────────────────────────────────────────────

def _should_skip(filename: str) -> bool:
    if not filename:
        return True
    if filename in _SKIP_PATHS:
        return True
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    return ext.lower() in _SKIP_EXTENSIONS


def _build_inline_comments(findings: list[FileFinding], commit_id: str) -> list[dict]:
    comments = []
    for f in findings:
        if f.line_start is None:
            continue
        icon = {"critical": "🔴", "suggestive": "🟡", "optimisation": "🟢"}.get(f.severity, "🟡")
        body = f"{icon} **{f.severity.upper()}**: {f.title}\n\n{f.body}"
        if f.suggestion:
            body += f"\n\n**Suggested fix:**\n```\n{f.suggestion}\n```"
        comments.append({"path": f.file_path, "line": f.line_start, "body": body})
    return comments


def _build_review_body(critical, suggestive, optimisation, report_md: str) -> str:
    total = len(critical) + len(suggestive) + len(optimisation)
    if total == 0:
        return "✅ **MACRV Review**: No issues found. Looks good!"
    lines = [
        "## 🤖 MACRV Automated Code Review",
        "",
        "| Severity | Count |",
        "|---|---|",
        f"| 🔴 Critical | {len(critical)} |",
        f"| 🟡 Suggestive | {len(suggestive)} |",
        f"| 🟢 Optimisation | {len(optimisation)} |",
        f"| **Total** | **{total}** |",
        "",
    ]
    if critical:
        lines.append("### 🔴 Critical Issues (blocking merge)")
        for f in critical:
            lines.append(f"- **{f.file_path}**: {f.title}")
        lines.append("")
    lines.append("<details><summary>Full Report</summary>\n\n" + report_md + "\n</details>")
    return "\n".join(lines)


# ── Build graph ────────────────────────────────────────────────────────────────

def build_review_graph():
    """Construct and compile the LangGraph review pipeline."""
    workflow = StateGraph(ReviewState)

    workflow.add_node("fetch_pr", fetch_pr)
    workflow.add_node("fan_out_review", fan_out_review)
    workflow.add_node("classify", classify)
    workflow.add_node("deep_security_scan", deep_security_scan)
    workflow.add_node("post_review", post_review)
    workflow.add_node("notify", notify)

    workflow.set_entry_point("fetch_pr")
    workflow.add_edge("fetch_pr", "fan_out_review")
    workflow.add_edge("fan_out_review", "classify")
    workflow.add_conditional_edges("classify", should_deep_scan, {
        "deep_security_scan": "deep_security_scan",
        "post_review": "post_review",
    })
    workflow.add_edge("deep_security_scan", "classify")
    workflow.add_edge("post_review", "notify")
    workflow.add_edge("notify", END)

    return workflow.compile()


# ── Public API — drop-in replacement for OrchestratorAgent.run() ──────────────

class GraphOrchestratorAgent:
    """LangGraph-based orchestrator. Same interface as OrchestratorAgent."""

    def __init__(self):
        self.graph = build_review_graph()
        self.log = log

    def run(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        pr_title: str,
        author_login: str,
        author_email: str | None,
        diff_url: str,
    ) -> dict:
        self.log.info("graph_orchestrator_run", repo=repo_full_name, pr=pr_number)

        initial_state: ReviewState = {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "pr_title": pr_title,
            "author_login": author_login,
            "author_email": author_email,
            "diff_url": diff_url,
            "files": [],
            "reviewable_files": [],
            "pr_detail": {},
            "intent_summary": "",
            "convention_hints": "",
            "base_ref": "main",
            "all_findings": [],
            "classified_findings": [],
            "security_scan_done": False,
            "decision": "COMMENT",
            "report_md": "",
            "report_json": "",
            "error": None,
            "status": "pending",
        }

        final_state = self.graph.invoke(initial_state)

        if final_state.get("error"):
            return {
                "repo": repo_full_name,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "total_findings": 0,
                "critical": 0,
                "suggestive": 0,
                "optimisation": 0,
                "decision": "error",
                "status": "failed",
                "error": final_state["error"],
            }

        classified = final_state.get("classified_findings", [])
        critical = [f for f in classified if f.severity == "critical"]
        suggestive = [f for f in classified if f.severity == "suggestive"]
        optimisation = [f for f in classified if f.severity == "optimisation"]

        return {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "total_findings": len(classified),
            "critical": len(critical),
            "suggestive": len(suggestive),
            "optimisation": len(optimisation),
            "decision": final_state.get("decision", "COMMENT"),
            "status": final_state.get("status", "completed"),
            "report_md": final_state.get("report_md", ""),
            "report_json": final_state.get("report_json", ""),
        }
