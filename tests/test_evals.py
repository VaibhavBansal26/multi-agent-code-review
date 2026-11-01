"""Tests for Phase 6 evaluation harness."""
from unittest.mock import patch

from app.agents.file_reviewer import FileFinding, FileReviewResult
from app.evals.harness import EvalCase, EvalHarness, EvalResult


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
        finding = FileFinding(
            title="SQL injection", body="b", file_path="a.py", severity="critical"
        )
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

    def test_weave_log_noop_when_not_configured(self):
        """_log_to_weave should silently do nothing when Weave is not initialised."""
        harness = _make_harness()
        harness._weave = None
        result = EvalResult(
            case_id="x", decision_correct=True,
            precision=1.0, recall=1.0, f1=1.0,
            latency_ms=100.0, actual_findings=[], expected_findings=[],
        )
        harness._log_to_weave(_make_case(), result)  # must not raise


# ── Fixtures ─────────────────────────────────────────────────────────────────

class TestFixtures:
    def test_built_in_cases_present(self):
        from app.evals.fixtures import BUILT_IN_CASES
        assert len(BUILT_IN_CASES) >= 4

    def test_all_cases_have_required_fields(self):
        from app.evals.fixtures import BUILT_IN_CASES
        for c in BUILT_IN_CASES:
            assert c.id
            assert c.diff
            assert c.expected_decision in ("COMMENT", "REQUEST_CHANGES")

    def test_get_case_by_id(self):
        from app.evals.fixtures import get_case_by_id
        c = get_case_by_id("sql-injection-001")
        assert c is not None
        assert c.expected_decision == "REQUEST_CHANGES"

    def test_get_case_by_id_missing(self):
        from app.evals.fixtures import get_case_by_id
        assert get_case_by_id("does-not-exist") is None

    def test_get_cases_by_tag(self):
        from app.evals.fixtures import get_cases_by_tag
        critical = get_cases_by_tag("critical")
        assert len(critical) >= 3
        for c in critical:
            assert "critical" in c.tags

    def test_clean_case_expects_no_findings(self):
        from app.evals.fixtures import get_case_by_id
        c = get_case_by_id("clean-001")
        assert c is not None
        assert c.expected_findings == []
        assert c.expected_decision == "COMMENT"


# ── Eval API endpoint ─────────────────────────────────────────────────────────

class TestEvalApi:
    def test_run_evals_all_cases(self, client):
        from app.agents.file_reviewer import FileFinding, FileReviewResult

        # All cases return no findings → COMMENT decision
        empty_result = FileReviewResult(file_path="PR", findings=[])
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review:
            mock_review.return_value = empty_result
            resp = client.post("/dashboard/api/evals/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cases" in data
        assert "avg_f1" in data
        assert data["total_cases"] >= 4

    def test_run_evals_specific_case_ids(self, client):
        from app.agents.file_reviewer import FileFinding, FileReviewResult
        empty_result = FileReviewResult(file_path="PR", findings=[])
        with patch("app.agents.file_reviewer.FileReviewAgent.review") as mock_review:
            mock_review.return_value = empty_result
            resp = client.post(
                "/dashboard/api/evals/run",
                json={"case_ids": ["sql-injection-001", "clean-001"]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cases"] == 2

    def test_run_evals_unknown_case_ids(self, client):
        resp = client.post(
            "/dashboard/api/evals/run",
            json={"case_ids": ["nonexistent-case"]},
        )
        assert resp.status_code == 400
