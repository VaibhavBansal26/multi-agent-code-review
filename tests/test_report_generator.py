"""Tests for the report generator."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.file_reviewer import FileFinding
from app.reports.generator import generate_report


def make_finding(severity: str, title: str = "Test finding") -> FileFinding:
    return FileFinding(
        title=title,
        body="Detailed explanation of the issue.",
        file_path="src/example.py",
        line_start=42,
        line_end=45,
        suggestion="# fixed code",
        severity=severity,
    )


class TestReportGenerator:
    def test_empty_findings(self):
        md, data = generate_report(
            repo="org/repo",
            pr_number=1,
            pr_title="test PR",
            author="devuser",
            head_sha="abc123",
            decision="approved",
            findings=[],
        )
        assert "No findings" in md
        assert data["summary"]["total"] == 0
        assert data["decision"] == "approved"

    def test_counts_by_severity(self):
        findings = [
            make_finding("critical"),
            make_finding("critical"),
            make_finding("suggestive"),
            make_finding("optimisation"),
        ]
        md, data = generate_report(
            repo="org/repo", pr_number=2, pr_title="test", author="dev",
            head_sha="def456", decision="changes_requested", findings=findings,
        )
        assert data["summary"]["critical"] == 2
        assert data["summary"]["suggestive"] == 1
        assert data["summary"]["optimisation"] == 1
        assert data["summary"]["total"] == 4

    def test_markdown_contains_severity_sections(self):
        findings = [make_finding("critical", "SQL injection risk")]
        md, _ = generate_report(
            repo="org/repo", pr_number=3, pr_title="test", author="dev",
            head_sha="ghi789", decision="changes_requested", findings=findings,
        )
        assert "CRITICAL" in md
        assert "SQL injection risk" in md
        assert "src/example.py" in md

    def test_json_data_structure(self):
        findings = [make_finding("suggestive", "Refactor this")]
        _, data = generate_report(
            repo="org/repo", pr_number=4, pr_title="test", author="dev",
            head_sha="jkl000", decision="commented", findings=findings,
        )
        assert "findings" in data
        assert len(data["findings"]) == 1
        f = data["findings"][0]
        assert f["severity"] == "suggestive"
        assert f["title"] == "Refactor this"
        assert f["file_path"] == "src/example.py"

    def test_suggestion_in_markdown(self):
        f = make_finding("optimisation")
        f.suggestion = "result = [x for x in items]"
        md, _ = generate_report(
            repo="org/repo", pr_number=5, pr_title="test", author="dev",
            head_sha="mno111", decision="approved", findings=[f],
        )
        assert "Suggested fix" in md
        assert "result = [x for x in items]" in md

    def test_head_sha_truncated_in_markdown(self):
        md, _ = generate_report(
            repo="org/repo", pr_number=6, pr_title="test", author="dev",
            head_sha="abcdef1234567890", decision="approved", findings=[],
        )
        assert "abcdef12" in md
        assert "abcdef1234567890" not in md


# ── Report API endpoint ───────────────────────────────────────────────────────

class TestReportApi:
    def _make_mock_db(self, return_value):
        """Build a FastAPI dependency override for get_db."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = return_value
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def _fake_get_db():
            yield mock_session

        return _fake_get_db

    def test_report_not_found(self, client):
        from app.db.session import get_db
        from app.main import app

        app.dependency_overrides[get_db] = self._make_mock_db(None)
        try:
            resp = client.get("/dashboard/api/reviews/99999/report")
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert resp.status_code == 404

    def test_report_returned_json(self, client):
        from datetime import datetime, timezone
        from app.db.session import get_db
        from app.main import app

        fake_report = MagicMock()
        fake_report.review_id = 1
        fake_report.markdown = "# Report"
        fake_report.json_data = '{"summary": {"total": 2}}'
        fake_report.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)

        app.dependency_overrides[get_db] = self._make_mock_db(fake_report)
        try:
            resp = client.get("/dashboard/api/reviews/1/report")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["review_id"] == 1
        assert "# Report" in data["markdown"]
        assert data["data"]["summary"]["total"] == 2
