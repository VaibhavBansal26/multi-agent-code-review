"""Tests for context_builder module."""
from app.agents.context_builder import (
    build_convention_hints,
    build_intent_summary,
    build_surrounding_context,
    extract_changed_line_ranges,
)

SAMPLE_DIFF = """@@ -10,6 +10,8 @@ def foo():
-    old_line()
+    new_line()
+    another_new()
 context_line()"""

SAMPLE_FILE = "\n".join(f"line {i}" for i in range(1, 50))


class TestExtractRanges:
    def test_basic_hunk(self):
        ranges = extract_changed_line_ranges(SAMPLE_DIFF)
        assert len(ranges) == 1
        assert ranges[0][0] == 10

    def test_empty_diff(self):
        assert extract_changed_line_ranges("") == []

    def test_multiple_hunks(self):
        diff = "@@ -5,3 +5,3 @@ \n+a\n@@ -20,3 +20,3 @@ \n+b\n"
        ranges = extract_changed_line_ranges(diff)
        assert len(ranges) == 2


class TestSurroundingContext:
    def test_returns_lines_around_change(self):
        result = build_surrounding_context(SAMPLE_FILE, SAMPLE_DIFF, context_lines=3)
        assert result
        assert "|" in result

    def test_empty_content_returns_empty(self):
        assert build_surrounding_context("", SAMPLE_DIFF) == ""

    def test_empty_diff_returns_empty(self):
        assert build_surrounding_context(SAMPLE_FILE, "") == ""


class TestIntentSummary:
    def test_full_pr_detail(self):
        detail = {
            "title": "Fix SQL injection",
            "body": "Sanitize user input before query",
            "labels": ["security", "bug"],
            "base_branch": "main",
            "head_branch": "fix/sql-injection",
        }
        result = build_intent_summary(detail)
        assert "Fix SQL injection" in result
        assert "security" in result
        assert "Sanitize" in result

    def test_empty_detail(self):
        result = build_intent_summary({})
        assert result == ""

    def test_long_body_truncated(self):
        detail = {"title": "t", "body": "x" * 1000}
        result = build_intent_summary(detail)
        assert len(result) < 1000


class TestConventionHints:
    def test_detects_test_files(self):
        hints = build_convention_hints(["tests/test_foo.py", "app/foo.py"])
        assert "test" in hints.lower()

    def test_detects_migration(self):
        hints = build_convention_hints(["alembic/versions/001_add_table.py"])
        assert "migration" in hints.lower() or "DB" in hints

    def test_detects_ci(self):
        hints = build_convention_hints([".github/workflows/ci.yml"])
        assert "CI" in hints or "Docker" in hints

    def test_no_hints_for_plain_files(self):
        hints = build_convention_hints(["app/main.py", "app/models.py"])
        assert hints == ""
