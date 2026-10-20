import json
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.webhook.models import PingEvent, PullRequestEvent
from app.webhook.validator import verify_github_signature

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

SUPPORTED_EVENTS = {"pull_request", "ping"}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_github_delivery: str = Header(default="", alias="X-GitHub-Delivery"),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
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
        return JSONResponse({"status": "pong", "hook_id": event.hook_id})

    if x_github_event == "pull_request":
        return await _handle_pull_request(payload, x_github_delivery)

    return JSONResponse({"status": "ignored", "event": x_github_event})


async def _handle_pull_request(payload: dict, delivery_id: str) -> JSONResponse:
    from app.queue.tasks import enqueue_pr_review

    try:
        pr_event = PullRequestEvent(**payload)
    except Exception as exc:
        log.error("pr_event_parse_error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    if not pr_event.should_review():
        log.info("pr_event_skipped", action=pr_event.action, pr=pr_event.number)
        return JSONResponse({"status": "skipped", "action": pr_event.action})

    task_id = enqueue_pr_review(
        repo_full_name=pr_event.repository.full_name,
        pr_number=pr_event.number,
        head_sha=pr_event.pull_request.head_sha,
        pr_title=pr_event.pull_request.title,
        author_login=pr_event.pull_request.user.login,
        author_email=pr_event.pull_request.user.email,
        diff_url=pr_event.pull_request.diff_url,
        delivery_id=delivery_id,
    )

    log.info(
        "pr_review_enqueued",
        pr=pr_event.number,
        repo=pr_event.repository.full_name,
        task_id=task_id,
    )
    return JSONResponse({"status": "queued", "task_id": task_id, "pr": pr_event.number})
