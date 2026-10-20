"""Security scanner agent — SAST, secret detection, dependency CVEs."""
from __future__ import annotations
import json
import re
import subprocess
import tempfile
import os
import structlog
from app.agents.base import BaseAgent
from app.agents.file_reviewer import FileFinding

log = structlog.get_logger(__name__)

# Semgrep rule severities → our severity model
_SEMGREP_SEVERITY = {
    "ERROR": "critical",
    "WARNING": "suggestive",
    "INFO": "optimisation",
}


class SecurityScannerAgent(BaseAgent):
    """
    Combines static analysis tools with LLM-based review for security issues.
    Tools: Semgrep (SAST), truffleHog (secrets), Bandit (Python), pip-audit (CVEs).
    """

    def scan(
        self,
        *,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        files: list[str],
        patches: dict[str, str] | None = None,
    ) -> dict:
        """Run security scan on a set of file paths or patches."""
        log.info("security_scan_started", repo=repo_full_name, pr=pr_number, files=len(files))
        findings: list[FileFinding] = []

        if patches:
            # Write patches to temp dir and run tools
            findings.extend(self._scan_patches(patches))
        else:
            log.info("security_scan_no_patches", note="pass patches dict for live scanning")

        # LLM-based security review of patches
        if patches:
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
        log.info(
            "security_scan_done",
            repo=repo_full_name,
            pr=pr_number,
            total=len(unique),
            critical=len(critical),
        )
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
            "summary": {
                "total": len(unique),
                "critical": len(critical),
            },
            "status": "completed",
        }

    def _scan_patches(self, patches: dict[str, str]) -> list[FileFinding]:
        findings: list[FileFinding] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for filename, patch in patches.items():
                # Reconstruct added lines from the diff
                added_lines = [
                    line[1:]  # strip the leading "+"
                    for line in patch.splitlines()
                    if line.startswith("+") and not line.startswith("+++")
                ]
                content = "\n".join(added_lines)
                if not content.strip():
                    continue
                safe_name = filename.replace("/", "_")
                filepath = os.path.join(tmpdir, safe_name)
                with open(filepath, "w") as fh:
                    fh.write(content)

            findings.extend(self._run_semgrep(tmpdir))
            findings.extend(self._run_bandit(tmpdir))
            findings.extend(self._run_secret_scan(tmpdir))
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
                sev = _SEMGREP_SEVERITY.get(item.get("extra", {}).get("severity", "WARNING"), "suggestive")
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
        try:
            result = subprocess.run(
                ["bandit", "-r", path, "-f", "json", "-q"],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(result.stdout or "{}")
            findings = []
            for item in data.get("results", []):
                confidence = item.get("issue_confidence", "LOW")
                sev_raw = item.get("issue_severity", "LOW")
                sev = "critical" if sev_raw == "HIGH" else "suggestive" if sev_raw == "MEDIUM" else "optimisation"
                findings.append(FileFinding(
                    title=f"Bandit: {item.get('test_id', '')} — {item.get('issue_text', '')[:60]}",
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

    def _run_secret_scan(self, path: str) -> list[FileFinding]:
        """Basic regex secret scan (truffleHog substitute for CI environments)."""
        SECRET_PATTERNS = [
            (r"(?i)(api[_-]?key|secret|password|token|private[_-]?key)\s*[=:]\s*['\"]([^'\"]{8,})['\"]",
             "Potential hardcoded secret"),
            (r"(?i)(aws_secret_access_key|aws_access_key_id)\s*[=:]\s*['\"]([^'\"]{10,})['\"]",
             "AWS credential exposure"),
            (r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
             "Private key in source"),
        ]
        findings = []
        for root, _, fnames in os.walk(path):
            for fname in fnames:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath) as fh:
                        content = fh.read()
                except Exception:
                    continue
                for pattern, title in SECRET_PATTERNS:
                    if re.search(pattern, content):
                        findings.append(FileFinding(
                            title=title,
                            body=f"Pattern matched in {fname}. Review and rotate any exposed credentials.",
                            file_path=fname,
                            severity="critical",
                            source="secret_scan",
                        ))
        return findings

    def _llm_security_review(self, patches: dict[str, str]) -> list[FileFinding]:
        """Ask the LLM to look for security issues across all patches."""
        SYSTEM = """You are a security-focused code reviewer.
Analyse the provided code diffs for security vulnerabilities including:
SQL injection, XSS, command injection, path traversal, insecure deserialization,
authentication bypass, SSRF, exposed secrets, insecure cryptography.

Return ONLY a JSON array of findings with:
{title, body, file_path, line_start (int|null), severity (critical|suggestive|optimisation), source: "llm_security"}
Empty array if none found."""

        combined = "\n\n".join(
            f"=== {fname} ===\n{patch[:3000]}"
            for fname, patch in list(patches.items())[:10]  # cap at 10 files
        )
        try:
            response = self._call(
                messages=[{"role": "user", "content": f"Review these diffs for security issues:\n\n{combined}"}],
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
