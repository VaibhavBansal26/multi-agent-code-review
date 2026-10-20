"""Tests for LangGraph-based graph orchestrator."""
from unittest.mock import patch

from app.agents.file_reviewer import FileFinding, FileReviewResult
from app.agents.graph_orchestrator import (
    ReviewState,
    build_review_graph,
    classify,
    deep_security_scan,
    fan_out_review,
    fetch_pr,
    should_deep_scan,
)


def _base_state(**overrides) -> ReviewState:
    state: ReviewState = {
        "repo": "owner/repo",
        "pr_number": 1,
        "head_sha": "abc123",
        "pr_title": "Test PR",
        "author_login": "dev",
        "author_email": None,
        "diff_url": "",
        "files": [],
        "reviewable_files": [],
        "pr_detail": {},
        "intent_summary": "",
        "convention_hints": "",
        "base_ref": "main",
        "all_findings": [],
        "classified_findings": [],
        "security_scan_done": False,
        "decision": "COMMENT",
        "report_md": "",
        "report_json": "",
        "error": None,
        "status": "pending",
    }
    state.update(overrides)
    return state


class TestShouldDeepScan:
    def test_routes_to_deep_scan_when_critical(self):
        finding = FileFinding(title="SQLi", body="b", file_path="a.py", severity="critical")
        state = _base_state(classified_findings=[finding], security_scan_done=False)
        assert should_deep_scan(state) == "deep_security_scan"

    def test_skips_deep_scan_when_already_done(self):
        finding = FileFinding(title="SQLi", body="b", file_path="a.py", severity="critical")
        state = _base_state(classified_findings=[finding], security_scan_done=True)
        assert should_deep_scan(state) == "post_review"

    def test_skips_deep_scan_when_no_critical(self):
        finding = FileFinding(title="Style", body="b", file_path="a.py", severity="suggestive")
        state = _base_state(classified_findings=[finding], security_scan_done=False)
        assert should_deep_scan(state) == "post_review"

    def test_skips_deep_scan_when_no_findings(self):
        state = _base_state(classified_findings=[], security_scan_done=False)
        assert should_deep_scan(state) == "post_review"


class TestFetchPrNode:
    def test_fetch_pr_success(self):
        files = [{"filename": "app.py", "patch": "+x=1"}]
        with patch("app.agents.graph_orchestrator.GitHubClient") as mock_gh_cls:
            mock_gh = mock_gh_cls.return_value
            mock_gh.get_pr_files.return_value = files
            mock_gh.get_pr_detail.return_value = {
                "title": "t", "body": "", "labels": [],
                "base_branch": "main", "head_branch": "feat",
            }
            result = fetch_pr(_base_state())
        assert result["files"] == files
        assert len(result["reviewable_files"]) == 1
        assert result["base_ref"] == "main"
        assert result["error"] is None

    def test_fetch_pr_failure_returns_error(self):
        with patch("app.agents.graph_orchestrator.GitHubClient") as mock_gh_cls:
            mock_gh_cls.return_value.get_pr_files.side_effect = Exception("network error")
            result = fetch_pr(_base_state())
        assert result["error"] == "network error"
        assert result["status"] == "failed"

    def test_fetch_pr_skips_binary_files(self):
        files = [
            {"filename": "app.py", "patch": "+x=1"},
            {"filename": "logo.png", "patch": None},
        ]
        with patch("app.agents.graph_orchestrator.GitHubClient") as mock_gh_cls:
            mock_gh = mock_gh_cls.return_value
            mock_gh.get_pr_files.return_value = files
            mock_gh.get_pr_detail.return_value = {
                "title": "t", "body": "", "labels": [],
                "base_branch": "main", "head_branch": "feat",
            }
            result = fetch_pr(_base_state())
        assert len(result["reviewable_files"]) == 1
        assert result["reviewable_files"][0]["filename"] == "app.py"


class TestFanOutReviewNode:
    def test_fan_out_review_collects_findings(self):
        finding = FileFinding(title="Bug", body="b", file_path="app.py", severity="critical")
        mock_result = FileReviewResult(file_path="app.py", findings=[finding])
        state = _base_state(
            reviewable_files=[{"filename": "app.py", "patch": "+x=1"}],
            repo="owner/repo",
            base_ref="main",
        )
        with patch("app.agents.graph_orchestrator.FileReviewAgent") as mock_cls, \
             patch("app.agents.graph_orchestrator.GitHubClient") as mock_gh_cls:
            mock_cls.return_value.review.return_value = mock_result
            mock_gh_cls.return_value.get_file_content.return_value = ""
            result = fan_out_review(state)
        assert len(result["all_findings"]) == 1

    def test_fan_out_skips_on_error_state(self):
        state = _base_state(error="some error")
        result = fan_out_review(state)
        assert result["all_findings"] == []

    def test_fan_out_empty_reviewable(self):
        state = _base_state(reviewable_files=[], repo="owner/repo", base_ref="main")
        result = fan_out_review(state)
        assert result["all_findings"] == []


class TestClassifyNode:
    def test_classify_sets_decision_request_changes(self):
        finding = FileFinding(title="SQLi", body="b", file_path="a.py", severity="critical")
        state = _base_state(all_findings=[finding])
        with patch("app.agents.graph_orchestrator.ClassifierAgent") as mock_cls:
            mock_cls.return_value.classify.return_value = [finding]
            result = classify(state)
        assert result["decision"] == "REQUEST_CHANGES"
        assert len(result["classified_findings"]) == 1

    def test_classify_comment_when_no_critical(self):
        finding = FileFinding(title="Style", body="b", file_path="a.py", severity="suggestive")
        state = _base_state(all_findings=[finding])
        with patch("app.agents.graph_orchestrator.ClassifierAgent") as mock_cls:
            mock_cls.return_value.classify.return_value = [finding]
            result = classify(state)
        assert result["decision"] == "COMMENT"

    def test_classify_empty_findings(self):
        state = _base_state(all_findings=[])
        result = classify(state)
        assert result["classified_findings"] == []
        assert result["decision"] == "COMMENT"


class TestDeepSecurityScanNode:
    def test_deep_scan_sets_done_flag(self):
        finding = FileFinding(title="SQLi", body="b", file_path="a.py", severity="critical")
        state = _base_state(
            classified_findings=[finding],
            reviewable_files=[{"filename": "a.py", "patch": "+x=1"}],
        )
        with patch("app.agents.graph_orchestrator.SecurityScannerAgent") as mock_cls:
            mock_cls.return_value.scan.return_value = {"findings": []}
            result = deep_security_scan(state)
        assert result["security_scan_done"] is True

    def test_deep_scan_exception_still_sets_done(self):
        finding = FileFinding(title="SQLi", body="b", file_path="a.py", severity="critical")
        state = _base_state(
            classified_findings=[finding],
            reviewable_files=[{"filename": "a.py", "patch": "+x=1"}],
        )
        with patch("app.agents.graph_orchestrator.SecurityScannerAgent") as mock_cls:
            mock_cls.return_value.scan.side_effect = Exception("scan error")
            result = deep_security_scan(state)
        assert result["security_scan_done"] is True


class TestGraphCompiles:
    def test_graph_builds_without_error(self):
        graph = build_review_graph()
        assert graph is not None

    def test_graph_orchestrator_agent_instantiates(self):
        from app.agents.graph_orchestrator import GraphOrchestratorAgent
        agent = GraphOrchestratorAgent()
        assert agent.graph is not None
