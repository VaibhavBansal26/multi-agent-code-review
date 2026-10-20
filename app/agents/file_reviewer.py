"""File review worker agent — reviews a single file diff."""
from __future__ import annotations
import json
import re
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
        """Review a single file diff using LLM analysis."""
        if not diff.strip():
            return FileReviewResult(file_path=file_path, findings=[])

        user_msg = f"File: {file_path}\n\n"
        if context:
            user_msg += f"Context:\n{context}\n\n"
        user_msg += f"Diff:\n```\n{diff}\n```"

        try:
            response = self._call(
                messages=[{"role": "user", "content": user_msg}],
                system=self.SYSTEM_PROMPT,
                max_tokens=4096,
            )
            raw = response.content[0].text.strip()
            findings_data = self._parse_json_array(raw)
            findings = []
            for item in findings_data:
                if not isinstance(item, dict):
                    continue
                findings.append(FileFinding(
                    title=item.get("title", "Unnamed finding"),
                    body=item.get("body", ""),
                    file_path=item.get("file_path", file_path),
                    line_start=item.get("line_start"),
                    line_end=item.get("line_end"),
                    suggestion=item.get("suggestion"),
                    severity=item.get("severity", "suggestive"),
                    source="llm",
                ))
            self.log.info("file_reviewed", file=file_path, findings=len(findings))
            return FileReviewResult(file_path=file_path, findings=findings)
        except Exception as exc:
            self.log.error("file_review_error", file=file_path, error=str(exc))
            return FileReviewResult(file_path=file_path, findings=[], error=str(exc))

    @staticmethod
    def _parse_json_array(text: str) -> list:
        """Extract a JSON array from LLM response, tolerating markdown fences."""
        # Strip markdown code fences
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE)
        text = text.strip()
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            # Try to find a JSON array in the text
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return []
