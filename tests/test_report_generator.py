"""Tests for the report generator."""
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
