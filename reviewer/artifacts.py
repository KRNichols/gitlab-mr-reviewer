"""Parse common CI artifacts when a job uploaded them."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from reviewer.config import severity_for
from reviewer.findings import Finding
from reviewer.http import gitlab_download, is_2xx

JUNIT_NAMES = ("junit.xml", "report.xml", "test-results.xml")
COVERAGE_NAMES = ("coverage.xml", "cobertura.xml")
PIP_AUDIT_NAMES = ("pip-audit.json", "pip-audit-report.json")
NPM_AUDIT_NAMES = ("npm-audit.json", "audit.json")


def _jobs_with_artifacts(jobs):
    """
    What: Job dicts that advertise a downloadable artifacts archive.
    Why: Most jobs have no zip; curling every job wastes the token budget.
    Who: consume_artifacts before it calls gitlab_download.
    Where: GitLab job objects with artifacts_file or a non-empty artifacts list.
    How: Keep rows that have a filename or a non-empty artifacts array.
    """
    found = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        artifact = job.get("artifacts_file") or {}
        rows = job.get("artifacts") or []
        has_file = isinstance(artifact, dict) and artifact.get("filename")
        if has_file or (isinstance(rows, list) and rows):
            found.append(job)
    return found


def parse_junit(data):
    """
    What: Count JUnit failures and errors from XML bytes or text.
    Why: A red test artifact must hold even if the job name was remapped.
    Who: consume_artifacts when a zip member looks like junit.xml.
    Where: testsuite and testsuites failure/error attributes.
    How: Parse XML, sum the attributes, ignore malformed documents.
    """
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return 0
    total = 0
    nodes = [root]
    nodes.extend(root.findall(".//testsuite"))
    for node in nodes:
        total += int(node.attrib.get("failures") or 0)
        total += int(node.attrib.get("errors") or 0)
    return total


def parse_coverage(data):
    """
    What: Read a Cobertura-style line-rate from coverage XML.
    Why: Operators can set coverage_min without parsing HTML reports.
    Who: consume_artifacts when a zip member looks like coverage.xml.
    Where: The coverage element's line-rate attribute (0 to 1).
    How: Parse XML and return the float, or None when the file is not coverage.
    """
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    rate = root.attrib.get("line-rate")
    if rate is None:
        return None
    try:
        return float(rate)
    except ValueError:
        return None


def parse_pip_audit(data):
    """
    What: Count pip-audit vulnerability rows from a JSON document.
    Why: A red pip-audit artifact is recorded; it still does not block by default.
    Who: consume_artifacts when a zip member looks like pip-audit JSON.
    Where: dependencies[].vulns or a top-level list of vuln objects.
    How: Walk either shape and count entries that include a vulns list or name.
    """
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return 0
    count = 0
    if isinstance(payload, dict):
        deps = payload.get("dependencies") or payload.get("vulns") or []
        if isinstance(deps, list):
            for item in deps:
                if isinstance(item, dict):
                    vulns = item.get("vulns") or item.get("vulnerabilities") or []
                    count += len(vulns) if isinstance(vulns, list) else 1
        return count
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                vulns = item.get("vulns") or []
                count += len(vulns) if vulns else 1
    return count


def parse_npm_audit(data):
    """
    What: Count high plus critical npm audit findings from JSON.
    Why: node-audit red must hold; the artifact is a second source for that gate.
    Who: consume_artifacts when a zip member looks like npm-audit JSON.
    Where: metadata.vulnerabilities.high and .critical.
    How: Sum those two integers; return 0 when the document is a different shape.
    """
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    meta = (payload.get("metadata") or {}).get("vulnerabilities") or {}
    if not isinstance(meta, dict):
        return 0
    try:
        return int(meta.get("high") or 0) + int(meta.get("critical") or 0)
    except (TypeError, ValueError):
        return 0


def findings_from_zip(raw, cfg, job_name=""):
    """
    What: Walk a job artifact zip and emit findings for known report names.
    Why: JUnit, coverage, pip-audit, and npm audit should feed the same verdict.
    Who: consume_artifacts after a 2xx download.
    Where: Member names containing the well-known report suffixes.
    How: Open the zip in memory, parse matching members, never print file bodies.
    """
    findings = []
    try:
        with zipfile.ZipFile(raw) as archive:
            names = archive.namelist()
            for name in names:
                lower = name.lower()
                payload = None
                if any(lower.endswith(item) or item in lower for item in JUNIT_NAMES):
                    payload = archive.read(name)
                    failed = parse_junit(payload)
                    if failed:
                        findings.append(
                            Finding(
                                "junit",
                                f"{job_name or 'job'} junit reported {failed} failure(s)",
                                severity_for(cfg, "junit"),
                            )
                        )
                elif any(lower.endswith(item) or item in lower for item in COVERAGE_NAMES):
                    payload = archive.read(name)
                    rate = parse_coverage(payload)
                    minimum = float(cfg.get("coverage_min") or 0)
                    if rate is not None and minimum and rate < minimum:
                        findings.append(
                            Finding(
                                "coverage",
                                f"coverage line-rate {rate:.2f} is under {minimum:.2f}",
                                severity_for(cfg, "coverage", "warn"),
                            )
                        )
                elif any(lower.endswith(item) or item in lower for item in PIP_AUDIT_NAMES):
                    payload = archive.read(name)
                    count = parse_pip_audit(payload)
                    if count:
                        sev = "blocker" if cfg.get("pip_audit_blocks") else severity_for(cfg, "pip-audit", "warn")
                        findings.append(
                            Finding(
                                "pip-audit",
                                f"{count} pip-audit finding(s) (does not block unless overridden; not a secrets-OK)",
                                sev,
                            )
                        )
                elif any(lower.endswith(item) or item in lower for item in NPM_AUDIT_NAMES):
                    payload = archive.read(name)
                    count = parse_npm_audit(payload)
                    if count:
                        findings.append(
                            Finding(
                                "npm-audit",
                                f"{count} high/critical npm audit finding(s)",
                                severity_for(cfg, "npm-audit"),
                            )
                        )
    except zipfile.BadZipFile:
        return findings
    return findings


def consume_artifacts(jobs, token, project_base_url, cfg, download_fn=None, api_root=None):
    """
    What: Download job artifacts when present and turn reports into findings.
    Why: A renamed test job can still hold via junit.xml even if names drifted.
    Who: run_review after evaluate_jobs.
    Where: GET /jobs/:id/artifacts on the allowlisted API root.
    How: Skip jobs without archives; ignore non-2xx downloads; never print bytes.
    """
    findings = []
    fetch = download_fn or gitlab_download
    for job in _jobs_with_artifacts(jobs):
        job_id = job.get("id")
        if job_id is None:
            continue
        url = f"{project_base_url.rstrip('/')}/jobs/{job_id}/artifacts"
        handle = tempfile.NamedTemporaryFile(prefix="glrev-art-", suffix=".zip", delete=False)
        handle.close()
        dest = Path(handle.name)
        try:
            status = fetch(url, token, str(dest), api_root)
            if not is_2xx(status) or not dest.is_file() or dest.stat().st_size == 0:
                continue
            findings.extend(findings_from_zip(dest, cfg, str(job.get("name") or "")))
        finally:
            try:
                dest.unlink()
            except OSError:
                pass
    return findings
