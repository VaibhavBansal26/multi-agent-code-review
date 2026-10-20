"""Celery tasks for PR review pipeline."""
from __future__ import annotations
import structlog
from celery import shared_task
from app.queue.celery_app import celery_app

log = structlog.get_logger(__name__)


def enqueue_pr_review(
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    pr_title: str,
    author_login: str,
    author_email: str | None,
    diff_url: str,
    delivery_id: str,
) -> str:
    """Enqueue a PR review task and return the task ID."""
    task = run_pr_review.apply_async(
        kwargs={
            "repo_full_name": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "pr_title": pr_title,
            "author_login": author_login,
            "author_email": author_email,
            "diff_url": diff_url,
            "delivery_id": delivery_id,
        },
        queue="reviews",
    )
    return task.id


@celery_app.task(
    name="app.queue.tasks.run_pr_review",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def run_pr_review(
    self,
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    pr_title: str,
    author_login: str,
    author_email: str | None,
    diff_url: str,
    delivery_id: str,
) -> dict:
    """Main PR review pipeline task."""
    log.info(
        "pr_review_started",
        repo=repo_full_name,
        pr=pr_number,
        sha=head_sha[:8],
        task_id=self.request.id,
    )

    try:
        from app.agents.graph_orchestrator import GraphOrchestratorAgent

        orchestrator = GraphOrchestratorAgent()
        result = orchestrator.run(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=pr_title,
            author_login=author_login,
            author_email=author_email,
            diff_url=diff_url,
        )

        log.info(
            "pr_review_completed",
            repo=repo_full_name,
            pr=pr_number,
            findings=result.get("total_findings", 0),
            decision=result.get("decision"),
        )
        return result

    except Exception as exc:
        log.error("pr_review_failed", repo=repo_full_name, pr=pr_number, error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.queue.tasks.run_security_scan",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
)
def run_security_scan(
    self,
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    files: list[str],
) -> dict:
    """Dedicated security scanning task."""
    log.info("security_scan_started", repo=repo_full_name, pr=pr_number)
    try:
        from app.agents.security_scanner import SecurityScannerAgent

        scanner = SecurityScannerAgent()
        return scanner.scan(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            files=files,
        )
    except Exception as exc:
        log.error("security_scan_failed", repo=repo_full_name, pr=pr_number, error=str(exc))
        raise self.retry(exc=exc)
