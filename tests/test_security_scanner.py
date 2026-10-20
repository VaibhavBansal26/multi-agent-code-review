"""Tests for Phase 4 SecurityScannerAgent."""
import json
from unittest.mock import MagicMock, patch

from app.agents.security_scanner import SecurityScannerAgent


def _make_scanner():
    agent = SecurityScannerAgent.__new__(SecurityScannerAgent)
    agent.log = MagicMock()
    agent.model = "claude-sonnet-4-6"
    agent.client = MagicMock()
    return agent


def _make_message(text: str):
    msg = MagicMock()
    block = MagicMock()
    block.text = text
    msg.content = [block]
    return msg


class TestSecurityScannerAgent:
    def test_scan_no_patches_returns_empty(self):
        agent = _make_scanner()
        result = agent.scan(
            repo_full_name="owner/repo",
            pr_number=1,
            head_sha="abc",
            files=["app.py"],
        )
        assert result["status"] == "completed"
        assert result["findings"] == []

    def test_scan_with_clean_patch_returns_empty(self):
        agent = _make_scanner()
        agent._call = MagicMock(return_value=_make_message("[]"))
        result = agent.scan(
            repo_full_name="owner/repo",
            pr_number=1,
            head_sha="abc",
            files=["app.py"],
            patches={"app.py": "+x = 1\n+y = 2"},
        )
        assert result["status"] == "completed"

    def test_secret_scan_detects_api_key(self):
        import os
        import tempfile
        agent = _make_scanner()
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "config.py")
            with open(fpath, "w") as f:
                f.write('API_KEY = "sk-1234567890abcdef"\n')
            findings = agent._run_secret_scan(tmpdir)
        assert len(findings) >= 1
        assert any(f.severity == "critical" for f in findings)
        assert any(f.source == "secret_scan" for f in findings)

    def test_secret_scan_detects_private_key(self):
        import os
        import tempfile
        agent = _make_scanner()
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "key.pem")
            with open(fpath, "w") as f:
                f.write("-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n")
            findings = agent._run_secret_scan(tmpdir)
        assert any("Private key" in f.title for f in findings)

    def test_secret_scan_clean_file_returns_empty(self):
        import os
        import tempfile
        agent = _make_scanner()
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "clean.py")
            with open(fpath, "w") as f:
                f.write("def add(a, b): return a + b\n")
            findings = agent._run_secret_scan(tmpdir)
        assert findings == []

    def test_run_semgrep_not_installed_returns_empty(self):
        agent = _make_scanner()
        with patch("subprocess.run", side_effect=FileNotFoundError("semgrep not found")):
            findings = agent._run_semgrep("/tmp/fake")
        assert findings == []

    def test_run_bandit_not_installed_returns_empty(self):
        agent = _make_scanner()
        with patch("subprocess.run", side_effect=FileNotFoundError("bandit not found")):
            findings = agent._run_bandit("/tmp/fake")
        assert findings == []

    def test_run_semgrep_parses_results(self):
        agent = _make_scanner()
        semgrep_output = json.dumps({
            "results": [{
                "check_id": "python.lang.security.injection.sql",
                "path": "app.py",
                "start": {"line": 42},
                "extra": {
                    "severity": "ERROR",
                    "message": "SQL injection vulnerability found",
                }
            }]
        })
        mock_proc = MagicMock()
        mock_proc.stdout = semgrep_output
        with patch("subprocess.run", return_value=mock_proc):
            findings = agent._run_semgrep("/tmp/test")
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].source == "semgrep"
        assert findings[0].line_start == 42

    def test_run_bandit_parses_results(self):
        agent = _make_scanner()
        bandit_output = json.dumps({
            "results": [{
                "test_id": "B106",
                "issue_text": "Hardcoded password string",
                "issue_severity": "HIGH",
                "issue_confidence": "MEDIUM",
                "filename": "auth.py",
                "line_number": 10,
            }]
        })
        mock_proc = MagicMock()
        mock_proc.stdout = bandit_output
        with patch("subprocess.run", return_value=mock_proc):
            findings = agent._run_bandit("/tmp/test")
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].source == "bandit"

    def test_llm_security_review_parses_findings(self):
        agent = _make_scanner()
        llm_response = json.dumps([{
            "title": "SQL Injection",
            "body": "User input directly in SQL query",
            "file_path": "db.py",
            "line_start": 15,
            "severity": "critical",
            "source": "llm_security",
        }])
        agent._call = MagicMock(return_value=_make_message(llm_response))
        findings = agent._llm_security_review({"db.py": "+query(user_input)"})
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert findings[0].source == "llm_security"

    def test_llm_security_review_handles_error(self):
        agent = _make_scanner()
        agent._call = MagicMock(side_effect=Exception("API error"))
        findings = agent._llm_security_review({"a.py": "+x=1"})
        assert findings == []

    def test_scan_deduplicates_findings(self):
        agent = _make_scanner()
        # Two findings with same title+file_path
        from app.agents.file_reviewer import FileFinding
        dup = FileFinding(
            title="SQL injection", body="b", file_path="a.py", severity="critical", source="semgrep"
        )
        agent._scan_patches = MagicMock(return_value=[dup, dup])
        agent._llm_security_review = MagicMock(return_value=[dup])
        result = agent.scan(
            repo_full_name="r/r",
            pr_number=1,
            head_sha="sha",
            files=["a.py"],
            patches={"a.py": "+x=1"},
        )
        assert result["summary"]["total"] == 1

    def test_scan_summary_counts_critical(self):
        agent = _make_scanner()
        from app.agents.file_reviewer import FileFinding
        critical = FileFinding(
            title="Injection", body="b", file_path="a.py", severity="critical", source="semgrep"
        )
        info_finding = FileFinding(title="Style", body="b2", file_path="b.py", severity="optimisation", source="bandit")
        agent._scan_patches = MagicMock(return_value=[critical, info_finding])
        agent._llm_security_review = MagicMock(return_value=[])
        result = agent.scan(
            repo_full_name="r/r",
            pr_number=1,
            head_sha="sha",
            files=["a.py"],
            patches={"a.py": "+x=1"},
        )
        assert result["summary"]["critical"] == 1
        assert result["summary"]["total"] == 2
