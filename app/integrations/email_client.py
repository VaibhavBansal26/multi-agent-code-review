"""Email notification client (SendGrid)."""
from __future__ import annotations

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


def send_critical_finding_email(
    *,
    to_email: str,
    to_name: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    critical_count: int,
    findings_summary: str,
) -> bool:
    """Send a critical finding notification email to the PR author."""
    settings = get_settings()
    if not settings.sendgrid_api_key:
        log.warning("email_skipped_no_key")
        return False

    try:
        import sendgrid  # type: ignore
        from sendgrid.helpers.mail import Mail, To  # type: ignore

        sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
        message = Mail(
            from_email=(settings.email_from, settings.email_from_name),
            to_emails=To(to_email, to_name),
            subject=f"[MACRV] ⛔ {critical_count} Critical Finding(s) — PR #{pr_number}: {pr_title}",
            html_content=_render_email(
                author=to_name,
                repo=repo,
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                critical_count=critical_count,
                findings_summary=findings_summary,
            ),
        )
        sg.send(message)
        log.info("email_sent", to=to_email, pr=pr_number, critical=critical_count)
        return True
    except Exception as exc:
        log.error("email_failed", error=str(exc))
        return False


def _render_email(
    author: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    critical_count: int,
    findings_summary: str,
) -> str:
    return f"""
<html><body style="font-family:monospace;background:#0a0d14;color:#e8edf5;padding:24px">
<h2 style="color:#ff5252">⛔ Critical Review Findings</h2>
<p>Hi {author},</p>
<p>Your pull request <strong>#{pr_number}: {pr_title}</strong> in <code>{repo}</code>
has <strong style="color:#ff5252">{critical_count} critical finding(s)</strong>
that must be resolved before merge.</p>
<h3 style="color:#4a9eff">Findings Summary</h3>
<pre style="background:#141926;padding:16px;border-radius:8px;white-space:pre-wrap">{findings_summary}</pre>
<p><a href="{pr_url}" style="color:#4a9eff">View Pull Request &rarr;</a></p>
<hr style="border-color:#252e45">
<p style="color:#535e72;font-size:11px">Sent by MACRV &mdash; Multi-Agent Code Review</p>
</body></html>"""
