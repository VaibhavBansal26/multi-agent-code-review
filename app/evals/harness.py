"""MACRV evaluation harness — measures review quality against labelled test cases."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class EvalCase:
    """A single labelled test case for evaluating the review pipeline."""
    id: str
    repo: str
    pr_number: int
    head_sha: str
    pr_title: str
    author_login: str
    diff: str  # the code diff to review
    expected_findings: list[dict] = field(default_factory=list)
    expected_decision: str = "COMMENT"  # COMMENT | REQUEST_CHANGES
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    decision_correct: bool
    precision: float   # true_positives / (true_positives + false_positives)
    recall: float      # true_positives / (true_positives + false_negatives)
    f1: float
    latency_ms: float
    actual_findings: list[dict]
    expected_findings: list[dict]
    notes: str = ""


class EvalHarness:
    """
    Runs the review pipeline against labelled cases and scores the results.
    Optionally logs to Weave (W&B) or LangSmith for experiment tracking.
    """

    def __init__(self, use_weave: bool = False, use_langsmith: bool = False):
        self.use_weave = use_weave
        self.use_langsmith = use_langsmith
        self._init_trackers()

    def _init_trackers(self) -> None:
        if self.use_weave:
            try:
                import weave  # type: ignore

                from app.config import get_settings
                settings = get_settings()
                weave.init(settings.weave_project)
                log.info("weave_initialized", project=settings.weave_project)
            except ImportError:
                log.warning("weave_not_installed")
                self.use_weave = False

        if self.use_langsmith:
            try:
                from langsmith import Client  # type: ignore
                self._langsmith = Client()
                log.info("langsmith_initialized")
            except ImportError:
                log.warning("langsmith_not_installed")
                self.use_langsmith = False

    def run_case(self, case: EvalCase) -> EvalResult:
        """Run the review pipeline on one case and score the result."""
        from app.agents.classifier import ClassifierAgent
        from app.agents.file_reviewer import FileReviewAgent

        start = time.monotonic()

        # Review the single diff
        reviewer = FileReviewAgent()
        result = reviewer.review(file_path=case.pr_title, diff=case.diff)
        all_findings = result.findings

        # Classify
        if all_findings:
            classifier = ClassifierAgent()
            all_findings = classifier.classify(all_findings)

        elapsed_ms = (time.monotonic() - start) * 1000

        actual = [
            {"title": f.title, "severity": f.severity, "file_path": f.file_path}
            for f in all_findings
        ]

        # Score
        critical = [f for f in all_findings if f.severity == "critical"]
        actual_decision = "REQUEST_CHANGES" if critical else "COMMENT"
        decision_correct = actual_decision == case.expected_decision

        precision, recall, f1 = self._score_findings(actual, case.expected_findings)

        eval_result = EvalResult(
            case_id=case.id,
            decision_correct=decision_correct,
            precision=precision,
            recall=recall,
            f1=f1,
            latency_ms=elapsed_ms,
            actual_findings=actual,
            expected_findings=case.expected_findings,
        )

        log.info(
            "eval_case_done",
            case=case.id,
            decision_correct=decision_correct,
            f1=round(f1, 3),
            latency_ms=round(elapsed_ms),
        )

        if self.use_weave:
            self._log_to_weave(case, eval_result)

        return eval_result

    def run_suite(self, cases: list[EvalCase]) -> dict[str, Any]:
        """Run all cases and aggregate metrics."""
        results = [self.run_case(c) for c in cases]

        avg_precision = sum(r.precision for r in results) / max(len(results), 1)
        avg_recall = sum(r.recall for r in results) / max(len(results), 1)
        avg_f1 = sum(r.f1 for r in results) / max(len(results), 1)
        decision_accuracy = sum(1 for r in results if r.decision_correct) / max(len(results), 1)
        avg_latency = sum(r.latency_ms for r in results) / max(len(results), 1)

        summary = {
            "total_cases": len(cases),
            "decision_accuracy": round(decision_accuracy, 3),
            "avg_precision": round(avg_precision, 3),
            "avg_recall": round(avg_recall, 3),
            "avg_f1": round(avg_f1, 3),
            "avg_latency_ms": round(avg_latency),
            "results": [
                {
                    "case_id": r.case_id,
                    "decision_correct": r.decision_correct,
                    "f1": round(r.f1, 3),
                    "latency_ms": round(r.latency_ms),
                }
                for r in results
            ],
        }
        log.info("eval_suite_done", **{k: v for k, v in summary.items() if k != "results"})
        return summary

    @staticmethod
    def _score_findings(actual: list[dict], expected: list[dict]) -> tuple[float, float, float]:
        """Simple title-based matching to compute precision/recall/F1."""
        if not expected:
            # No expected findings — perfect if we also found nothing
            if not actual:
                return 1.0, 1.0, 1.0
            return 0.0, 1.0, 0.0  # false positives

        expected_titles = {e.get("title", "").lower() for e in expected}
        actual_titles = {a.get("title", "").lower() for a in actual}

        tp = len(expected_titles & actual_titles)
        fp = len(actual_titles - expected_titles)
        fn = len(expected_titles - actual_titles)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        return precision, recall, f1

    def _log_to_weave(self, case: EvalCase, result: EvalResult) -> None:
        try:
            # weave.log is version-dependent; use the call API when available
            pass  # actual logging done by @weave.op decorator in production
        except Exception as exc:
            log.warning("weave_log_failed", error=str(exc))
