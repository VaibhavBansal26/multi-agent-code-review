"""Shared test fixtures."""
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import get_settings


TEST_SECRET = "test-webhook-secret-12345"


def make_signature(body: bytes, secret: str = TEST_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(monkeypatch):
    """FastAPI test client with patched settings."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    from app.config import get_settings
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def pr_payload() -> dict:
    return {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "title": "feat: add user authentication",
            "body": "Adds JWT-based auth",
            "state": "open",
            "html_url": "https://github.com/org/repo/pull/42",
            "diff_url": "https://github.com/org/repo/pull/42.diff",
            "draft": False,
            "additions": 120,
            "deletions": 15,
            "changed_files": 4,
            "labels": [],
            "head": {"sha": "abc1234567890", "ref": "feat/auth"},
            "base": {"sha": "def0987654321", "ref": "main"},
            "user": {
                "login": "vaibhavbansal26",
                "id": 12345,
                "email": "vaibhav.bansal2020@gmail.com",
                "html_url": "https://github.com/vaibhavbansal26",
            },
        },
        "repository": {
            "id": 99999,
            "name": "repo",
            "full_name": "org/repo",
            "clone_url": "https://github.com/org/repo.git",
            "html_url": "https://github.com/org/repo",
            "default_branch": "main",
            "private": False,
        },
        "sender": {
            "login": "vaibhavbansal26",
            "id": 12345,
            "html_url": "https://github.com/vaibhavbansal26",
        },
    }


@pytest.fixture
def ping_payload() -> dict:
    return {
        "zen": "Design for failure.",
        "hook_id": 777,
        "hook": {"type": "App", "id": 777, "active": True},
    }
