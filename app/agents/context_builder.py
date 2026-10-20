"""Builds rich context for each file being reviewed."""
from __future__ import annotations

import re

import structlog

log = structlog.get_logger(__name__)

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", re.MULTILINE)
_CONTEXT_LINES = 20


def extract_changed_line_ranges(diff: str) -> list[tuple[int, int]]:
    """Return list of (start, end) line numbers from the new file that were changed."""
    ranges = []
    for m in _HUNK_HEADER.finditer(diff):
        new_start = int(m.group(2))
        hunk_text = diff[m.end():]
        next_hunk = _HUNK_HEADER.search(hunk_text)
        hunk_body = hunk_text[:next_hunk.start()] if next_hunk else hunk_text
        added = sum(1 for line in hunk_body.splitlines() if line.startswith("+"))
        ranges.append((new_start, new_start + max(added - 1, 0)))
    return ranges


def build_surrounding_context(file_content: str, diff: str, context_lines: int = _CONTEXT_LINES) -> str:
    """
    Extract surrounding lines from the full file around changed hunks.
    Returns a formatted string showing the relevant context with line numbers.
    """
    if not file_content:
        return ""

    lines = file_content.splitlines()
    total = len(lines)
    ranges = extract_changed_line_ranges(diff)
    if not ranges:
        return ""

    expanded: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        lo = max(0, start - context_lines - 1)
        hi = min(total, end + context_lines)
        if expanded and lo <= expanded[-1][1]:
            expanded[-1] = (expanded[-1][0], max(expanded[-1][1], hi))
        else:
            expanded.append((lo, hi))

    sections = []
    for lo, hi in expanded:
        numbered = [f"{i+1:4d} | {lines[i]}" for i in range(lo, hi)]
        sections.append("\n".join(numbered))

    return "\n...\n".join(sections)


def build_intent_summary(pr_detail: dict) -> str:
    """Format PR intent into a concise context block."""
    parts = []
    if pr_detail.get("title"):
        parts.append(f"PR Title: {pr_detail['title']}")
    if pr_detail.get("head_branch"):
        parts.append(f"Branch: {pr_detail['head_branch']} → {pr_detail.get('base_branch', 'main')}")
    if pr_detail.get("labels"):
        parts.append(f"Labels: {', '.join(pr_detail['labels'])}")
    if pr_detail.get("body"):
        body = pr_detail["body"].strip()
        if len(body) > 800:
            body = body[:800] + "…"
        parts.append(f"Description:\n{body}")
    return "\n".join(parts)


def build_convention_hints(all_file_paths: list[str]) -> str:
    """
    Infer codebase conventions from file paths present in the PR.
    Returns hints the reviewer should know before judging the diff.
    """
    hints = []

    test_files = [f for f in all_file_paths if "/test" in f or f.startswith("test_") or "_test." in f]
    if test_files:
        hints.append("This PR includes test files — verify test coverage matches the changed logic.")

    if any("migration" in f or "alembic" in f for f in all_file_paths):
        hints.append("DB migration detected — check for data-loss risk and reversibility.")

    if any(f.endswith((".env", ".yaml", ".yml", ".toml", ".ini")) for f in all_file_paths):
        hints.append("Config file changed — check for hardcoded secrets or environment-specific values.")

    if any(".github/" in f or "Dockerfile" in f or "docker-compose" in f for f in all_file_paths):
        hints.append("CI/CD or Docker config changed — verify it doesn't weaken security controls.")

    return "\n".join(f"• {h}" for h in hints) if hints else ""
