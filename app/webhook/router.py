import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.webhook.models import PingEvent, PullRequestEvent
from app.webhook.validator import verify_github_signature

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

SUPPORTED_EVENTS = {"pull_request", "ping"}
_review_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="macrv-review")


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_github_delivery: str = Header(default="", alias="X-GitHub-Delivery"),
) -> JSONResponse:
    settings = get_settings()
    body = await verify_github_signature(request, settings.github_webhook_secret)
    payload = json.loads(body)

    log.info(
        "webhook_received",
        gh_event=x_github_event,
        delivery=x_github_delivery,
    )

    if x_github_event == "ping":
        event = PingEvent(**payload)
        log.info("github_ping", hook_id=event.hook_id, zen=event.zen)
        _try_upsert_repo(event)
        return JSONResponse({"status": "pong", "hook_id": event.hook_id})

    if x_github_event == "pull_request":
        return _handle_pull_request(payload, x_github_delivery)

    return JSONResponse({"status": "ignored", "event": x_github_event})


def _handle_pull_request(payload: dict, delivery_id: str) -> JSONResponse:
    try:
        pr_event = PullRequestEvent(**payload)
    except Exception as exc:
        log.error("pr_event_parse_error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if not pr_event.should_review():
        log.info("pr_event_skipped", action=pr_event.action, pr=pr_event.number)
        return JSONResponse({"status": "skipped", "action": pr_event.action})

    repo = pr_event.repository.full_name
    pr_number = pr_event.number
    head_sha = pr_event.pull_request.head_sha
    pr_title = pr_event.pull_request.title
    author_login = pr_event.pull_request.user.login
    author_email = pr_event.pull_request.user.email
    diff_url = pr_event.pull_request.diff_url

    log.info("pr_review_enqueued", pr=pr_number, repo=repo)

    # Run review in background thread so we return 200 immediately.
    # This keeps the webhook alive on Render free tier (no Redis/Celery needed).
    _review_executor.submit(
        _run_review_sync,
        repo, pr_number, head_sha, pr_title, author_login, author_email, diff_url, delivery_id,
    )

    return JSONResponse({"status": "queued", "pr": pr_number, "repo": repo})


def _run_review_sync(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    pr_title: str,
    author_login: str,
    author_email: str | None,
    diff_url: str,
    delivery_id: str,
) -> None:
    """Run the full review pipeline in a background thread and post the result to GitHub."""
    log.info("pr_review_started", repo=repo_full_name, pr=pr_number, sha=head_sha[:8])
    try:
        from app.agents.graph_orchestrator import GraphOrchestratorAgent

        result = GraphOrchestratorAgent().run(
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
        _try_persist(repo_full_name, pr_number, head_sha, pr_title, author_login, author_email, result)
    except Exception as exc:
        log.error("pr_review_failed", repo=repo_full_name, pr=pr_number, error=str(exc))


def _try_persist(repo_full_name, pr_number, head_sha, pr_title, author_login, author_email, result):
    """Best-effort DB persistence — never raises so a DB blip doesn't kill the review."""
    try:
        import json as _json
        from sqlalchemy.orm import sessionmaker
        from app.db.session import get_sync_engine
        from app.db.models import (
            Finding, PRReview, ReviewDecision, ReviewReport, ReviewStatus, Severity,
        )

        _DECISION_MAP = {
            "COMMENT": "commented", "REQUEST_CHANGES": "changes_requested",
            "APPROVE": "approved", "error": "commented",
        }

        factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
        session = factory()
        try:
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
                    status=ReviewStatus.COMPLETED if result.get("status") != "failed" else ReviewStatus.FAILED,
                    decision=ReviewDecision(_DECISION_MAP.get(result.get("decision", "COMMENT"), "commented")),
                    files_reviewed=result.get("files_reviewed", 0),
                )
                session.add(row)
                session.commit()
            else:
                row.status = ReviewStatus.COMPLETED if result.get("status") != "failed" else ReviewStatus.FAILED
                row.decision = ReviewDecision(_DECISION_MAP.get(result.get("decision", "COMMENT"), "commented"))
                row.files_reviewed = result.get("files_reviewed", 0)
                session.commit()

            review_id = row.id
            for f in result.get("findings", []):
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

            if result.get("report_md"):
                existing = session.query(ReviewReport).filter_by(review_id=review_id).first()
                if existing:
                    existing.markdown = result["report_md"]
                    existing.json_data = _json.dumps(result.get("report_json", {}))
                else:
                    session.add(ReviewReport(
                        review_id=review_id,
                        markdown=result["report_md"],
                        json_data=_json.dumps(result.get("report_json", {})),
                    ))
            session.commit()
            log.info("pr_review_persisted", pr=pr_number)
        finally:
            session.close()
    except Exception as exc:
        log.warning("pr_review_persist_failed", pr=pr_number, error=str(exc))


def _try_upsert_repo(event: PingEvent) -> None:
    """Best-effort upsert of a connected repo on ping."""
    if not event.repository:
        return
    try:
        from sqlalchemy.orm import sessionmaker
        from app.db.session import get_sync_engine
        from app.db.models import ConnectedRepo

        factory = sessionmaker(bind=get_sync_engine(), expire_on_commit=False)
        session = factory()
        try:
            now = datetime.now(UTC)
            existing = (
                session.query(ConnectedRepo)
                .filter_by(repo_full_name=event.repository.full_name)
                .first()
            )
            if existing:
                existing.hook_id = event.hook_id
                existing.active = True
                existing.updated_at = now
            else:
                session.add(ConnectedRepo(
                    repo_full_name=event.repository.full_name,
                    hook_id=event.hook_id,
                    active=True,
                    connected_at=now,
                    updated_at=now,
                ))
            session.commit()
            log.info("connected_repo_upserted", repo=event.repository.full_name)
        finally:
            session.close()
    except Exception as exc:
        log.warning("upsert_repo_failed", error=str(exc))
