"""Tests for Phase 6 evaluation harness."""
import pytest
from unittest.mock import MagicMock, patch
from app.evals.harness import EvalCase, EvalHarness, EvalResult
from app.agents.file_reviewer import FileFinding, FileReviewResult


def _make_harness():
    return EvalHarness(use_weave=False, use_langsmith=False)


def _make_case(**kwargs) -> EvalCase:
    defaults = dict(
        id="test-001",
        repo="owner/repo",
        pr_number=1,
        head_sha="abc123",
        pr_title="Test PR",
        author_login="dev",
        diff="+x = unsafe_query(user_input)",
        expected_findings=[{"title": "SQL injection", "severity": "critical"}],
        expected_decision="REQUEST_CHANGES",
    )
    defaults.update(kwargs)
    return EvalCase(**defaults)


class TestEvalScoring:
    def test_score_exact_match(self):
        actual = [{"title": "SQL injection", "severity": "critical"}]
        expected = [{"title": "SQL injection", "severity": "critical"}]
        p, r, f1 = EvalHarness._score_findings(actual, expected)
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_score_no_expected_no_actual(self):
        p, r, f1 = EvalHarness._score_findings([], [])
        assert p == 1.0
        assert r == 1.0
        assert f1 == 1.0

    def test_score_false_positive(self):
        actual = [{"title": "XSS", "severity": "critical"}]
        expected = []
        p, r, f1 = EvalHarness._score_findings(actual, expected)
        assert p == 0.0
        assert f1 == 0.0

    def test_score_false_negative(self):
        actual = []
        expected = [{"title": "SQL injection"}]
        p, r, f1 = EvalHarness._score_findings(actual, expected)
        assert r == 0.0
        assert f1 == 0.0

    def test_score_partial_match(self):
        actual = [{"title": "sql injection"}, {"title": "false positive"}]
        expected = [{"title": "SQL injection"}, {"title": "missing finding"}]
        p, r, f1 = EvalHarness._score_findings(actual, expected)
        assert p == 0.5  # 1 tp, 1 fp
        assert r == 0.5  # 1 tp, 1 fn
        assert abs(f1 - 0.5) < 0.01

    def test_score_case_insensitive(self):
        actual = [{"title": "SQL INJECTION"}]
        expected = [{"title": "sql injection"}]
        p, r, f1 = EvalHarness._score_findings(actual, expected)
        assert f1 == 1.0


class TestEvalHarness:
    def test_run_case_correct_decision(self):
        harness = _make_harness()
        case = _make_case()
        mock_result = FileReviewResult(
            file_path="PR",
            findings=[
                FileFinding(title="SQL injection", body="b", file_path="a.py", severity="critical")
            ],
        )
        mock_classifier_result = [
            FileFinding(title="SQL injection", body="b", file_path="a.py", severity="critical")
        ]
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review, \
             patch("app.agents.classifier.ClassifierAgent.classify") as mock_classify:
            mock_review.return_value = mock_result
            mock_classify.return_value = mock_classifier_result
            result = harness.run_case(case)
        assert result.decision_correct is True
        assert result.f1 == 1.0
        assert result.latency_ms > 0

    def test_run_case_wrong_decision(self):
        harness = _make_harness()
        case = _make_case(expected_decision="REQUEST_CHANGES")
        mock_result = FileReviewResult(file_path="PR", findings=[])
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review:
            mock_review.return_value = mock_result
            result = harness.run_case(case)
        assert result.decision_correct is False

    def test_run_suite_aggregates(self):
        harness = _make_harness()
        cases = [_make_case(id=f"case-{i}") for i in range(3)]
        finding = FileFinding(title="SQL injection", body="b", file_path="a.py", severity="critical")
        mock_result = FileReviewResult(file_path="PR", findings=[finding])
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review, \
             patch("app.agents.classifier.ClassifierAgent.classify") as mock_classify:
            mock_review.return_value = mock_result
            mock_classify.return_value = [finding]
            summary = harness.run_suite(cases)
        assert summary["total_cases"] == 3
        assert summary["decision_accuracy"] == 1.0
        assert "avg_f1" in summary
        assert len(summary["results"]) == 3

    def test_run_case_empty_diff(self):
        harness = _make_harness()
        case = _make_case(
            diff="",
            expected_findings=[],
            expected_decision="COMMENT",
        )
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review:
            mock_review.return_value = FileReviewResult(file_path="PR", findings=[])
            result = harness.run_case(case)
        assert result.decision_correct is True
        assert result.f1 == 1.0

    def test_eval_result_fields(self):
        r = EvalResult(
            case_id="x",
            decision_correct=True,
            precision=0.8,
            recall=0.9,
            f1=0.847,
            latency_ms=250.0,
            actual_findings=[],
            expected_findings=[],
        )
        assert r.case_id == "x"
        assert r.notes == ""

    def test_eval_case_defaults(self):
        c = EvalCase(
            id="c1", repo="r", pr_number=1, head_sha="s",
            pr_title="t", author_login="u", diff="+x",
        )
        assert c.expected_decision == "COMMENT"
        assert c.tags == []
        assert c.expected_findings == []
