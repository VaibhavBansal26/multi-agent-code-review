"""Finding classifier agent — normalises and ranks findings by severity."""
from __future__ import annotations
import json
import re
from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileFinding

_SEVERITY_ORDER = {"critical": 0, "suggestive": 1, "optimisation": 2}
_VALID_SEVERITIES = set(_SEVERITY_ORDER.keys())


class ClassifierAgent(BaseAgent):
    """
    Runs on claude-haiku-5-5.
    Takes raw findings from all file review agents,
    normalises and re-ranks them with a consistent severity scale.
    """

    SYSTEM_PROMPT = """You are a code review triage specialist.
You receive a list of findings from multiple file reviewers.
Your job is to:
1. Re-assess each finding's severity consistently
2. Remove exact duplicates (same title + file_path)
3. Return the findings ranked: critical first, then suggestive, then optimisation

Severity rules:
- critical: actual bugs, security vulnerabilities, data loss, injection risks, auth bypass
- suggestive: poor error handling, missing validation, code smells, logic concerns
- optimisation: style, naming, minor performance, dead code, doc gaps

Return ONLY a JSON array. Each item keeps all original fields but may have severity updated.
Preserve all original fields exactly. Do not add commentary outside the JSON."""

    def classify(self, findings: list[FileFinding]) -> list[FileFinding]:
        """Classify and rank findings using LLM re-assessment."""
        if not findings:
            return []

        # For small batches go straight to LLM; for 0 findings skip
        raw_dicts = [
            {
                "title": f.title,
                "body": f.body,
                "file_path": f.file_path,
                "line_start": f.line_start,
                "line_end": f.line_end,
                "suggestion": f.suggestion,
                "severity": f.severity,
                "source": f.source,
            }
            for f in findings
        ]

        try:
            response = self._call(
                messages=[{
                    "role": "user",
                    "content": f"Classify and rank these findings:\n{json.dumps(raw_dicts, indent=2)}",
                }],
                system=self.SYSTEM_PROMPT,
                max_tokens=4096,
            )
            raw = response.content[0].text.strip()
            classified = self._parse_json_array(raw)
            result = []
            seen = set()
            for item in classified:
                if not isinstance(item, dict):
                    continue
                key = (item.get("title", ""), item.get("file_path", ""))
                if key in seen:
                    continue
                seen.add(key)
                sev = item.get("severity", "suggestive")
                if sev not in _VALID_SEVERITIES:
                    sev = "suggestive"
                result.append(FileFinding(
                    title=item.get("title", ""),
                    body=item.get("body", ""),
                    file_path=item.get("file_path", ""),
                    line_start=item.get("line_start"),
                    line_end=item.get("line_end"),
                    suggestion=item.get("suggestion"),
                    severity=sev,
                    source=item.get("source", "llm"),
                ))
            self.log.info("findings_classified", total=len(result))
            return result
        except Exception as exc:
            self.log.error("classifier_error", error=str(exc))
            # Fallback: just sort by severity
            return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 1))

    @staticmethod
    def _parse_json_array(text: str) -> list:
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n?```$", "", text, flags=re.MULTILINE)
        text = text.strip()
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return []
