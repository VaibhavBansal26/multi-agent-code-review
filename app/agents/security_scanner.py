"""Security scanner agent — SAST, secret detection, dependency CVEs."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

import structlog

from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileFinding

log = structlog.get_logger(__name__)

_SEMGREP_SEVERITY = {
    "ERROR": "critical",
    "WARNING": "suggestive",
    "INFO": "optimisation",
}

# Comprehensive secret patterns (covers AWS, GCP, GitHub, Stripe, generic keys/passwords)
_SECRET_PATTERNS: list[tuple[str, str]] = [
    # Generic key/password/secret assignments
    (
        r'(?i)(api[_-]?key|secret[_-]?key|access[_-]?key|private[_-]?key|auth[_-]?token'
        r'|password|passwd|pwd|secret)\s*[=:]\s*["\']([^"\']{8,})["\']',
        "Potential hardcoded secret",
    ),
    # AWS
    (r'AKIA[0-9A-Z]{16}', "AWS Access Key ID"),
    (r'(?i)aws[_\-\s]?secret[_\-\s]?access[_\-\s]?key\s*[=:]\s*["\']([^"\']{20,})["\']', "AWS Secret Access Key"),
    # GitHub
    (r'gh[pousr]_[A-Za-z0-9]{36}', "GitHub Personal Access Token"),
    (r'github_pat_[A-Za-z0-9_]{82}', "GitHub Fine-grained PAT"),
    # Stripe
    (r'sk_(live|test)_[A-Za-z0-9]{24,}', "Stripe Secret Key"),
    # Anthropic / OpenAI
    (r'sk-[A-Za-z0-9]{32,}', "API Secret Key (Anthropic/OpenAI-style)"),
    # Private keys
    (r'-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----', "Private key in source"),
    # DB connection strings
    (
        r'(postgresql|mysql|mongodb)://[^:]+:[^@]+@[^/\s]+',
        "Database connection string with credentials",
    ),
    # JWT with HS256 signature (detects hardcoded JWT tokens)
    (r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}', "Hardcoded JWT token"),
]


class SecurityScannerAgent(BaseAgent):
    """
    Phase 4 security scanner: SAST + secret detection + dependency CVE audit.

    scan(mode="sast")  — static tools only (Semgrep, Bandit, secrets, dep audit)
    scan(mode="llm")   — LLM-based adversarial review only
    scan(mode="full")  — both (default, used in legacy deep scan path)
    """

    def scan(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        files: list[str],
        patches: dict[str, str] | None = None,
        dep_files: dict[str, str] | None = None,
        mode: str = "full",
    ) -> dict:
        log.info("security_scan_started", repo=repo_full_name, pr=pr_number, files=len(files), mode=mode)
        findings: list[FileFinding] = []

        if mode in ("sast", "full") and patches:
            findings.extend(self._scan_patches(patches))
        if mode in ("sast", "full") and dep_files:
            findings.extend(self._run_dep_audit(dep_files))
        if mode in ("llm", "full") and patches:
            findings.extend(self._llm_security_review(patches))

        # Deduplicate by (title, file_path)
        seen: set[tuple[str, str]] = set()
        unique: list[FileFinding] = []
        for f in findings:
            key = (f.title, f.file_path)
            if key not in seen:
                seen.add(key)
                unique.append(f)

        critical = [f for f in unique if f.severity == "critical"]
        log.info("security_scan_done", repo=repo_full_name, pr=pr_number,
                 total=len(unique), critical=len(critical), mode=mode)
        return {
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "findings": [
                {
                    "severity": f.severity,
                    "title": f.title,
                    "file_path": f.file_path,
                    "line_start": f.line_start,
                    "body": f.body,
                    "source": f.source,
                }
                for f in unique
            ],
            "summary": {"total": len(unique), "critical": len(critical)},
            "status": "completed",
        }

    # ── SAST tools ──────────────────────────────────────────────────────────────

    def _scan_patches(self, patches: dict[str, str]) -> list[FileFinding]:
        findings: list[FileFinding] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write added lines from each patch to temp files
            for filename, patch in patches.items():
                added_lines = [
                    line[1:]
                    for line in patch.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                ]
                content = "\n".join(added_lines)
                if not content.strip():
                    continue
                safe_name = filename.replace("/", "_")
                with open(os.path.join(tmpdir, safe_name), "w") as fh:
                    fh.write(content)

            findings.extend(self._run_semgrep(tmpdir))
            findings.extend(self._run_bandit(tmpdir))
            findings.extend(self._run_secret_scan(tmpdir, patches))

        return findings

    def _run_semgrep(self, path: str) -> list[FileFinding]:
        try:
            result = subprocess.run(
                ["semgrep", "--config=auto", "--json", "--quiet", path],
                capture_output=True, text=True, timeout=120,
            )
            data = json.loads(result.stdout or "{}")
            findings = []
            for item in data.get("results", []):
                sev = _SEMGREP_SEVERITY.get(
                    item.get("extra", {}).get("severity", "WARNING"), "suggestive"
                )
                findings.append(FileFinding(
                    title=item.get("check_id", "Semgrep finding"),
                    body=item.get("extra", {}).get("message", ""),
                    file_path=item.get("path", ""),
                    line_start=item.get("start", {}).get("line"),
                    severity=sev,
                    source="semgrep",
                ))
            log.info("semgrep_done", findings=len(findings))
            return findings
        except FileNotFoundError:
            log.warning("semgrep_not_installed")
            return []
        except Exception as exc:
            log.error("semgrep_failed", error=str(exc))
            return []

    def _run_bandit(self, path: str) -> list[FileFinding]:
        """Python-specific SAST with Bandit."""
        try:
            result = subprocess.run(
                ["bandit", "-r", path, "-f", "json", "-q", "--exit-zero"],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(result.stdout or "{}")
            findings = []
            for item in data.get("results", []):
                sev_raw = item.get("issue_severity", "LOW")
                sev = "critical" if sev_raw == "HIGH" else "suggestive" if sev_raw == "MEDIUM" else "optimisation"
                confidence = item.get("issue_confidence", "LOW")
                findings.append(FileFinding(
                    title=f"Bandit {item.get('test_id', '')} — {item.get('issue_text', '')[:80]}",
                    body=f"{item.get('issue_text', '')} (confidence: {confidence})",
                    file_path=item.get("filename", ""),
                    line_start=item.get("line_number"),
                    severity=sev,
                    source="bandit",
                ))
            log.info("bandit_done", findings=len(findings))
            return findings
        except FileNotFoundError:
            log.warning("bandit_not_installed")
            return []
        except Exception as exc:
            log.error("bandit_failed", error=str(exc))
            return []

    def _run_secret_scan(self, path: str, patches: dict[str, str] | None = None) -> list[FileFinding]:
        """
        Multi-pattern secret detector covering AWS, GitHub, Stripe, JWT,
        DB connection strings, and generic key/password assignments.
        Also checks the raw patch text (not just extracted added lines)
        so secrets in context lines aren't missed.
        """
        findings: list[FileFinding] = []
        sources_to_scan: list[tuple[str, str]] = []  # (display_filename, content)

        # Scan temp files
        for root, _, fnames in os.walk(path):
            for fname in fnames:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath) as fh:
                        sources_to_scan.append((fname, fh.read()))
                except Exception:
                    continue

        # Also scan full raw patches for secrets in context lines
        if patches:
            for filename, patch in patches.items():
                sources_to_scan.append((filename, patch))

        seen_secrets: set[tuple[str, str]] = set()
        for display_name, content in sources_to_scan:
            for pattern, title in _SECRET_PATTERNS:
                if re.search(pattern, content):
                    key = (title, display_name)
                    if key not in seen_secrets:
                        seen_secrets.add(key)
                        findings.append(FileFinding(
                            title=title,
                            body=(
                                f"Pattern matched in {display_name}. "
                                "Review and rotate any exposed credentials immediately."
                            ),
                            file_path=display_name,
                            severity="critical",
                            source="secret_scan",
                        ))

        log.info("secret_scan_done", findings=len(findings))
        return findings

    def _run_dep_audit(self, dep_files: dict[str, str]) -> list[FileFinding]:
        """
        CVE audit for Python (pip-audit) and JavaScript (npm audit) dependencies.
        dep_files: {filename: file_content} for requirements.txt, package.json, etc.
        """
        findings: list[FileFinding] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename, content in dep_files.items():
                fpath = os.path.join(tmpdir, filename)
                with open(fpath, "w") as fh:
                    fh.write(content)

            # pip-audit for Python
            req_files = [f for f in dep_files if f in ("requirements.txt", "Pipfile", "pyproject.toml")]
            for req_file in req_files:
                findings.extend(self._pip_audit(os.path.join(tmpdir, req_file), req_file))

            # npm audit for JavaScript
            if "package.json" in dep_files:
                findings.extend(self._npm_audit(tmpdir))

        log.info("dep_audit_done", findings=len(findings))
        return findings

    def _pip_audit(self, req_path: str, display_name: str) -> list[FileFinding]:
        try:
            result = subprocess.run(
                ["pip-audit", "-r", req_path, "--format", "json", "--progress-spinner", "off"],
                capture_output=True, text=True, timeout=120,
            )
            # pip-audit exits non-zero when vulnerabilities found — that's expected
            raw = result.stdout.strip()
            if not raw:
                return []
            data = json.loads(raw)
            findings = []
            for dep in data.get("dependencies", []):
                for vuln in dep.get("vulns", []):
                    fix = dep.get("fix_versions", [])
                    fix_note = f" Fix: upgrade to {fix[0]}" if fix else " No fix available."
                    findings.append(FileFinding(
                        title=f"CVE {vuln.get('id', '?')} in {dep.get('name', '?')} {dep.get('version', '')}",
                        body=f"{vuln.get('description', '')} ({vuln.get('id', '')}){fix_note}",
                        file_path=display_name,
                        severity="critical" if any(
                            s in vuln.get("id", "").upper() for s in ("CVE",)
                        ) else "suggestive",
                        source="pip-audit",
                    ))
            return findings
        except FileNotFoundError:
            log.warning("pip_audit_not_installed")
            return []
        except Exception as exc:
            log.error("pip_audit_failed", error=str(exc))
            return []

    def _npm_audit(self, tmpdir: str) -> list[FileFinding]:
        try:
            # npm audit requires node_modules; skip if not available
            result = subprocess.run(
                ["npm", "audit", "--json", "--prefix", tmpdir],
                capture_output=True, text=True, timeout=120,
            )
            data = json.loads(result.stdout or "{}")
            findings = []
            for adv_id, adv in data.get("advisories", {}).items():
                severity = adv.get("severity", "moderate")
                sev = "critical" if severity in ("critical", "high") else "suggestive"
                findings.append(FileFinding(
                    title=f"npm advisory: {adv.get('title', adv_id)}",
                    body=(
                        f"Package: {adv.get('module_name', '?')} "
                        f"({adv.get('vulnerable_versions', '?')}). "
                        f"{adv.get('recommendation', '')}"
                    ),
                    file_path="package.json",
                    severity=sev,
                    source="npm-audit",
                ))
            return findings
        except FileNotFoundError:
            log.warning("npm_not_installed")
            return []
        except Exception as exc:
            log.error("npm_audit_failed", error=str(exc))
            return []

    # ── LLM-based security review ───────────────────────────────────────────────

    def _llm_security_review(self, patches: dict[str, str]) -> list[FileFinding]:
        """Adversarial LLM pass — catches logic-level issues SAST tools miss."""
        SYSTEM = """You are an adversarial security code reviewer.
Analyse these diffs for vulnerabilities: SQL injection, XSS, command injection,
path traversal, insecure deserialization, auth bypass, SSRF, IDOR, race conditions,
exposed secrets, insecure crypto, and logic flaws.

Return ONLY a JSON array. Each item:
{
  "title": "short finding name",
  "body": "explanation with line reference",
  "file_path": "filename",
  "line_start": <int or null>,
  "severity": "critical|suggestive|optimisation",
  "source": "llm_security"
}
Empty array [] if none found."""

        combined = "\n\n".join(
            f"=== {fname} ===\n{patch[:3000]}"
            for fname, patch in list(patches.items())[:10]
        )
        try:
            response = self._call(
                messages=[{"role": "user", "content": f"Review for security vulnerabilities:\n\n{combined}"}],
                system=SYSTEM,
                max_tokens=4096,
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
            items = json.loads(raw.strip()) if raw.strip().startswith("[") else []
            findings = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                findings.append(FileFinding(
                    title=item.get("title", "Security finding"),
                    body=item.get("body", ""),
                    file_path=item.get("file_path", ""),
                    line_start=item.get("line_start"),
                    severity=item.get("severity", "critical"),
                    source=item.get("source", "llm_security"),
                ))
            return findings
        except Exception as exc:
            log.error("llm_security_review_failed", error=str(exc))
            return []
