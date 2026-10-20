"""End-to-end smoke test: creates a real PR with deliberate bugs, then runs MACRV against it.

Usage (requires real credentials):
    ANTHROPIC_API_KEY=sk-ant-xxx \
    TEST_REPO=owner/repo \
    python tests/test_real_pr.py

What it does:
  1. Creates a feature branch from the current HEAD with a Python file
     containing deliberate issues (SQL injection, hardcoded secret,
     bare except, hardcoded admin credentials).
  2. Opens a pull request via `gh pr create`.
  3. Calls GraphOrchestratorAgent.run() directly.
  4. Prints the review result and writes it to /tmp/macrv_real_pr_result.json.
  5. Asserts that critical findings were detected.
  6. Cleans up: closes the PR and deletes the branch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Git / gh helpers (write ops go through git/gh, not the REST API)
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


def run(cmd: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, printing it for visibility."""
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0:
        if result.stderr.strip():
            print(f"    STDERR: {result.stderr.strip()}", file=sys.stderr)
        if check:
            raise RuntimeError(f"Command failed (exit {result.returncode}): {cmd}")
    return result


# ---------------------------------------------------------------------------
# Buggy file content
# ---------------------------------------------------------------------------

BUGGY_FILE = '''\
"""User account utilities - updated for new auth layer."""

import logging
import os  # unused import

log = logging.getLogger(__name__)

# Internal API key for audit service
AUDIT_API_KEY = "sk-audit-c0ffee1234567890abcdef"


def get_user(db, user_id: int) -> dict | None:
    """Fetch a user by ID."""
    cursor = db.cursor()
    # SQL injection: user_id interpolated directly
    query = f"SELECT id, name, email FROM users WHERE id = {user_id}"
    cursor.execute(query)
    row = cursor.fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2]}


def list_users(db) -> list[dict]:
    """Return all users."""
    try:
        cursor = db.cursor()
        cursor.execute("SELECT id, name, email FROM users")
        return [{"id": r[0], "name": r[1], "email": r[2]} for r in cursor.fetchall()]
    except:
        log.error("list_users failed")
        return []


def authenticate(username: str, password: str) -> bool:
    """Placeholder auth."""
    if username == "admin" and password == "admin123":
        return True
    return False
'''


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run_real_pr_test() -> None:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY env var is required.", file=sys.stderr)
        sys.exit(1)

    # Use GH_TOKEN (set by Claude Code Remote) or GITHUB_TOKEN
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN or GH_TOKEN env var is required.", file=sys.stderr)
        sys.exit(1)

    repo = os.environ.get("TEST_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        repo = input("Enter target repo (owner/repo): ").strip()
    if not repo or "/" not in repo:
        print("ERROR: TEST_REPO must be in owner/repo format.", file=sys.stderr)
        sys.exit(1)

    run_id = uuid.uuid4().hex[:8]
    feature_branch = f"macrv-smoke-test-{run_id}"
    file_path = "app/utils/user_accounts.py"
    repo_root = Path(__file__).parent.parent

    # Get the default branch name
    try:
        repo_meta = gh_get(token, f"/repos/{repo}")
        base_branch = repo_meta.get("default_branch", "main")
    except Exception:
        base_branch = "main"

    print(f"\n{'='*60}")
    print("MACRV real-PR smoke test")
    print(f"  Repo:           {repo}")
    print(f"  Base branch:    {base_branch}")
    print(f"  Feature branch: {feature_branch}")
    print(f"  File:           {file_path}")
    print(f"{'='*60}\n")

    pr_number = None
    original_branch = None

    try:
        # ------------------------------------------------------------------ #
        # 1. Remember current branch, create feature branch                  #
        # ------------------------------------------------------------------ #
        print("Step 1: Creating feature branch via git...")
        result = run("git rev-parse --abbrev-ref HEAD", cwd=str(repo_root))
        original_branch = result.stdout.strip()

        run(f"git checkout -b {feature_branch}", cwd=str(repo_root))
        print(f"  On branch '{feature_branch}'")

        # ------------------------------------------------------------------ #
        # 2. Write buggy file and commit                                      #
        # ------------------------------------------------------------------ #
        print("\nStep 2: Writing buggy file and committing...")
        target = repo_root / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BUGGY_FILE)

        run(f"git add {file_path}", cwd=str(repo_root))
        run(
            f'git commit -m "feat: refactor user_accounts with new auth layer (smoke test {run_id})"',
            cwd=str(repo_root),
        )
        # Capture the head SHA
        head_sha_result = run("git rev-parse HEAD", cwd=str(repo_root))
        head_sha = head_sha_result.stdout.strip()
        print(f"  Committed. HEAD: {head_sha[:12]}...")

        # ------------------------------------------------------------------ #
        # 3. Push feature branch                                              #
        # ------------------------------------------------------------------ #
        print("\nStep 3: Pushing feature branch...")
        run(f"git push -u origin {feature_branch}", cwd=str(repo_root))

        # ------------------------------------------------------------------ #
        # 4. Open PR via gh CLI                                               #
        # ------------------------------------------------------------------ #
        print("\nStep 4: Opening pull request via gh...")
        pr_body = (
            "## What changed\n\n"
            "Refactored `user_accounts.py` to use the new internal audit API key "
            "and added an `authenticate()` helper.\n\n"
            "Fixes login issues reported in staging.\n\n"
            "> This PR was created automatically by `test_real_pr.py` for smoke testing."
        )
        pr_title = f"[MACRV Smoke Test {run_id}] Refactor user_accounts with auth layer"

        # Write body to temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(pr_body)
            body_file = f.name

        # Use REST API via gh api (GraphQL is blocked in this env)
        import json as _json  # noqa: PLC0415
        pr_payload = {
            "title": pr_title,
            "body": pr_body,
            "head": feature_branch,
            "base": base_branch,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as pf:
            _json.dump(pr_payload, pf)
            pr_payload_file = pf.name
        Path(body_file).unlink(missing_ok=True)
        pr_result = run(
            f"gh api repos/{repo}/pulls --method POST --input {pr_payload_file}",
            cwd=str(repo_root),
        )
        Path(pr_payload_file).unlink(missing_ok=True)
        pr_data = _json.loads(pr_result.stdout)
        pr_url = pr_data.get("html_url", "")
        pr_number = pr_data["number"]
        print(f"  PR #{pr_number} opened: {pr_url}")

        # ------------------------------------------------------------------ #
        # 5. Run MACRV review pipeline                                       #
        # ------------------------------------------------------------------ #
        print("\nStep 5: Running MACRV GraphOrchestratorAgent...")
        print("  (LLM calls in progress — may take 30-90s)\n")

        # Set env vars so the agent can call Anthropic
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key

        from app.agents.graph_orchestrator import GraphOrchestratorAgent  # noqa: PLC0415

        agent = GraphOrchestratorAgent()
        result = agent.run(
            repo_full_name=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            pr_title=pr_title,
            author_login="smoke-test",
            author_email=None,
            diff_url=f"https://github.com/{repo}/pull/{pr_number}.diff",
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

        report_json = result.get("report_json", {})
        findings = report_json.get("findings", []) if isinstance(report_json, dict) else []
        critical_findings = [f for f in findings if f.get("severity") == "critical"]
        if critical_findings:
            print("Critical findings detected:")
            for f in critical_findings:
                print(f"  [CRITICAL] [{f.get('file_path')}] {f.get('title')}")
        else:
            print(f"  (report_json findings: {len(findings)} total)")

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

        print("\n All assertions passed - MACRV correctly reviewed the real PR!")

    except Exception:
        print("\n Test failed:", file=sys.stderr)
        traceback.print_exc()
        raise

    finally:
        # ------------------------------------------------------------------ #
        # 8. Cleanup                                                          #
        # ------------------------------------------------------------------ #
        print("\nStep 6: Cleaning up...")

        # Switch back to original branch first
        if original_branch:
            try:
                run(f"git checkout {original_branch}", cwd=str(repo_root))
                # Remove buggy file if it leaked to disk
                buggy = repo_root / file_path
                if buggy.exists():
                    buggy.unlink()
                    run(f"git checkout -- {file_path}", cwd=str(repo_root), check=False)
            except Exception as exc:
                print(f"  Warning: could not switch back to {original_branch}: {exc}")

        if pr_number:
            try:
                run(
                    f'gh api repos/{repo}/pulls/{pr_number} --method PATCH '
                    f'--field state=closed',
                    cwd=str(repo_root),
                )
                print(f"  PR #{pr_number} closed.")
            except Exception as exc:
                print(f"  Warning: could not close PR: {exc}")

        # Delete remote feature branch
        try:
            run(f"git push origin --delete {feature_branch}", cwd=str(repo_root), check=False)
            print(f"  Branch '{feature_branch}' deleted from remote.")
        except Exception as exc:
            print(f"  Warning: could not delete remote branch: {exc}")


# ---------------------------------------------------------------------------
# pytest integration (skipped unless real creds are set)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN"),
    reason="Requires GITHUB_TOKEN or GH_TOKEN env var",
)
@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY env var",
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
