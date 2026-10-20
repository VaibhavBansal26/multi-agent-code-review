"""Slack notification client (incoming webhook)."""
from __future__ import annotations
import structlog
import httpx
from app.config import get_settings

log = structlog.get_logger(__name__)


def send_review_notification(
    *,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    author: str,
    decision: str,
    critical: int,
    suggestive: int,
    optimisation: int,
) -> bool:
    """Post a PR review summary to the configured Slack webhook."""
    settings = get_settings()
    if not settings.slack_webhook_url:
        log.info("slack_skipped_no_webhook")
        return False

    total = critical + suggestive + optimisation
    color = "#ff5252" if critical else "#4caf50"
    decision_icon = "🚫" if decision == "REQUEST_CHANGES" else "✅"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{decision_icon} MACRV Code Review — {repo}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*PR:* <{pr_url}|#{pr_number}: {pr_title}>"},
                {"type": "mrkdwn", "text": f"*Author:* @{author}"},
                {"type": "mrkdwn", "text": f"*Decision:* `{decision}`"},
                {"type": "mrkdwn", "text": f"*Findings:* 🔴 {critical}  🟡 {suggestive}  🟢 {optimisation}"},
            ],
        },
    ]

    if critical:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⛔ *{critical} critical issue(s) must be resolved before merge.*",
            },
        })

    payload = {"attachments": [{"color": color, "blocks": blocks}]}

    try:
        resp = httpx.post(settings.slack_webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("slack_notification_sent", repo=repo, pr=pr_number)
        return True
    except Exception as exc:
        log.error("slack_notification_failed", error=str(exc))
        return False
