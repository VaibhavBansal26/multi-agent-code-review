"""Tests for Pydantic webhook models and DB models."""
import pytest
from app.webhook.models import PullRequestEvent, PingEvent, GitHubUser, GitHubRepo


class TestPullRequestEvent:
    def test_should_review_opened(self, pr_payload):
        event = PullRequestEvent(**pr_payload)
        assert event.should_review() is True

    def test_should_review_synchronize(self, pr_payload):
        pr_payload["action"] = "synchronize"
        event = PullRequestEvent(**pr_payload)
        assert event.should_review() is True

    def test_should_review_reopened(self, pr_payload):
        pr_payload["action"] = "reopened"
        event = PullRequestEvent(**pr_payload)
        assert event.should_review() is True

    def test_should_not_review_closed(self, pr_payload):
        pr_payload["action"] = "closed"
        event = PullRequestEvent(**pr_payload)
        assert event.should_review() is False

    def test_head_sha(self, pr_payload):
        event = PullRequestEvent(**pr_payload)
        assert event.pull_request.head_sha == "abc1234567890"

    def test_head_ref(self, pr_payload):
        event = PullRequestEvent(**pr_payload)
        assert event.pull_request.head_ref == "feat/auth"

    def test_base_ref(self, pr_payload):
        event = PullRequestEvent(**pr_payload)
        assert event.pull_request.base_ref == "main"


class TestPingEvent:
    def test_ping_parsed(self, ping_payload):
        event = PingEvent(**ping_payload)
        assert event.hook_id == 777
        assert event.zen == "Design for failure."


class TestDBModels:
    def test_review_status_enum(self):
        from app.db.models import ReviewStatus
        assert ReviewStatus.PENDING == "pending"
        assert ReviewStatus.COMPLETED == "completed"

    def test_severity_enum(self):
        from app.db.models import Severity
        assert Severity.CRITICAL == "critical"
        assert Severity.SUGGESTIVE == "suggestive"
        assert Severity.OPTIMISATION == "optimisation"

    def test_review_decision_enum(self):
        from app.db.models import ReviewDecision
        assert ReviewDecision.APPROVED == "approved"
        assert ReviewDecision.CHANGES_REQUESTED == "changes_requested"
