"""Tests for GitHub client and review progress endpoint."""
import json
import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch


# ─── GitHubClient ────────────────────────────────────────────────────────────

class TestGitHubClient:
    def _make_client(self, token="gh-test-token"):
        from app.integrations.github_client import GitHubClient
        with patch("app.integrations.github_client.get_settings") as mock_settings:
            mock_settings.return_value.github_token = token
            client = GitHubClient(token=token)
        return client

    @respx.mock
    def test_get_pr_files_returns_list(self):
        client = self._make_client()
        files_data = [{"filename": "app.py", "patch": "+x=1", "status": "modified"}]
        respx.get("https://api.github.com/repos/owner/repo/pulls/1/files").mock(
            return_value=httpx.Response(200, json=files_data)
        )
        result = client.get_pr_files("owner/repo", 1)
        assert result == files_data

    @respx.mock
    def test_get_pr_files_raises_on_error(self):
        client = self._make_client()
        respx.get("https://api.github.com/repos/owner/repo/pulls/1/files").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            client.get_pr_files("owner/repo", 1)

    @respx.mock
    def test_get_pr_diff_returns_text(self):
        client = self._make_client()
        respx.get("https://api.github.com/repos/owner/repo/pulls/1").mock(
            return_value=httpx.Response(200, text="diff --git a/f b/f\n+added line")
        )
        diff = client.get_pr_diff("owner/repo", 1)
        assert "added line" in diff

    @respx.mock
    def test_add_pr_comment(self):
        client = self._make_client()
        respx.post("https://api.github.com/repos/owner/repo/issues/42/comments").mock(
            return_value=httpx.Response(201, json={"id": 999, "body": "hello"})
        )
        result = client.add_pr_comment("owner/repo", 42, "hello")
        assert result["id"] == 999

    @respx.mock
    def test_create_review_request_changes(self):
        client = self._make_client()
        respx.post("https://api.github.com/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json={"id": 1, "state": "CHANGES_REQUESTED"})
        )
        result = client.create_review(
            repo="owner/repo",
            pr_number=1,
            commit_id="abc123",
            body="Review body",
            event="REQUEST_CHANGES",
        )
        assert result["state"] == "CHANGES_REQUESTED"

    @respx.mock
    def test_create_review_with_inline_comments(self):
        client = self._make_client()
        respx.post("https://api.github.com/repos/owner/repo/pulls/1/reviews").mock(
            return_value=httpx.Response(200, json={"id": 2, "state": "COMMENT"})
        )
        comments = [{"path": "app.py", "line": 10, "body": "Issue here"}]
        result = client.create_review(
            repo="owner/repo",
            pr_number=1,
            commit_id="sha123",
            body="comments",
            event="COMMENT",
            comments=comments,
        )
        assert result["id"] == 2

    @respx.mock
    def test_get_commit(self):
        client = self._make_client()
        commit_data = {"sha": "abc123", "commit": {"message": "Fix bug"}}
        respx.get("https://api.github.com/repos/owner/repo/commits/abc123").mock(
            return_value=httpx.Response(200, json=commit_data)
        )
        result = client.get_commit("owner/repo", "abc123")
        assert result["sha"] == "abc123"


# ─── Progress endpoint ───────────────────────────────────────────────────────

class TestProgressEndpoint:
    def test_pending_task(self, client):
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        with patch("app.queue.celery_app.celery_app") as mock_celery:
            mock_celery.AsyncResult.return_value = mock_result
            resp = client.get("/reviews/task-123/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["progress_pct"] == 0

    def test_in_progress_task(self, client):
        mock_result = MagicMock()
        mock_result.state = "STARTED"
        mock_result.info = {"progress_pct": 40, "reviewed": 2, "total": 5}
        with patch("app.queue.celery_app.celery_app") as mock_celery:
            mock_celery.AsyncResult.return_value = mock_result
            resp = client.get("/reviews/task-456/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"
        assert data["progress_pct"] == 40
        assert data["reviewed"] == 2

    def test_completed_task(self, client):
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.result = {
            "decision": "REQUEST_CHANGES",
            "total_findings": 3,
            "critical": 1,
            "suggestive": 2,
            "optimisation": 0,
        }
        with patch("app.queue.celery_app.celery_app") as mock_celery:
            mock_celery.AsyncResult.return_value = mock_result
            resp = client.get("/reviews/task-789/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["progress_pct"] == 100
        assert data["decision"] == "REQUEST_CHANGES"
        assert data["critical"] == 1

    def test_failed_task(self, client):
        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.info = Exception("Worker crashed")
        with patch("app.queue.celery_app.celery_app") as mock_celery:
            mock_celery.AsyncResult.return_value = mock_result
            resp = client.get("/reviews/task-fail/progress")
        assert resp.status_code == 500
        assert "failed" in resp.json()["status"]
