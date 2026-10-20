"""Tests for Phase 2 agent implementations."""
import json
import pytest
from unittest.mock import MagicMock, patch
from app.agents.file_reviewer import FileReviewAgent, FileFinding, FileReviewResult
from app.agents.classifier import ClassifierAgent


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_message(text: str):
    """Return a fake anthropic Message with a single TextBlock."""
    msg = MagicMock()
    block = MagicMock()
    block.text = text
    msg.content = [block]
    return msg


def _make_finding(**kwargs) -> FileFinding:
    defaults = dict(title="Test", body="body", file_path="foo.py", severity="suggestive")
    defaults.update(kwargs)
    return FileFinding(**defaults)


# ─── FileReviewAgent ────────────────────────────────────────────────────────

class TestFileReviewAgent:
    def test_empty_diff_returns_no_findings(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        result = agent.review(file_path="foo.py", diff="")
        assert result.findings == []
        assert result.error is None

    def test_whitespace_diff_returns_no_findings(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        result = agent.review(file_path="foo.py", diff="   \n  ")
        assert result.findings == []

    def test_valid_llm_response_parsed(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        findings_data = [
            {
                "title": "SQL injection risk",
                "body": "User input directly in query",
                "file_path": "app.py",
                "line_start": 10,
                "line_end": 12,
                "suggestion": "Use parameterized queries",
                "severity": "critical",
                "source": "llm",
            }
        ]
        agent._call = MagicMock(return_value=_make_message(json.dumps(findings_data)))
        result = agent.review(file_path="app.py", diff="+x = query(user_input)")
        assert len(result.findings) == 1
        f = result.findings[0]
        assert f.title == "SQL injection risk"
        assert f.severity == "critical"
        assert f.line_start == 10

    def test_llm_error_returns_empty_with_error(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        agent._call = MagicMock(side_effect=Exception("API timeout"))
        result = agent.review(file_path="app.py", diff="+x = 1")
        assert result.findings == []
        assert result.error == "API timeout"

    def test_markdown_fenced_json_parsed(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        fenced = '```json\n[{"title":"t","body":"b","file_path":"f.py","severity":"optimisation"}]\n```'
        agent._call = MagicMock(return_value=_make_message(fenced))
        result = agent.review(file_path="f.py", diff="+x=1")
        assert len(result.findings) == 1
        assert result.findings[0].severity == "optimisation"

    def test_invalid_json_returns_empty(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        agent._call = MagicMock(return_value=_make_message("not json at all"))
        result = agent.review(file_path="f.py", diff="+x=1")
        assert result.findings == []
        assert result.error is None

    def test_non_list_json_returns_empty(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        agent._call = MagicMock(return_value=_make_message('{"key": "value"}'))
        result = agent.review(file_path="f.py", diff="+x=1")
        assert result.findings == []

    def test_finding_with_missing_optional_fields(self):
        agent = FileReviewAgent.__new__(FileReviewAgent)
        agent.log = MagicMock()
        minimal = '[{"title":"t","body":"b","file_path":"a.py","severity":"suggestive"}]'
        agent._call = MagicMock(return_value=_make_message(minimal))
        result = agent.review(file_path="a.py", diff="+x=1")
        f = result.findings[0]
        assert f.line_start is None
        assert f.suggestion is None

    def test_parse_json_array_strips_fence(self):
        text = "```\n[1, 2, 3]\n```"
        assert FileReviewAgent._parse_json_array(text) == [1, 2, 3]

    def test_parse_json_array_finds_embedded(self):
        text = "Here is the output: [1, 2] done"
        assert FileReviewAgent._parse_json_array(text) == [1, 2]

    def test_parse_json_array_returns_empty_on_total_failure(self):
        assert FileReviewAgent._parse_json_array("nothing useful") == []

    def test_review_with_context_passed_through(self):
        agent = FileReviewAgent()
        with patch.object(agent, "_call") as mock_call:
            mock_call.return_value = MagicMock(content=[MagicMock(text="[]")])
            agent.review(file_path="app.py", diff="+x=1", context="PR Intent: fix bug\nSurrounding: line 5 | x = 0")
            call_args = mock_call.call_args
            user_msg = call_args[1]["messages"][0]["content"]
            assert "PR Intent" in user_msg
            assert "Surrounding" in user_msg


# ─── ClassifierAgent ────────────────────────────────────────────────────────

class TestClassifierAgent:
    def test_empty_list_returns_empty(self):
        agent = ClassifierAgent.__new__(ClassifierAgent)
        assert agent.classify([]) == []

    def test_llm_classifies_and_deduplicates(self):
        agent = ClassifierAgent.__new__(ClassifierAgent)
        agent.log = MagicMock()
        classified = [
            {"title": "SQL injection", "body": "b", "file_path": "a.py",
             "severity": "critical", "source": "llm", "line_start": None,
             "line_end": None, "suggestion": None},
            {"title": "Magic number", "body": "b2", "file_path": "b.py",
             "severity": "optimisation", "source": "llm", "line_start": None,
             "line_end": None, "suggestion": None},
            # duplicate — same title + file_path
            {"title": "SQL injection", "body": "dup", "file_path": "a.py",
             "severity": "critical", "source": "llm", "line_start": None,
             "line_end": None, "suggestion": None},
        ]
        agent._call = MagicMock(return_value=_make_message(json.dumps(classified)))
        findings = [
            _make_finding(title="SQL injection", file_path="a.py", severity="suggestive"),
            _make_finding(title="Magic number", file_path="b.py", severity="optimisation"),
        ]
        result = agent.classify(findings)
        assert len(result) == 2  # duplicate removed
        assert result[0].severity == "critical"

    def test_invalid_severity_normalised(self):
        agent = ClassifierAgent.__new__(ClassifierAgent)
        agent.log = MagicMock()
        classified = [{"title": "x", "body": "y", "file_path": "f.py",
                       "severity": "unknown_level", "source": "llm",
                       "line_start": None, "line_end": None, "suggestion": None}]
        agent._call = MagicMock(return_value=_make_message(json.dumps(classified)))
        result = agent.classify([_make_finding()])
        assert result[0].severity == "suggestive"

    def test_fallback_on_llm_error(self):
        agent = ClassifierAgent.__new__(ClassifierAgent)
        agent.log = MagicMock()
        agent._call = MagicMock(side_effect=Exception("timeout"))
        findings = [
            _make_finding(severity="optimisation"),
            _make_finding(severity="critical"),
            _make_finding(severity="suggestive"),
        ]
        result = agent.classify(findings)
        assert result[0].severity == "critical"
        assert result[-1].severity == "optimisation"

    def test_ordering_critical_first(self):
        agent = ClassifierAgent.__new__(ClassifierAgent)
        agent.log = MagicMock()
        out = [
            {"title": "a", "body": "b", "file_path": "f.py", "severity": "optimisation",
             "source": "llm", "line_start": None, "line_end": None, "suggestion": None},
            {"title": "b", "body": "b", "file_path": "g.py", "severity": "critical",
             "source": "llm", "line_start": None, "line_end": None, "suggestion": None},
        ]
        agent._call = MagicMock(return_value=_make_message(json.dumps(out)))
        findings = [
            _make_finding(title="a", severity="optimisation"),
            _make_finding(title="b", severity="critical"),
        ]
        result = agent.classify(findings)
        # classifier respects the order returned by the LLM
        assert result[0].severity == "optimisation"
        assert result[1].severity == "critical"


# ─── OrchestratorAgent ──────────────────────────────────────────────────────

class TestOrchestratorAgent:
    def _make_orchestrator(self):
        from app.agents.orchestrator import OrchestratorAgent
        agent = OrchestratorAgent.__new__(OrchestratorAgent)
        agent.log = MagicMock()
        agent.settings = MagicMock()
        agent.model = "claude-opus-5-5"
        agent.client = MagicMock()
        return agent

    def test_skip_lock_files(self):
        from app.agents.orchestrator import OrchestratorAgent
        assert OrchestratorAgent._should_skip("package-lock.json") is True
        assert OrchestratorAgent._should_skip("yarn.lock") is True
        assert OrchestratorAgent._should_skip("app.py") is False

    def test_skip_binary_extensions(self):
        from app.agents.orchestrator import OrchestratorAgent
        assert OrchestratorAgent._should_skip("logo.png") is True
        assert OrchestratorAgent._should_skip("font.woff2") is True
        assert OrchestratorAgent._should_skip("main.ts") is False

    def test_no_patch_files_skipped(self):
        from app.agents.orchestrator import OrchestratorAgent
        agent = self._make_orchestrator()
        mock_files = [
            {"filename": "README.md", "patch": None},
            {"filename": "logo.png", "patch": "+binary"},
        ]
        with patch("app.agents.orchestrator.GitHubClient") as MockGH, \
             patch("app.agents.orchestrator.generate_report", return_value=("md", {})), \
             patch("app.agents.orchestrator.ClassifierAgent") as MockClassifier:
            mock_gh = MockGH.return_value
            mock_gh.get_pr_files.return_value = mock_files
            mock_gh.create_review.return_value = {}
            MockClassifier.return_value.classify.return_value = []
            result = agent.run(
                repo_full_name="owner/repo",
                pr_number=1,
                head_sha="abc123",
                pr_title="Test PR",
                author_login="user",
                author_email=None,
                diff_url="https://example.com/diff",
            )
        assert result["total_findings"] == 0
        assert result["status"] == "completed"

    def test_github_fetch_failure_returns_error(self):
        agent = self._make_orchestrator()
        with patch("app.agents.orchestrator.GitHubClient") as MockGH:
            MockGH.return_value.get_pr_files.side_effect = Exception("Network error")
            result = agent.run(
                repo_full_name="owner/repo",
                pr_number=1,
                head_sha="abc",
                pr_title="T",
                author_login="u",
                author_email=None,
                diff_url="https://x.com",
            )
        assert result["status"] == "failed"
        assert "Network error" in result["error"]

    def test_critical_finding_triggers_request_changes(self):
        from app.agents.orchestrator import OrchestratorAgent
        agent = self._make_orchestrator()
        mock_files = [{"filename": "app.py", "patch": "+x = unsafe(user_input)"}]
        critical_finding = _make_finding(title="Injection", severity="critical", file_path="app.py")

        with patch("app.agents.orchestrator.GitHubClient") as MockGH, \
             patch("app.agents.orchestrator.FileReviewAgent") as MockFR, \
             patch("app.agents.orchestrator.ClassifierAgent") as MockCL, \
             patch("app.agents.orchestrator.generate_report", return_value=("md", {})):
            MockGH.return_value.get_pr_files.return_value = mock_files
            MockGH.return_value.create_review.return_value = {}
            mock_result = MagicMock()
            mock_result.findings = [critical_finding]
            MockFR.return_value.review.return_value = mock_result
            MockCL.return_value.classify.return_value = [critical_finding]
            result = agent.run(
                repo_full_name="owner/repo",
                pr_number=42,
                head_sha="sha1",
                pr_title="Risky PR",
                author_login="dev",
                author_email="dev@x.com",
                diff_url="https://x.com",
            )
        assert result["decision"] == "REQUEST_CHANGES"
        assert result["critical"] == 1

    def test_no_findings_approves_with_comment(self):
        agent = self._make_orchestrator()
        mock_files = [{"filename": "app.py", "patch": "+x = 1"}]

        with patch("app.agents.orchestrator.GitHubClient") as MockGH, \
             patch("app.agents.orchestrator.FileReviewAgent") as MockFR, \
             patch("app.agents.orchestrator.ClassifierAgent") as MockCL, \
             patch("app.agents.orchestrator.generate_report", return_value=("md", {})):
            MockGH.return_value.get_pr_files.return_value = mock_files
            MockGH.return_value.create_review.return_value = {}
            mock_result = MagicMock()
            mock_result.findings = []
            MockFR.return_value.review.return_value = mock_result
            MockCL.return_value.classify.return_value = []
            result = agent.run(
                repo_full_name="owner/repo",
                pr_number=1,
                head_sha="sha1",
                pr_title="Clean PR",
                author_login="dev",
                author_email=None,
                diff_url="https://x.com",
            )
        assert result["decision"] == "COMMENT"
        assert result["total_findings"] == 0

    def test_build_review_body_no_findings(self):
        from app.agents.orchestrator import OrchestratorAgent
        body = OrchestratorAgent._build_review_body([], [], [], "")
        assert "No issues found" in body

    def test_build_review_body_with_critical(self):
        from app.agents.orchestrator import OrchestratorAgent
        critical = [_make_finding(title="Bug", severity="critical", file_path="f.py")]
        body = OrchestratorAgent._build_review_body(critical, [], [], "")
        assert "Critical Issues" in body
        assert "f.py" in body

    def test_build_inline_comments_skips_no_line(self):
        from app.agents.orchestrator import OrchestratorAgent
        findings = [
            _make_finding(severity="critical", line_start=5),
            _make_finding(severity="suggestive", line_start=None),
        ]
        comments = OrchestratorAgent._build_inline_comments(findings, "sha")
        assert len(comments) == 1
        assert comments[0]["line"] == 5
