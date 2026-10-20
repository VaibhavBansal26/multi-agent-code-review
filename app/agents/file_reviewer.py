"""File review worker agent — reviews a single file diff."""
from __future__ import annotations
from dataclasses import dataclass, field
from app.agents.base import BaseAgent


@dataclass
class FileFinding:
    title: str
    body: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    suggestion: str | None = None
    severity: str = "suggestive"  # critical | suggestive | optimisation
    source: str = "llm"


@dataclass
class FileReviewResult:
    file_path: str
    findings: list[FileFinding] = field(default_factory=list)
    error: str | None = None


class FileReviewAgent(BaseAgent):
    """
    Runs on claude-sonnet-4-6.
    Reviews a single file diff and returns structured findings.
    Fully implemented in Phase 2.
    """

    SYSTEM_PROMPT = """You are an expert code reviewer. Your job is to review a file diff
from a pull request and identify issues. For each issue, output a JSON object with these fields:
- title: concise one-line summary
- body: detailed explanation with context
- file_path: the file being reviewed
- line_start: starting line number (int or null)
- line_end: ending line number (int or null)
- suggestion: optional code snippet showing the fix
- severity: one of "critical" | "suggestive" | "optimisation"

severity definitions:
- critical: bugs, security issues, data loss risks — MUST be fixed before merge
- suggestive: should be fixed, code quality or correctness concern
- optimisation: nice-to-have improvements, style, performance

Return ONLY a JSON array of findings. Empty array if no issues found.
Do not add commentary outside the JSON."""

    def review(self, *, file_path: str, diff: str, context: str = "") -> FileReviewResult:
        """Review a single file diff. Phase 1 stub — implemented in Phase 2."""
        return FileReviewResult(file_path=file_path, findings=[])
