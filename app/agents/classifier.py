"""Finding classifier agent — normalises and ranks findings by severity."""
from __future__ import annotations
from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileFinding


class ClassifierAgent(BaseAgent):
    """
    Runs on claude-haiku-5-5.
    Takes raw findings from all file review agents,
    normalises and re-ranks them with a consistent severity scale.
    Fully implemented in Phase 2.
    """

    SYSTEM_PROMPT = """You are a code review triage specialist.
You receive a list of findings from multiple file reviewers.
Your job is to:
1. Re-assess each finding's severity consistently
2. Remove exact duplicates
3. Return the findings ranked: critical first, then suggestive, then optimisation

Return ONLY a JSON array. Each item keeps all original fields but may have severity updated."""

    def classify(self, findings: list[FileFinding]) -> list[FileFinding]:
        """Classify and rank findings. Phase 1 stub — implemented in Phase 2."""
        return sorted(
            findings,
            key=lambda f: {"critical": 0, "suggestive": 1, "optimisation": 2}.get(f.severity, 1),
        )
