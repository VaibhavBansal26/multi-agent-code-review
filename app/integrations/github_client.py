"""GitHub API client — diff fetching, PR comments, reviews."""
from __future__ import annotations
import structlog
import httpx
from app.config import get_settings

log = structlog.get_logger(__name__)


class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: str | None = None):
        settings = get_settings()
        self.token = token or settings.github_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a pull request."""
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}"
        with httpx.Client(headers={**self.headers, "Accept": "application/vnd.github.v3.diff"}) as client:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text

    def get_pr_files(self, repo: str, pr_number: int) -> list[dict]:
        """List files changed in a PR."""
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}/files"
        with httpx.Client(headers=self.headers) as client:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()

    def create_review(
        self,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        event: str,  # "APPROVE" | "REQUEST_CHANGES" | "COMMENT"
        comments: list[dict] | None = None,
    ) -> dict:
        """Submit a PR review with optional inline comments."""
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}/reviews"
        payload: dict = {"commit_id": commit_id, "body": body, "event": event}
        if comments:
            payload["comments"] = comments
        with httpx.Client(headers=self.headers) as client:
            resp = client.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            log.info("review_posted", repo=repo, pr=pr_number, review_event=event)
            return resp.json()

    def add_pr_comment(self, repo: str, pr_number: int, body: str) -> dict:
        """Post a top-level PR comment (not inline)."""
        url = f"{self.BASE}/repos/{repo}/issues/{pr_number}/comments"
        with httpx.Client(headers=self.headers) as client:
            resp = client.post(url, json={"body": body}, timeout=30)
            resp.raise_for_status()
            return resp.json()

    def get_pr_detail(self, repo: str, pr_number: int) -> dict:
        """Fetch PR metadata: title, body, labels, branches."""
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}"
        with httpx.Client(headers=self.headers) as client:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "body": data.get("body", "") or "",
                "labels": [l["name"] for l in data.get("labels", [])],
                "base_branch": data.get("base", {}).get("ref", "main"),
                "head_branch": data.get("head", {}).get("ref", ""),
                "author": data.get("user", {}).get("login", ""),
            }

    def get_file_content(self, repo: str, ref: str, path: str) -> str:
        """Fetch raw file content at a given ref (base branch SHA)."""
        import base64
        url = f"{self.BASE}/repos/{repo}/contents/{path}"
        with httpx.Client(headers=self.headers) as client:
            resp = client.get(url, params={"ref": ref}, timeout=30)
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
            data = resp.json()
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return data.get("content", "")

    def get_commit(self, repo: str, sha: str) -> dict:
        url = f"{self.BASE}/repos/{repo}/commits/{sha}"
        with httpx.Client(headers=self.headers) as client:
            resp = client.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
