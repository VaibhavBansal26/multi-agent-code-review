"""Tests for the GitHub webhook endpoint."""
import json
from unittest.mock import MagicMock, patch

from tests.conftest import make_signature


def _post_webhook(client, event: str, payload: dict, secret_override: str | None = None):
    body = json.dumps(payload).encode()
    sig = make_signature(body, secret_override or "test-webhook-secret-12345")
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": "test-delivery-001",
            "X-Hub-Signature-256": sig,
        },
    )


class TestWebhookSignature:
    def test_missing_signature_returns_401(self, client):
        resp = client.post(
            "/webhook/github",
            json={"action": "opened"},
            headers={"X-GitHub-Event": "pull_request"},
        )
        assert resp.status_code == 401

    def test_invalid_signature_returns_401(self, client, pr_payload):
        body = json.dumps(pr_payload).encode()
        resp = client.post(
            "/webhook/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=deadbeef",
            },
        )
        assert resp.status_code == 401

    def test_wrong_secret_returns_401(self, client, pr_payload):
        resp = _post_webhook(client, "pull_request", pr_payload, secret_override="wrong-secret")
        assert resp.status_code == 401

    def test_valid_signature_accepted(self, client, ping_payload):
        resp = _post_webhook(client, "ping", ping_payload)
        assert resp.status_code == 200


class TestPingEvent:
    def test_ping_returns_pong(self, client, ping_payload):
        resp = _post_webhook(client, "ping", ping_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pong"
        assert data["hook_id"] == 777


class TestPullRequestEvent:
    def test_pr_opened_queued(self, client, pr_payload):
        mock_task = MagicMock()
        mock_task.id = "task-abc-123"
        with patch("app.queue.tasks.run_pr_review") as mock_celery_task:
            mock_celery_task.apply_async.return_value = mock_task
            resp = _post_webhook(client, "pull_request", pr_payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["pr"] == 42
        assert data["task_id"] == "task-abc-123"

    def test_pr_closed_skipped(self, client, pr_payload):
        pr_payload["action"] = "closed"
        resp = _post_webhook(client, "pull_request", pr_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_pr_synchronized_queued(self, client, pr_payload):
        pr_payload["action"] = "synchronize"
        mock_task = MagicMock()
        mock_task.id = "task-xyz"
        with patch("app.queue.tasks.run_pr_review") as m:
            m.apply_async.return_value = mock_task
            resp = _post_webhook(client, "pull_request", pr_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_pr_reopened_queued(self, client, pr_payload):
        pr_payload["action"] = "reopened"
        mock_task = MagicMock()
        mock_task.id = "task-reopened"
        with patch("app.queue.tasks.run_pr_review") as m:
            m.apply_async.return_value = mock_task
            resp = _post_webhook(client, "pull_request", pr_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_unknown_event_ignored(self, client, ping_payload):
        resp = _post_webhook(client, "issues", ping_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    def test_malformed_pr_payload_returns_422(self, client):
        resp = _post_webhook(client, "pull_request", {"action": "opened", "number": 1})
        assert resp.status_code == 422


class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
