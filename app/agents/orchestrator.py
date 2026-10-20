"""Orchestrator agent — plans and drives the full PR review pipeline."""
from __future__ import annotations
import structlog
from app.agents.base import BaseAgent
from app.config import get_settings

log = structlog.get_logger(__name__)


class OrchestratorAgent(BaseAgent):
    """
    Runs on claude-opus-5-5.
    Fetches the PR diff, fans out FileReviewAgents per file,
    collects findings, classifies them, posts comments, and decides
    whether to approve or request changes.
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(model=settings.orchestrator_model)

    def run(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        pr_title: str,
        author_login: str,
        author_email: str | None,
        diff_url: str,
    ) -> dict:
        """
        Full pipeline entry point.
        Returns a result dict with findings, decision, and report metadata.
        Phase 1: returns a stub result. Fully implemented in Phase 2.
        """
        self.log.info("orchestrator_run", repo=repo_full_name, pr=pr_number)

        # Phase 1 stub — full implementation added in Phase 2
        return {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "total_findings": 0,
            "critical": 0,
            "suggestive": 0,
            "optimisation": 0,
            "decision": "pending",
            "status": "phase_1_stub",
        }
