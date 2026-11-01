"""Tests for Phase 5: email, Slack, and ticket provider notifications."""
from unittest.mock import MagicMock, patch

import httpx
import respx

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
        mock_sendgrid_module = MagicMock()
        mock_sg_client = MagicMock()
        mock_sendgrid_module.SendGridAPIClient.return_value = mock_sg_client
        mock_sg_client.send.return_value = MagicMock(status_code=202)
        mock_mail_module = MagicMock()
        mocked = {"sendgrid": mock_sendgrid_module, "sendgrid.helpers.mail": mock_mail_module}
        with patch.dict(sys.modules, mocked):
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

    @respx.mock
    def test_linear_create_ticket(self):
        from app.integrations.ticket_provider import LinearProvider, TicketRequest
        respx.post("https://api.linear.app/graphql").mock(
            return_value=httpx.Response(200, json={
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "abc123", "identifier": "ENG-42", "url": "https://linear.app/team/ENG-42"},
                    }
                }
            })
        )
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.linear_api_key = "lin_api_key_test"
            mock_s.return_value.linear_team_id = "team-xyz"
            p = LinearProvider()
            req = TicketRequest(
                title="Hardcoded secret found",
                description="AWS key exposed in config.py",
                severity="critical",
                repo="owner/repo",
                pr_number=5,
                file_path="config.py",
            )
            result = p.create_ticket(req)
        assert result.ticket_id == "ENG-42"
        assert result.provider == "linear"
        assert "linear.app" in result.url

    def test_get_ticket_provider_returns_jira_when_configured(self):
        from app.integrations.ticket_provider import JiraProvider, get_ticket_provider
        with patch("app.integrations.ticket_provider.get_settings") as mock_s:
            mock_s.return_value.jira_url = "https://co.atlassian.net"
            mock_s.return_value.jira_email = "u@co.com"
            mock_s.return_value.jira_token = "tok"
            mock_s.return_value.jira_project_key = "ENG"
            mock_s.return_value.linear_api_key = ""
            mock_s.return_value.linear_team_id = ""
            provider = get_ticket_provider()
        assert isinstance(provider, JiraProvider)


# ─── Notification Dispatcher ─────────────────────────────────────────────────

class TestNotificationDispatcher:
    def test_no_channels_configured(self):
        from app.integrations.notification_dispatcher import NotificationDispatcher
        from app.agents.file_reviewer import FileFinding
        with (
            patch("app.integrations.notification_dispatcher.send_critical_finding_email", return_value=False),
            patch("app.integrations.notification_dispatcher.send_review_notification", return_value=False),
            patch("app.integrations.notification_dispatcher.get_ticket_provider", return_value=None),
        ):
            d = NotificationDispatcher()
            result = d.dispatch(
                repo="owner/repo",
                pr_number=1,
                pr_title="Test PR",
                author_login="alice",
                author_email=None,
                decision="COMMENT",
                findings=[],
            )
        assert result.email_sent is False
        assert result.slack_sent is False
        assert result.tickets_created == []
        assert result.any_sent is False

    def test_email_only_when_author_email_and_critical(self):
        from app.integrations.notification_dispatcher import NotificationDispatcher
        from app.agents.file_reviewer import FileFinding
        critical_finding = FileFinding(
            title="SQL injection", body="details", file_path="app.py", severity="critical", source="test"
        )
        with (
            patch("app.integrations.notification_dispatcher.send_critical_finding_email", return_value=True) as mock_email,
            patch("app.integrations.notification_dispatcher.send_review_notification", return_value=False),
            patch("app.integrations.notification_dispatcher.get_ticket_provider", return_value=None),
        ):
            d = NotificationDispatcher()
            result = d.dispatch(
                repo="owner/repo",
                pr_number=1,
                pr_title="Risky PR",
                author_login="bob",
                author_email="bob@example.com",
                decision="REQUEST_CHANGES",
                findings=[critical_finding],
            )
        mock_email.assert_called_once()
        assert result.email_sent is True

    def test_email_skipped_without_author_email(self):
        from app.integrations.notification_dispatcher import NotificationDispatcher
        from app.agents.file_reviewer import FileFinding
        critical_finding = FileFinding(
            title="SQL injection", body="details", file_path="app.py", severity="critical", source="test"
        )
        with (
            patch("app.integrations.notification_dispatcher.send_critical_finding_email", return_value=True) as mock_email,
            patch("app.integrations.notification_dispatcher.send_review_notification", return_value=True),
            patch("app.integrations.notification_dispatcher.get_ticket_provider", return_value=None),
        ):
            d = NotificationDispatcher()
            result = d.dispatch(
                repo="r", pr_number=1, pr_title="t",
                author_login="bob", author_email=None,
                decision="REQUEST_CHANGES",
                findings=[critical_finding],
            )
        mock_email.assert_not_called()
        assert result.email_sent is False

    def test_tickets_created_for_critical(self):
        from app.integrations.notification_dispatcher import NotificationDispatcher
        from app.agents.file_reviewer import FileFinding
        from app.integrations.ticket_provider import TicketResult
        critical_finding = FileFinding(
            title="Hardcoded secret", body="AWS key found", file_path="config.py",
            severity="critical", source="secret_scan",
        )
        mock_provider = MagicMock()
        mock_provider.create_ticket.return_value = TicketResult(
            provider="jira", ticket_id="ENG-99", url="https://jira.example.com/ENG-99"
        )
        with (
            patch("app.integrations.notification_dispatcher.send_critical_finding_email", return_value=False),
            patch("app.integrations.notification_dispatcher.send_review_notification", return_value=True),
            patch("app.integrations.notification_dispatcher.get_ticket_provider", return_value=mock_provider),
        ):
            d = NotificationDispatcher()
            result = d.dispatch(
                repo="owner/repo", pr_number=3, pr_title="Add config",
                author_login="carol", author_email=None,
                decision="REQUEST_CHANGES",
                findings=[critical_finding],
            )
        assert len(result.tickets_created) == 1
        assert result.tickets_created[0].ticket_id == "ENG-99"
        assert "tickets(ENG-99)" in result.summary()

    def test_summary_all_channels(self):
        from app.integrations.notification_dispatcher import NotificationResult
        from app.integrations.ticket_provider import TicketResult
        r = NotificationResult(
            email_sent=True,
            slack_sent=True,
            tickets_created=[TicketResult(provider="linear", ticket_id="LIN-1", url="http://x")],
        )
        assert r.any_sent is True
        assert "email" in r.summary()
        assert "slack" in r.summary()
        assert "tickets(LIN-1)" in r.summary()


# ── Integrations API endpoint ─────────────────────────────────────────────────

class TestIntegrationsApi:
    def test_integrations_list_returns_five_ids(self, client):
        resp = client.get("/dashboard/api/integrations")
        assert resp.status_code == 200
        data = resp.json()
        ids = {i["id"] for i in data["integrations"]}
        assert ids == {"email", "slack", "jira", "linear", "weave"}

    def test_integrations_all_unconfigured_by_default(self, client):
        resp = client.get("/dashboard/api/integrations")
        assert resp.status_code == 200
        for integration in resp.json()["integrations"]:
            assert integration["configured"] is False

    def test_integration_test_unknown_channel(self, client):
        resp = client.post("/dashboard/api/integrations/test", json={"channel": "unknown"})
        assert resp.status_code == 400

    def test_integration_test_email_not_configured(self, client):
        resp = client.post("/dashboard/api/integrations/test", json={"channel": "email"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "not configured" in resp.json()["error"]
