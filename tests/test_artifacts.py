"""Artifact parsers: junit blocks, pip-audit does not."""

from __future__ import annotations

import io
import json
import unittest
import zipfile

from reviewer.artifacts import findings_from_zip, parse_junit, parse_npm_audit, parse_pip_audit
from reviewer.config import load_config


def _zip_with(name, payload):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
        archive.writestr(name, data)
    buf.seek(0)
    return buf


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config({"REVIEW_CONFIG": "/nonexistent/reviewer.json"}, root="/tmp")

    def test_junit_failures_are_blockers(self):
        xml = '<testsuite failures="2" errors="1"></testsuite>'
        self.assertEqual(parse_junit(xml), 3)
        findings = findings_from_zip(_zip_with("junit.xml", xml), self.cfg, "backend")
        self.assertTrue(any(item.rule == "junit" and item.severity == "blocker" for item in findings))

    def test_pip_audit_is_warn_by_default(self):
        payload = json.dumps({"dependencies": [{"name": "flask", "vulns": [{"id": "X"}, {"id": "Y"}]}]})
        self.assertEqual(parse_pip_audit(payload), 2)
        findings = findings_from_zip(_zip_with("pip-audit.json", payload), self.cfg, "security:pip")
        self.assertTrue(findings)
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("not a secrets-OK", findings[0].message)

    def test_npm_high_is_blocker(self):
        payload = json.dumps({"metadata": {"vulnerabilities": {"high": 1, "critical": 1, "low": 9}}})
        self.assertEqual(parse_npm_audit(payload), 2)
        findings = findings_from_zip(_zip_with("npm-audit.json", payload), self.cfg, "security:node")
        self.assertTrue(any(item.rule == "npm-audit" and item.severity == "blocker" for item in findings))


if __name__ == "__main__":
    unittest.main()
