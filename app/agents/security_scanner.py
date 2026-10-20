"""Security scanner agent — SAST, secret detection, dependency CVEs."""
from __future__ import annotations
import subprocess
import structlog
from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileFinding

log = structlog.get_logger(__name__)


class SecurityScannerAgent(BaseAgent):
    """
    Combines static analysis tools with LLM-based review for security issues.
    Tools used: Semgrep (SAST), truffleHog (secrets), pip-audit (CVEs).
    Fully implemented in Phase 4.
    """

    def scan(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        files: list[str],
    ) -> dict:
        """Run security scan. Phase 1 stub — implemented in Phase 4."""
        log.info("security_scan_stub", repo=repo_full_name, pr=pr_number)
        return {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "findings": [],
            "status": "phase_1_stub",
        }

    def _run_semgrep(self, path: str) -> list[dict]:
        """Run Semgrep SAST on a local path."""
        try:
            result = subprocess.run(
                ["semgrep", "--config=auto", "--json", path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            import json
            data = json.loads(result.stdout)
            return data.get("results", [])
        except Exception as exc:
            log.error("semgrep_failed", error=str(exc))
            return []
