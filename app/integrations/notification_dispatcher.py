"""Central notification dispatcher for Phase 5.

Coordinates email, Slack, and ticket creation from a single call site,
returning a structured result so callers know which channels fired.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.agents.file_reviewer import FileFinding
from app.integrations.email_client import send_critical_finding_email
from app.integrations.slack_client import send_review_notification
from app.integrations.ticket_provider import TicketRequest, TicketResult, get_ticket_provider

log = structlog.get_logger(__name__)


@dataclass
class NotificationResult:
    email_sent: bool = False
    slack_sent: bool = False
    tickets_created: list[TicketResult] = field(default_factory=list)

    @property
    def any_sent(self) -> bool:
        return self.email_sent or self.slack_sent or bool(self.tickets_created)

    def summary(self) -> str:
        parts = []
        if self.email_sent:
            parts.append("email")
        if self.slack_sent:
            parts.append("slack")
        if self.tickets_created:
            ids = ", ".join(t.ticket_id for t in self.tickets_created)
            parts.append(f"tickets({ids})")
        return ", ".join(parts) if parts else "none"


class NotificationDispatcher:
    """
    Dispatches review notifications across all configured channels.

    Channels fire only when their credentials are set in Settings — all are
    optional and fail-safe (errors are logged, never raised).
    """

    def dispatch(
        self,
        *,
        repo: str,
        pr_number: int,
        pr_title: str,
        author_login: str,
        author_email: str | None,
        decision: str,
        findings: list[FileFinding],
    ) -> NotificationResult:
        critical = [f for f in findings if f.severity == "critical"]
        suggestive = [f for f in findings if f.severity == "suggestive"]
        optimisation = [f for f in findings if f.severity == "optimisation"]
        pr_url = f"https://github.com/{repo}/pull/{pr_number}"
        result = NotificationResult()

        # Email — only on critical findings, only when we have an address
        if critical and author_email:
            summary = "\n".join(
                f"• [{f.file_path}] {f.title}" for f in critical[:10]
            )
            result.email_sent = send_critical_finding_email(
                to_email=author_email,
                to_name=author_login,
                repo=repo,
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                critical_count=len(critical),
                findings_summary=summary,
            )

        # Slack — every review
        result.slack_sent = send_review_notification(
            repo=repo,
            pr_number=pr_number,
            pr_title=pr_title,
            pr_url=pr_url,
            author=author_login,
            decision=decision,
            critical=len(critical),
            suggestive=len(suggestive),
            optimisation=len(optimisation),
        )

        # Tickets — one ticket per critical finding, up to 5
        if critical:
            provider = get_ticket_provider()
            if provider:
                for finding in critical[:5]:
                    try:
                        ticket = provider.create_ticket(TicketRequest(
                            title=finding.title,
                            description=(
                                f"**PR:** {pr_url}\n"
                                f"**File:** `{finding.file_path}`"
                                + (f" line {finding.line_start}" if finding.line_start else "")
                                + f"\n**Repo:** {repo}\n**Author:** @{author_login}\n\n"
                                + finding.body
                            ),
                            severity=finding.severity,
                            repo=repo,
                            pr_number=pr_number,
                            file_path=finding.file_path,
                            line_start=finding.line_start,
                            labels=["code-review", "critical", "automated"],
                        ))
                        result.tickets_created.append(ticket)
                    except Exception as exc:
                        log.error("ticket_create_failed", error=str(exc), title=finding.title)

        log.info(
            "notifications_dispatched",
            repo=repo,
            pr=pr_number,
            channels=result.summary(),
            critical=len(critical),
        )
        return result
