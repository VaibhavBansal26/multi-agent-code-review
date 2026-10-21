"""Celery tasks for PR review pipeline."""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.queue.celery_app import celery_app

log = structlog.get_logger(__name__)

_DECISION_MAP = {
    "COMMENT": "commented",
    "REQUEST_CHANGES": "changes_requested",
    "APPROVE": "approved",
    "error": "commented",
}


def _get_sync_session() -> Session:
    from sqlalchemy.orm import sessionmaker
    from app.db.session import get_sync_engine
    factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
    return factory()


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
    from app.db.models import Finding, PRReview, ReviewDecision, ReviewStatus, Severity

    task_id = self.request.id
    log.info(
        "pr_review_started",
        repo=repo_full_name,
        pr=pr_number,
        sha=head_sha[:8],
        task_id=task_id,
    )

    session = _get_sync_session()
    try:
        # Upsert PRReview row
        row = (
            session.query(PRReview)
            .filter_by(repo_full_name=repo_full_name, pr_number=pr_number, head_sha=head_sha)
            .first()
        )
        if row is None:
            row = PRReview(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                pr_title=pr_title,
                author_login=author_login,
                author_email=author_email,
                status=ReviewStatus.IN_PROGRESS,
                task_id=task_id,
            )
            session.add(row)
        else:
            row.status = ReviewStatus.IN_PROGRESS
            row.task_id = task_id
        session.commit()
        review_id = row.id

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

        # Map GitHub event string → DB enum value
        raw_decision = result.get("decision", "COMMENT")
        decision_value = _DECISION_MAP.get(raw_decision, "commented")

        # Persist findings (FileFinding dataclass objects)
        raw_findings = result.get("findings", [])
        for f in raw_findings:
            fdict = f.__dict__ if hasattr(f, "__dict__") else f
            try:
                sev = Severity(fdict.get("severity", "suggestive"))
            except ValueError:
                sev = Severity.SUGGESTIVE
            session.add(Finding(
                review_id=review_id,
                severity=sev,
                file_path=fdict.get("file_path", ""),
                line_start=fdict.get("line_start"),
                line_end=fdict.get("line_end"),
                title=fdict.get("title", ""),
                body=fdict.get("body", ""),
                suggestion=fdict.get("suggestion"),
                source=fdict.get("source", "llm"),
            ))

        row.status = ReviewStatus.COMPLETED if result.get("status") != "failed" else ReviewStatus.FAILED
        row.decision = ReviewDecision(decision_value)
        row.files_reviewed = result.get("files_reviewed", 0)
        session.commit()

        log.info(
            "pr_review_completed",
            repo=repo_full_name,
            pr=pr_number,
            findings=result.get("total_findings", 0),
            decision=decision_value,
        )
        return result

    except Exception as exc:
        try:
            if 'row' in dir() and row.id:
                row.status = ReviewStatus.FAILED
                session.commit()
        except Exception:
            pass
        log.error("pr_review_failed", repo=repo_full_name, pr=pr_number, error=str(exc))
        raise self.retry(exc=exc) from exc
    finally:
        session.close()


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
        raise self.retry(exc=exc) from exc
