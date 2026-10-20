"""Abstract ticket provider interface + Jira / Linear adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class TicketRequest:
    title: str
    description: str
    severity: str
    repo: str
    pr_number: int
    file_path: str
    line_start: int | None = None
    labels: list[str] | None = None


@dataclass
class TicketResult:
    provider: str
    ticket_id: str
    url: str


class TicketProvider(ABC):
    @abstractmethod
    def create_ticket(self, req: TicketRequest) -> TicketResult:
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        ...


class JiraProvider(TicketProvider):
    def __init__(self):
        settings = get_settings()
        self.url = settings.jira_url
        self.email = settings.jira_email
        self.token = settings.jira_token
        self.project = settings.jira_project_key

    def is_configured(self) -> bool:
        return bool(self.url and self.email and self.token)

    def create_ticket(self, req: TicketRequest) -> TicketResult:
        payload = {
            "fields": {
                "project": {"key": self.project},
                "summary": f"[MACRV] {req.title}",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": req.description}]}],
                },
                "issuetype": {"name": "Bug" if req.severity == "critical" else "Task"},
                "labels": ["macrv", req.severity] + (req.labels or []),
            }
        }
        with httpx.Client(auth=(self.email, self.token), timeout=15) as client:
            resp = client.post(f"{self.url}/rest/api/3/issue", json=payload)
            resp.raise_for_status()
            data = resp.json()
            issue_key = data["key"]
            return TicketResult(
                provider="jira",
                ticket_id=issue_key,
                url=f"{self.url}/browse/{issue_key}",
            )


class LinearProvider(TicketProvider):
    API = "https://api.linear.app/graphql"

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.linear_api_key
        self.team_id = settings.linear_team_id

    def is_configured(self) -> bool:
        return bool(self.api_key and self.team_id)

    def create_ticket(self, req: TicketRequest) -> TicketResult:
        priority = 1 if req.severity == "critical" else 3
        mutation = """
        mutation CreateIssue($title: String!, $description: String, $teamId: String!, $priority: Int) {
          issueCreate(input: {title: $title, description: $description, teamId: $teamId, priority: $priority}) {
            success
            issue { id identifier url }
          }
        }"""
        with httpx.Client(headers={"Authorization": self.api_key}, timeout=15) as client:
            resp = client.post(
                self.API,
                json={
                    "query": mutation,
                    "variables": {
                        "title": f"[MACRV] {req.title}",
                        "description": req.description,
                        "teamId": self.team_id,
                        "priority": priority,
                    },
                },
            )
            resp.raise_for_status()
            issue = resp.json()["data"]["issueCreate"]["issue"]
            return TicketResult(provider="linear", ticket_id=issue["identifier"], url=issue["url"])


def get_ticket_provider() -> TicketProvider | None:
    """Return the first configured ticket provider."""
    for provider in [JiraProvider(), LinearProvider()]:
        if provider.is_configured():
            return provider
    return None
