"""Tests for Phase 5: email, Slack, and ticket provider notifications."""
import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch


# ─── Email ────────────────────────────────────────────────────────────────────

class TestEmailClient:
    def test_no_sendgrid_key_returns_false(self):
        from app.integrations.email_client import send_critical_finding_email
        with patch("app.integrations.email_client.get_settings") as mock_s:
            mock_s.return_value.sendgrid_api_key = ""
            result = send_critical_finding_email(
                to_email="dev@example.com",
                to_name="Dev",
                repo="owner/repo",
                pr_number=1,
                pr_title="Test PR",
                pr_url="https://github.com/owner/repo/pull/1",
                critical_count=2,
                findings_summary="• [app.py] SQL injection",
            )
        assert result is False

    def test_sendgrid_send_called(self):
        import sys
        from unittest.mock import MagicMock
        mock_sendgrid_module = MagicMock()
        mock_sg_client = MagicMock()
        mock_sendgrid_module.SendGridAPIClient.return_value = mock_sg_client
        mock_sg_client.send.return_value = MagicMock(status_code=202)
        mock_mail_module = MagicMock()
        with patch.dict(sys.modules, {"sendgrid": mock_sendgrid_module, "sendgrid.helpers.mail": mock_mail_module}):
            from importlib import reload
            import app.integrations.email_client as ec
            reload(ec)
            with patch.object(ec, "get_settings") as mock_s:
                mock_s.return_value.sendgrid_api_key = "SG.test"
                mock_s.return_value.email_from = "bot@macrv.dev"
                mock_s.return_value.email_from_name = "MACRV"
                result = ec.send_critical_finding_email(
                    to_email="dev@example.com",
                    to_name="Dev",
                    repo="owner/repo",
                    pr_number=1,
                    pr_title="Test PR",
                    pr_url="https://github.com/owner/repo/pull/1",
                    critical_count=1,
                    findings_summary="• issue",
                )
        assert result is True

    def test_sendgrid_exception_returns_false(self):
        from app.integrations.email_client import send_critical_finding_email
        with patch("app.integrations.email_client.get_settings") as mock_s:
            mock_s.return_value.sendgrid_api_key = "SG.test"
            mock_s.return_value.email_from = "bot@macrv.dev"
            mock_s.return_value.email_from_name = "MACRV"
            # Patch the internal import to raise
            import builtins
            real_import = builtins.__import__
            def mock_import(name, *args, **kwargs):
                if name == "sendgrid":
                    raise ImportError("sendgrid not installed")
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=mock_import):
                result = send_critical_finding_email(
                    to_email="a@b.com", to_name="A", repo="r", pr_number=1,
                    pr_title="t", pr_url="http://x", critical_count=1, findings_summary="x",
                )
        assert result is False


# ─── Slack ────────────────────────────────────────────────────────────────────

class TestSlackClient:
    def test_no_webhook_url_returns_false(self):
        from app.integrations.slack_client import send_review_notification
        with patch("app.integrations.slack_client.get_settings") as mock_s:
            mock_s.return_value.slack_webhook_url = ""
            result = send_review_notification(
                repo="r", pr_number=1, pr_title="t", pr_url="http://x",
                author="dev", decision="COMMENT", critical=0, suggestive=1, optimisation=0,
            )
        assert result is False

    @respx.mock
    def test_slack_sends_payload(self):
        from app.integrations.slack_client import send_review_notification
        respx.post("https://hooks.slack.com/test").mock(
            return_value=httpx.Response(200, text="ok")
        )
        with patch("app.integrations.slack_client.get_settings") as mock_s:
            mock_s.return_value.slack_webhook_url = "https://hooks.slack.com/test"
            result = send_review_notification(
                repo="owner/repo",
                pr_number=42,
                pr_title="Add feature",
                pr_url="https://github.com/owner/repo/pull/42",
                author="alice",
                decision="COMMENT",
                critical=0,
                suggestive=2,
                optimisation=1,
            )
        assert result is True

    @respx.mock
    def test_slack_critical_payload(self):
        from app.integrations.slack_client import send_review_notification
        respx.post("https://hooks.slack.com/test").mock(
            return_value=httpx.Response(200, text="ok")
        )
        with patch("app.integrations.slack_client.get_settings") as mock_s:
            mock_s.return_value.slack_webhook_url = "https://hooks.slack.com/test"
            result = send_review_notification(
                repo="r", pr_number=1, pr_title="t", pr_url="http://x",
                author="dev", decision="REQUEST_CHANGES", critical=2, suggestive=0, optimisation=0,
            )
        assert result is True

    @respx.mock
    def test_slack_http_error_returns_false(self):
        from app.integrations.slack_client import send_review_notification
        respx.post("https://hooks.slack.com/fail").mock(
            return_value=httpx.Response(500, text="error")
        )
        with patch("app.integrations.slack_client.get_settings") as mock_s:
            mock_s.return_value.slack_webhook_url = "https://hooks.slack.com/fail"
            result = send_review_notification(
                repo="r", pr_number=1, pr_title="t", pr_url="http://x",
                author="dev", decision="COMMENT", critical=0, suggestive=0, optimisation=0,
            )
        assert result is False


# ─── Ticket Provider ─────────────────────────────────────────────────────────

class TestTicketProvider:
    def test_get_ticket_provider_no_config_returns_none(self):
        from app.integrations.ticket_provider import get_ticket_provider
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.jira_url = ""
            mock_s.return_value.jira_email = ""
            mock_s.return_value.jira_token = ""
            mock_s.return_value.linear_api_key = ""
            mock_s.return_value.linear_team_id = ""
            provider = get_ticket_provider()
        assert provider is None

    def test_jira_provider_is_configured(self):
        from app.integrations.ticket_provider import JiraProvider
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.jira_url = "https://company.atlassian.net"
            mock_s.return_value.jira_email = "user@company.com"
            mock_s.return_value.jira_token = "token123"
            mock_s.return_value.jira_project_key = "ENG"
            p = JiraProvider()
        assert p.is_configured() is True

    def test_jira_provider_not_configured_empty_url(self):
        from app.integrations.ticket_provider import JiraProvider
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.jira_url = ""
            mock_s.return_value.jira_email = ""
            mock_s.return_value.jira_token = ""
            mock_s.return_value.jira_project_key = ""
            p = JiraProvider()
        assert p.is_configured() is False

    def test_linear_provider_is_configured(self):
        from app.integrations.ticket_provider import LinearProvider
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.linear_api_key = "lin_key_123"
            mock_s.return_value.linear_team_id = "team-abc"
            p = LinearProvider()
        assert p.is_configured() is True

    @respx.mock
    def test_jira_create_ticket(self):
        from app.integrations.ticket_provider import JiraProvider, TicketRequest
        respx.post("https://company.atlassian.net/rest/api/3/issue").mock(
            return_value=httpx.Response(201, json={"id": "10001", "key": "ENG-1", "self": "https://company.atlassian.net/rest/api/3/issue/10001"})
        )
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.jira_url = "https://company.atlassian.net"
            mock_s.return_value.jira_email = "user@co.com"
            mock_s.return_value.jira_token = "token"
            mock_s.return_value.jira_project_key = "ENG"
            p = JiraProvider()
            req = TicketRequest(
                title="SQL injection found",
                description="Details here",
                severity="critical",
                repo="owner/repo",
                pr_number=1,
                file_path="app.py",
            )
            result = p.create_ticket(req)
        assert result.ticket_id == "ENG-1"
        assert result.provider == "jira"
