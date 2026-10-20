"""Pydantic models for GitHub webhook payloads."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class GitHubUser(BaseModel):
    login: str
    id: int
    email: str | None = None
    html_url: str = ""


class GitHubRepo(BaseModel):
    id: int
    name: str
    full_name: str
    clone_url: str
    html_url: str
    default_branch: str = "main"
    private: bool = False


class GitHubLabel(BaseModel):
    id: int
    name: str
    color: str


class PullRequest(BaseModel):
    number: int
    title: str
    body: str | None = None
    state: str
    html_url: str
    diff_url: str
    head: dict[str, Any]
    base: dict[str, Any]
    user: GitHubUser
    draft: bool = False
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    labels: list[GitHubLabel] = Field(default_factory=list)

    @property
    def head_sha(self) -> str:
        return self.head.get("sha", "")

    @property
    def head_ref(self) -> str:
        return self.head.get("ref", "")

    @property
    def base_ref(self) -> str:
        return self.base.get("ref", "")


class PullRequestEvent(BaseModel):
    action: str
    number: int
    pull_request: PullRequest
    repository: GitHubRepo
    sender: GitHubUser
    installation: dict[str, Any] | None = None

    def should_review(self) -> bool:
        """Only trigger a review on open/reopen/synchronize."""
        return self.action in {"opened", "reopened", "synchronize"}


class PushEvent(BaseModel):
    ref: str
    before: str
    after: str
    repository: GitHubRepo
    pusher: dict[str, Any]
    commits: list[dict[str, Any]] = Field(default_factory=list)


class PingEvent(BaseModel):
    zen: str
    hook_id: int
    hook: dict[str, Any]
