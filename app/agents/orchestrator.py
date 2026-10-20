"""Orchestrator agent — plans and drives the full PR review pipeline."""
from __future__ import annotations
import concurrent.futures
import structlog
from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileReviewAgent, FileFinding, FileReviewResult
from app.agents.classifier import ClassifierAgent
from app.config import get_settings
from app.integrations.github_client import GitHubClient
from app.reports.generator import generate_report

log = structlog.get_logger(__name__)

# Skip these file types — no meaningful review possible
_SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".otf", ".mp4", ".mp3", ".pdf", ".zip", ".tar",
    ".lock", ".sum",
}
_SKIP_PATHS = {"package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock"}
_MAX_DIFF_CHARS = 12_000  # truncate very large file diffs
_MAX_WORKERS = 5


class OrchestratorAgent(BaseAgent):
    """
    Runs on claude-opus-5-5.
    Fetches the PR diff, fans out FileReviewAgents per file,
    collects findings, classifies them, posts comments, and decides
    whether to approve or request changes.
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(model=settings.orchestrator_model)

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
        """Full pipeline: fetch → fan-out review → classify → report → GitHub review."""
        self.log.info("orchestrator_run", repo=repo_full_name, pr=pr_number)

        gh = GitHubClient()

        # 1. Fetch changed files
        try:
            files = gh.get_pr_files(repo_full_name, pr_number)
        except Exception as exc:
            self.log.error("fetch_files_failed", error=str(exc))
            return self._error_result(repo_full_name, pr_number, head_sha, str(exc))

        reviewable = [
            f for f in files
            if not self._should_skip(f.get("filename", ""))
            and f.get("patch")  # has a diff
        ]

        self.log.info("files_to_review", total=len(files), reviewable=len(reviewable))

        # 2. Fan out file reviewers in parallel
        all_findings: list[FileFinding] = []
        progress = {"reviewed": 0, "total": len(reviewable)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._review_file, f): f.get("filename", "")
                for f in reviewable
            }
            for future in concurrent.futures.as_completed(futures):
                filename = futures[future]
                try:
                    result: FileReviewResult = future.result()
                    all_findings.extend(result.findings)
                    progress["reviewed"] += 1
                    pct = int(progress["reviewed"] / max(progress["total"], 1) * 100)
                    self.log.info(
                        "review_progress",
                        file=filename,
                        pct=pct,
                        reviewed=progress["reviewed"],
                        total=progress["total"],
                    )
                except Exception as exc:
                    self.log.error("file_review_failed", file=filename, error=str(exc))

        # 3. Classify and rank findings
        if all_findings:
            classifier = ClassifierAgent()
            all_findings = classifier.classify(all_findings)

        # 4. Summarise
        critical = [f for f in all_findings if f.severity == "critical"]
        suggestive = [f for f in all_findings if f.severity == "suggestive"]
        optimisation = [f for f in all_findings if f.severity == "optimisation"]

        decision = "REQUEST_CHANGES" if critical else "COMMENT"

        # 5. Generate report
        report_md, report_json = generate_report(
            repo=repo_full_name,
            pr_number=pr_number,
            pr_title=pr_title,
            head_sha=head_sha,
            author=author_login,
            decision=decision,
            findings=all_findings,
        )

        # 6. Post GitHub review (inline comments + top-level summary)
        try:
            inline_comments = self._build_inline_comments(all_findings, head_sha)
            gh.create_review(
                repo=repo_full_name,
                pr_number=pr_number,
                commit_id=head_sha,
                body=self._build_review_body(critical, suggestive, optimisation, report_md),
                event=decision,
                comments=inline_comments or None,
            )
        except Exception as exc:
            self.log.error("post_review_failed", error=str(exc))

        result = {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "total_findings": len(all_findings),
            "critical": len(critical),
            "suggestive": len(suggestive),
            "optimisation": len(optimisation),
            "decision": decision,
            "status": "completed",
            "report_md": report_md,
            "report_json": report_json,
        }
        self.log.info(
            "orchestrator_done",
            repo=repo_full_name,
            pr=pr_number,
            total=len(all_findings),
            decision=decision,
        )
        return result

    def _review_file(self, file_info: dict) -> FileReviewResult:
        filename = file_info.get("filename", "")
        patch = file_info.get("patch", "") or ""
        if len(patch) > _MAX_DIFF_CHARS:
            patch = patch[:_MAX_DIFF_CHARS] + "\n... (diff truncated)"
        reviewer = FileReviewAgent()
        return reviewer.review(file_path=filename, diff=patch)

    @staticmethod
    def _should_skip(filename: str) -> bool:
        if not filename:
            return True
        if filename in _SKIP_PATHS:
            return True
        ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
        return ext.lower() in _SKIP_EXTENSIONS

    @staticmethod
    def _build_inline_comments(findings: list[FileFinding], commit_id: str) -> list[dict]:
        comments = []
        for f in findings:
            if f.line_start is None:
                continue
            icon = {"critical": "🔴", "suggestive": "🟡", "optimisation": "🟢"}.get(f.severity, "🟡")
            body = f"{icon} **{f.severity.upper()}**: {f.title}\n\n{f.body}"
            if f.suggestion:
                body += f"\n\n**Suggested fix:**\n```\n{f.suggestion}\n```"
            comments.append({
                "path": f.file_path,
                "line": f.line_start,
                "body": body,
            })
        return comments

    @staticmethod
    def _build_review_body(critical, suggestive, optimisation, report_md: str) -> str:
        total = len(critical) + len(suggestive) + len(optimisation)
        if total == 0:
            return "✅ **MACRV Review**: No issues found. Looks good!"
        lines = [
            "## 🤖 MACRV Automated Code Review",
            "",
            f"| Severity | Count |",
            f"|---|---|",
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

    @staticmethod
    def _error_result(repo: str, pr_number: int, head_sha: str, error: str) -> dict:
        return {
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "total_findings": 0,
            "critical": 0,
            "suggestive": 0,
            "optimisation": 0,
            "decision": "error",
            "status": "failed",
            "error": error,
        }
