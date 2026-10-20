"""End-to-end smoke test: creates a real PR with deliberate bugs, then runs MACRV against it.

Usage (requires real credentials):
    GITHUB_TOKEN=ghp_xxx ANTHROPIC_API_KEY=sk-ant-xxx \
    TEST_REPO=owner/repo \
    python tests/test_real_pr.py

If TEST_REPO is not set, defaults to the repo from GITHUB_REPOSITORY env var,
or prompts the user.

What it does:
  1. Creates a feature branch with a Python file containing deliberate issues
     (SQL injection, hardcoded secret, bare except, unused import).
  2. Opens a pull request targeting main (or TEST_BASE_BRANCH).
  3. Calls GraphOrchestratorAgent.run() directly.
  4. Prints the review result and writes it to /tmp/macrv_real_pr_result.json.
  5. Asserts that critical findings were detected.
  6. Cleans up: closes the PR and deletes the branch.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


def gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gh_get(token: str, path: str, **kwargs) -> dict:
    resp = httpx.get(f"{GITHUB_API}{path}", headers=gh_headers(token), timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def gh_post(token: str, path: str, payload: dict) -> dict:
    resp = httpx.post(f"{GITHUB_API}{path}", headers=gh_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def gh_patch(token: str, path: str, payload: dict) -> dict:
    resp = httpx.patch(f"{GITHUB_API}{path}", headers=gh_headers(token), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def gh_delete(token: str, path: str) -> None:
    resp = httpx.delete(f"{GITHUB_API}{path}", headers=gh_headers(token), timeout=30)
    if resp.status_code not in (204, 422):  # 422 = already gone
        resp.raise_for_status()


def get_default_branch_sha(token: str, repo: str, branch: str) -> str:
    data = gh_get(token, f"/repos/{repo}/git/ref/heads/{branch}")
    return data["object"]["sha"]


def create_branch(token: str, repo: str, branch: str, sha: str) -> None:
    gh_post(token, f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})


def upsert_file(token: str, repo: str, branch: str, path: str, content_b64: str, message: str) -> str:
    """Create or update a file; returns the new commit SHA."""
    import base64

    # Check if file exists (to get its SHA for updates)
    existing_sha = None
    try:
        existing = gh_get(token, f"/repos/{repo}/contents/{path}", params={"ref": branch})
        existing_sha = existing.get("sha")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise

    payload: dict = {
        "message": message,
        "content": content_b64,
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha

    resp = httpx.put(
        f"{GITHUB_API}/repos/{repo}/contents/{path}",
        headers=gh_headers(token),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["commit"]["sha"]


def open_pr(token: str, repo: str, head: str, base: str, title: str, body: str) -> dict:
    return gh_post(token, f"/repos/{repo}/pulls", {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    })


def close_pr(token: str, repo: str, pr_number: int) -> None:
    gh_patch(token, f"/repos/{repo}/pulls/{pr_number}", {"state": "closed"})


def delete_branch(token: str, repo: str, branch: str) -> None:
    gh_delete(token, f"/repos/{repo}/git/refs/heads/{branch}")


# ---------------------------------------------------------------------------
# File contents
# ---------------------------------------------------------------------------

# A "safe" base file that already exists on main so the PR diff is meaningful
BASE_FILE_CONTENT = b"""\"\"\"User account utilities.\"\"\"

import logging

log = logging.getLogger(__name__)


def get_user(db, user_id: int) -> dict | None:
    \"\"\"Fetch a user by ID.\"\"\"
    cursor = db.cursor()
    cursor.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2]}


def list_users(db) -> list[dict]:
    \"\"\"Return all users.\"\"\"
    cursor = db.cursor()
    cursor.execute("SELECT id, name, email FROM users")
    return [{"id": r[0], "name": r[1], "email": r[2]} for r in cursor.fetchall()]
"""

# The "buggy" version pushed on the feature branch — has deliberate issues:
# 1. SQL injection (user_id interpolated directly into query string)
# 2. Hardcoded secret / API key
# 3. bare except (swallows all errors)
# 4. Unused import (os)
BUGGY_FILE_CONTENT = b"""\"\"\"User account utilities — updated for new auth layer.\"\"\"

import logging
import os  # noqa: F401 - unused

log = logging.getLogger(__name__)

# Internal API key for audit service
AUDIT_API_KEY = "sk-audit-c0ffee1234567890abcdef"  # noqa: S105


def get_user(db, user_id: int) -> dict | None:
    \"\"\"Fetch a user by ID.\"\"\"
    cursor = db.cursor()
    # FIXME: migrate to ORM later
    query = f"SELECT id, name, email FROM users WHERE id = {user_id}"
    cursor.execute(query)
    row = cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2]}


def list_users(db) -> list[dict]:
    \"\"\"Return all users.\"\"\"
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id, name, email FROM users")
        return [{"id": r[0], "name": r[1], "email": r[2]} for r in cursor.fetchall()]
    except:  # noqa: E722
        log.error("list_users failed")
        return []


def authenticate(username: str, password: str) -> bool:
    \"\"\"Placeholder auth — always returns True for admin.\"\"\"
    if username == "admin" and password == "admin123":
        return True
    return False
"""


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run_real_pr_test() -> None:  # noqa: C901 - complexity fine for a smoke test
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN (or GH_TOKEN) env var is required.", file=sys.stderr)
        sys.exit(1)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY env var is required.", file=sys.stderr)
        sys.exit(1)

    repo = os.environ.get("TEST_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        repo = input("Enter target repo (owner/repo): ").strip()
    if not repo or "/" not in repo:
        print("ERROR: TEST_REPO must be in owner/repo format.", file=sys.stderr)
        sys.exit(1)

    base_branch = os.environ.get("TEST_BASE_BRANCH", "main")
    run_id = uuid.uuid4().hex[:8]
    feature_branch = f"macrv-smoke-test-{run_id}"
    file_path = "app/utils/user_accounts.py"

    print(f"\n{'='*60}")
    print(f"MACRV real-PR smoke test")
    print(f"  Repo:           {repo}")
    print(f"  Base branch:    {base_branch}")
    print(f"  Feature branch: {feature_branch}")
    print(f"  File:           {file_path}")
    print(f"{'='*60}\n")

    pr_number = None

    try:
        import base64

        # ------------------------------------------------------------------ #
        # 1. Ensure base file exists on main                                  #
        # ------------------------------------------------------------------ #
        print("Step 1: Ensuring base file exists on main...")
        base_sha = get_default_branch_sha(token, repo, base_branch)
        upsert_file(
            token, repo, base_branch, file_path,
            base64.b64encode(BASE_FILE_CONTENT).decode(),
            f"chore: add user_accounts.py base (smoke test {run_id})",
        )
        print(f"  Base file committed. HEAD SHA: {base_sha[:12]}...")

        # ------------------------------------------------------------------ #
        # 2. Create feature branch                                            #
        # ------------------------------------------------------------------ #
        print("Step 2: Creating feature branch...")
        fresh_base_sha = get_default_branch_sha(token, repo, base_branch)
        create_branch(token, repo, feature_branch, fresh_base_sha)
        print(f"  Branch '{feature_branch}' created at {fresh_base_sha[:12]}...")

        # ------------------------------------------------------------------ #
        # 3. Push buggy file onto feature branch                             #
        # ------------------------------------------------------------------ #
        print("Step 3: Pushing buggy code onto feature branch...")
        head_sha = upsert_file(
            token, repo, feature_branch, file_path,
            base64.b64encode(BUGGY_FILE_CONTENT).decode(),
            f"feat: refactor user_accounts with new auth layer (smoke test {run_id})",
        )
        print(f"  Buggy commit: {head_sha[:12]}...")

        # ------------------------------------------------------------------ #
        # 4. Open PR                                                          #
        # ------------------------------------------------------------------ #
        print("Step 4: Opening pull request...")
        pr = open_pr(
            token, repo,
            head=feature_branch,
            base=base_branch,
            title=f"[MACRV Smoke Test {run_id}] Refactor user_accounts with auth layer",
            body=(
                "## What changed\n\n"
                "Refactored `user_accounts.py` to use the new internal audit API key "
                "and added an `authenticate()` helper.\n\n"
                "Fixes login issues reported in staging.\n\n"
                "> ⚠️ This PR was created automatically by `test_real_pr.py` for smoke testing. "
                "It will be closed automatically."
            ),
        )
        pr_number = pr["number"]
        print(f"  PR #{pr_number} opened: {pr['html_url']}")

        # Small pause so GitHub registers the PR and its files
        time.sleep(2)

        # ------------------------------------------------------------------ #
        # 5. Run MACRV review pipeline                                       #
        # ------------------------------------------------------------------ #
        print("\nStep 5: Running MACRV GraphOrchestratorAgent...")
        print("  (This will make LLM calls — may take 30-90s)\n")

        # Import here so missing dependencies surface as a clear error
        from app.agents.graph_orchestrator import GraphOrchestratorAgent  # noqa: PLC0415

        agent = GraphOrchestratorAgent()
        result = agent.run(
            repo_full_name=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=pr["title"],
            author_login="smoke-test",
            author_email=None,
            diff_url=pr.get("diff_url", ""),
        )

        # ------------------------------------------------------------------ #
        # 6. Print results                                                    #
        # ------------------------------------------------------------------ #
        print("\n" + "=" * 60)
        print("REVIEW RESULT")
        print("=" * 60)
        print(f"  Status:     {result.get('status')}")
        print(f"  Decision:   {result.get('decision')}")
        print(f"  Total:      {result.get('total_findings')}")
        print(f"  Critical:   {result.get('critical')}")
        print(f"  Suggestive: {result.get('suggestive')}")
        print(f"  Optimisati: {result.get('optimisation')}")
        print()

        # Show critical finding titles
        report_json = result.get("report_json", {})
        findings = report_json.get("findings", []) if isinstance(report_json, dict) else []
        critical_findings = [f for f in findings if f.get("severity") == "critical"]
        if critical_findings:
            print("Critical findings detected:")
            for f in critical_findings:
                print(f"  🔴 [{f.get('file_path')}] {f.get('title')}")
        else:
            print("No critical findings in report_json (may be embedded differently).")

        # Save full result
        output_path = Path("/tmp/macrv_real_pr_result.json")
        output_path.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nFull result written to: {output_path}")

        # ------------------------------------------------------------------ #
        # 7. Assertions                                                       #
        # ------------------------------------------------------------------ #
        assert result.get("status") == "completed", f"Expected status=completed, got: {result.get('status')}"
        assert result.get("total_findings", 0) > 0, "Expected at least one finding for the buggy code"
        assert result.get("critical", 0) > 0, (
            "Expected at least one CRITICAL finding (SQL injection or hardcoded secret)"
        )
        assert result.get("decision") == "REQUEST_CHANGES", (
            f"Expected decision=REQUEST_CHANGES, got: {result.get('decision')}"
        )

        print("\n✅ All assertions passed — MACRV correctly reviewed the real PR!")

    except Exception:
        print("\n❌ Test failed:", file=sys.stderr)
        traceback.print_exc()
        raise

    finally:
        # ------------------------------------------------------------------ #
        # 8. Cleanup                                                          #
        # ------------------------------------------------------------------ #
        print("\nStep 6: Cleaning up...")
        if pr_number:
            try:
                close_pr(token, repo, pr_number)
                print(f"  PR #{pr_number} closed.")
            except Exception as exc:
                print(f"  Warning: could not close PR: {exc}")
        try:
            delete_branch(token, repo, feature_branch)
            print(f"  Branch '{feature_branch}' deleted.")
        except Exception as exc:
            print(f"  Warning: could not delete branch: {exc}")


# ---------------------------------------------------------------------------
# pytest integration (skipped unless real creds are set)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires GITHUB_TOKEN and ANTHROPIC_API_KEY env vars",
)
@pytest.mark.skipif(
    not os.environ.get("TEST_REPO") and not os.environ.get("GITHUB_REPOSITORY"),
    reason="Requires TEST_REPO or GITHUB_REPOSITORY env var",
)
def test_real_pr_review() -> None:
    """End-to-end: create a PR with deliberate bugs, run MACRV, assert critical findings."""
    run_real_pr_test()


if __name__ == "__main__":
    run_real_pr_test()
