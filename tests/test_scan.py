"""Diff scanners: pins, secrets, five-part comments, no raw snippets."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reviewer.config import load_config
from reviewer.diff import parse_diff
from reviewer.pins import load_review_allowlist, merge_allowlists
from reviewer.scan import classify_secret, description_has_story_url, scan_diff, scan_meta


def _hunk(path, *added):
    rows = list(added)
    lines = [
        f"diff --git a/{path} b/{path}",
        f"--- a/{path}",
        f"+++ b/{path}",
        "@@ -0,0 +1,%d @@" % len(rows),
    ]
    for row in rows:
        lines.append("+" + row)
    return "\n".join(lines) + "\n"


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config({"REVIEW_CONFIG": "/nonexistent/reviewer.json"}, root="/tmp")

    def test_caret_pin_is_a_blocker(self):
        diff = _hunk("frontend/package.json", '    "react": "^18.3.1",')
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertTrue(any(item.rule == "pin-range" and item.severity == "blocker" for item in findings))
        joined = " ".join(item.message for item in findings)
        self.assertNotIn("^18.3.1", joined)

    def test_tilde_and_range_are_findings(self):
        diff = _hunk("backend/requirements.txt", "flask>=2.0.0", "django~=4.2")
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertGreaterEqual(sum(1 for item in findings if item.rule == "pin-range"), 1)

    def test_empty_head_keep_protected_allowlist(self):
        trusted = {"backend": {"flask": "==3.0.0"}, "frontend": {}}
        merged = merge_allowlists(trusted, {"backend": {}, "frontend": {}})
        self.assertEqual(merged["backend"]["flask"], "==3.0.0")
        diff = _hunk("backend/requirements.txt", "requests==2.31.0")
        findings = scan_diff(parse_diff(diff), allow=merged, cfg=self.cfg)
        self.assertTrue(any(item.rule == "pin-allowlist" for item in findings))

    def test_load_review_allowlist_ignores_emptied_checkout(self):
        trusted_text = '{"backend": {"flask": "==3.0.0"}, "frontend": {}}'

        def show_fn(spec):
            if spec.endswith("approved-packages.json"):
                return trusted_text
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "approved-packages.json").write_text(
                '{"backend": {}, "frontend": {}}',
                encoding="utf-8",
            )
            allow = load_review_allowlist(
                root,
                {"CI_DEFAULT_BRANCH": "main"},
                show_fn=show_fn,
            )
        self.assertTrue(allow["backend"])
        self.assertIn("flask", allow["backend"])
        diff = _hunk("backend/requirements.txt", "evil==1.0.0")
        findings = scan_diff(parse_diff(diff), allow=allow, cfg=self.cfg)
        self.assertTrue(any(item.rule == "pin-allowlist" for item in findings))

    def test_exact_pin_is_not_a_range_finding(self):
        diff = _hunk("backend/requirements.txt", "flask==3.0.0")
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertFalse(any(item.rule == "pin-range" for item in findings))

    def test_secret_akia_holds_and_does_not_echo(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        diff = _hunk("backend/app.py", f"KEY={secret}")
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        secrets = [item for item in findings if item.rule == "secret"]
        self.assertTrue(secrets)
        self.assertEqual(secrets[0].severity, "blocker")
        self.assertNotIn(secret, secrets[0].message)
        self.assertNotIn(secret, secrets[0].as_line())

    def test_pem_and_reviewer_token_assignment(self):
        self.assertEqual(classify_secret("-----BEGIN RSA PRIVATE KEY-----"), "pem-private-key")
        self.assertEqual(
            classify_secret("GITLAB_REVIEWER_TOKEN=glpat-aaaaaaaaaaaaaaaaaaaa"),
            "reviewer-token-assignment",
        )
        self.assertEqual(classify_secret("token = os.environ['GITLAB_REVIEWER_TOKEN']"), "")

    def test_new_function_without_comment_is_a_finding(self):
        diff = _hunk("reviewer/extra.py", "def brand_new_helper():", "    return 1")
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertTrue(any("five-part" in item.rule for item in findings))

    def test_new_function_with_comment_passes(self):
        diff = _hunk(
            "reviewer/extra.py",
            "def brand_new_helper():",
            '    """',
            "    What: Helper used only in this unit test.",
            "    Why: Prove a documented new function is not a finding.",
            "    Who: The reviewer bot scan_diff path.",
            "    Where: A synthetic backend module hunk.",
            "    How: Return a constant after the five-part docstring.",
            '    """',
            "    return 1",
        )
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertFalse(any(item.rule == "five-part" for item in findings))

    def test_labels_alone_are_not_enough(self):
        diff = _hunk(
            "reviewer/extra.py",
            "def brand_new_helper():",
            '    """',
            "    What: helper",
            "    Why: helper",
            "    Who: us",
            "    Where: here",
            "    How: return 1",
            '    """',
            "    return 1",
        )
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        hits = [item for item in findings if item.rule == "five-part"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].severity, "blocker")
        joined = " ".join(item.message for item in hits)
        self.assertIn("fails", joined)

    def test_placeholder_five_part_is_a_finding(self):
        diff = _hunk(
            "reviewer/extra.py",
            "def brand_new_helper():",
            '    """',
            "    What: Placeholder helper used by the unit test.",
            "    Why: TODO fill this in after review.",
            "    Who: The reviewer bot scan_diff path.",
            "    Where: A synthetic backend module hunk.",
            "    How: Return a constant after the five-part docstring.",
            '    """',
            "    return 1",
        )
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertTrue(any(item.rule == "five-part" for item in findings))

    def test_what_equals_why_is_a_finding(self):
        diff = _hunk(
            "reviewer/extra.py",
            "def brand_new_helper():",
            '    """',
            "    What: Validates the inbound payload shape.",
            "    Why: Validates the inbound payload shape.",
            "    Who: The reviewer bot scan_diff path.",
            "    Where: A synthetic backend module hunk.",
            "    How: Return a constant after the five-part docstring.",
            '    """',
            "    return 1",
        )
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertTrue(any("WHAT equals WHY" in item.message for item in findings if item.rule == "five-part"))

    def test_how_copies_body_is_a_finding(self):
        diff = _hunk(
            "reviewer/extra.py",
            "def brand_new_helper():",
            '    """',
            "    What: Helper used only in this unit test.",
            "    Why: Prove a copied HOW line is still a finding.",
            "    Who: The reviewer bot scan_diff path.",
            "    Where: A synthetic backend module hunk.",
            "    How: return 1",
            '    """',
            "    return 1",
        )
        findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=self.cfg)
        self.assertTrue(any("HOW copies body" in item.message for item in findings if item.rule == "five-part"))

    def test_story_url_rejects_bare_ids_and_badges(self):
        self.assertFalse(description_has_story_url(""))
        self.assertFalse(description_has_story_url("Ready for review. ADO-1234 see the wiki."))
        self.assertFalse(description_has_story_url("![ci](https://img.shields.io/badge/build-ok-green)"))
        self.assertTrue(description_has_story_url("Story: https://dev.azure.com/org/proj/_workitems/edit/88"))
        self.assertTrue(description_has_story_url("[Login](https://jira.example.com/browse/PD-88)"))

    def test_scan_meta_missing_story_url_is_blocker(self):
        findings = scan_meta([], "Ready for review. ADO-1234", self.cfg)
        hits = [item for item in findings if item.rule == "missing-story-link"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].severity, "blocker")
        self.assertFalse(any(item.rule == "empty-description" for item in findings))

    def test_scan_meta_empty_description_stays_empty_rule(self):
        findings = scan_meta([], "   ", self.cfg)
        self.assertTrue(any(item.rule == "empty-description" for item in findings))
        self.assertFalse(any(item.rule == "missing-story-link" for item in findings))

    def test_scan_meta_markdown_story_link_passes(self):
        findings = scan_meta([], "Story: [PD-88](https://jira.example.com/browse/PD-88)", self.cfg)
        self.assertFalse(any(item.rule in {"missing-story-link", "empty-description"} for item in findings))

    def test_parse_diff_paths_and_line_numbers(self):
        diff = _hunk("foo.py", "print(1)")
        files = parse_diff(diff)
        self.assertEqual(files[0]["path"], "foo.py")
        self.assertEqual(files[0]["added"][0]["new_line"], 1)


if __name__ == "__main__":
    unittest.main()
