"""Built-in labelled eval fixtures for MACRV.

Each EvalCase has a synthetic diff and expected findings so teams can
run `POST /api/evals/run` against these cases without needing real PRs.
"""
from __future__ import annotations

from app.evals.harness import EvalCase

BUILT_IN_CASES: list[EvalCase] = [
    # ── 1. SQL injection (critical) ───────────────────────────────────────────
    EvalCase(
        id="sql-injection-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000001",
        pr_title="Add user search endpoint",
        author_login="eval-bot",
        diff="""\
+def search_users(user_input: str):
+    query = f"SELECT * FROM users WHERE name = '{user_input}'"
+    return db.execute(query)
""",
        expected_findings=[{"title": "SQL injection", "severity": "critical"}],
        expected_decision="REQUEST_CHANGES",
        tags=["sql", "injection", "critical"],
    ),

    # ── 2. Hardcoded secret (critical) ────────────────────────────────────────
    EvalCase(
        id="hardcoded-secret-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000002",
        pr_title="Add payment integration",
        author_login="eval-bot",
        diff="""\
+PAYMENT_SECRET = "sk_live_XXXX_EVAL_FIXTURE_NOT_REAL_XXXX"
+EMAIL_API_KEY = "SG.EVAL_FIXTURE_NOT_REAL.signature"
+
+def charge(amount):
+    payment_client.api_key = PAYMENT_SECRET
+    return payment_client.charge(amount=amount)
""",
        expected_findings=[
            {"title": "Stripe Secret Key", "severity": "critical"},
        ],
        expected_decision="REQUEST_CHANGES",
        tags=["secret", "credentials", "critical"],
    ),

    # ── 3. XSS vulnerability (critical) ──────────────────────────────────────
    EvalCase(
        id="xss-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000003",
        pr_title="Add user profile display",
        author_login="eval-bot",
        diff="""\
+function renderProfile(user) {
+  document.getElementById('name').innerHTML = user.displayName;
+  document.getElementById('bio').innerHTML = user.bio;
+}
""",
        expected_findings=[{"title": "XSS", "severity": "critical"}],
        expected_decision="REQUEST_CHANGES",
        tags=["xss", "javascript", "critical"],
    ),

    # ── 4. Clean diff — no issues ─────────────────────────────────────────────
    EvalCase(
        id="clean-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000004",
        pr_title="Refactor utility functions",
        author_login="eval-bot",
        diff="""\
+def calculate_discount(price: float, rate: float) -> float:
+    \"\"\"Return price after discount, clamped to zero.\"\"\"
+    if not 0.0 <= rate <= 1.0:
+        raise ValueError(f"rate must be in [0, 1], got {rate}")
+    return max(0.0, price * (1.0 - rate))
""",
        expected_findings=[],
        expected_decision="COMMENT",
        tags=["clean", "no-issues"],
    ),

    # ── 5. Command injection (critical) ──────────────────────────────────────
    EvalCase(
        id="cmd-injection-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000005",
        pr_title="Add file processor",
        author_login="eval-bot",
        diff="""\
+import subprocess
+
+def process_file(filename: str) -> str:
+    result = subprocess.run(f"cat {filename}", shell=True, capture_output=True, text=True)
+    return result.stdout
""",
        expected_findings=[{"title": "Command injection", "severity": "critical"}],
        expected_decision="REQUEST_CHANGES",
        tags=["command-injection", "subprocess", "critical"],
    ),

    # ── 6. Suggestive-only (no critical block) ────────────────────────────────
    EvalCase(
        id="style-issues-001",
        repo="macrv/eval-fixtures",
        pr_number=0,
        head_sha="eval000006",
        pr_title="Add retry helper",
        author_login="eval-bot",
        diff="""\
+import time
+
+def retry(fn, n=5):
+    for i in range(n):
+        try:
+            return fn()
+        except:
+            time.sleep(1)
+    return fn()
""",
        expected_findings=[],
        expected_decision="COMMENT",
        tags=["style", "suggestive"],
    ),
]


def get_cases_by_tag(tag: str) -> list[EvalCase]:
    return [c for c in BUILT_IN_CASES if tag in c.tags]


def get_case_by_id(case_id: str) -> EvalCase | None:
    return next((c for c in BUILT_IN_CASES if c.id == case_id), None)
