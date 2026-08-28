"""Policy must come from the protected ref; MR-tree cannot weaken blockers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reviewer.config import (
    DEFAULTS,
    apply_untrusted_policy,
    load_config,
    severity_for,
    trusted_ref_specs,
)
from reviewer.jobs import evaluate_jobs
from reviewer.scan import scan_diff
from reviewer.diff import parse_diff


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


class ConfigTests(unittest.TestCase):
    def test_defaults_require_blocking_jobs(self):
        self.assertTrue(DEFAULTS["require_blocking_jobs"])
        cfg = load_config({"REVIEW_CONFIG": "/nonexistent/reviewer.json"}, root="/tmp")
        self.assertTrue(cfg["require_blocking_jobs"])
        self.assertEqual(cfg["severities"]["secret"], "blocker")
        self.assertEqual(cfg["severities"]["pin-range"], "blocker")

    def test_trusted_ref_specs_prefer_target_sha(self):
        specs = trusted_ref_specs(
            {
                "CI_MERGE_REQUEST_TARGET_BRANCH_SHA": "abc123",
                "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
                "CI_DEFAULT_BRANCH": "main",
            }
        )
        self.assertEqual(specs[0], "abc123:reviewer.json")
        self.assertIn("origin/main:reviewer.json", specs)

    def test_mr_tree_cannot_downgrade_secret_or_empty_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reviewer.json").write_text(
                json.dumps(
                    {
                        "severities": {"secret": "warn", "pin-range": "warn"},
                        "blocking_jobs": [],
                        "require_blocking_jobs": False,
                    }
                ),
                encoding="utf-8",
            )
            trusted = {
                "severities": {"secret": "blocker", "pin-range": "blocker"},
                "blocking_jobs": ["backend", "frontend", "quality", "build", "security:node"],
                "require_blocking_jobs": True,
            }

            def show_fn(spec):
                if spec.endswith("reviewer.json"):
                    return json.dumps(trusted)
                return None

            cfg = load_config(
                {
                    "CI_MERGE_REQUEST_IID": "9",
                    "CI_DEFAULT_BRANCH": "main",
                    "REVIEW_PROJECT_DIR": str(root),
                },
                root=root,
                show_fn=show_fn,
            )
            self.assertEqual(cfg["severities"]["secret"], "blocker")
            self.assertEqual(cfg["severities"]["pin-range"], "blocker")
            self.assertTrue(cfg["blocking_jobs"])
            self.assertIn("backend", cfg["blocking_jobs"])
            self.assertTrue(cfg["require_blocking_jobs"])

    def test_working_tree_alone_cannot_beat_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reviewer.json").write_text(
                json.dumps(
                    {
                        "severities": {"secret": "warn", "pin-range": "warn"},
                        "blocking_jobs": [],
                        "require_blocking_jobs": False,
                    }
                ),
                encoding="utf-8",
            )
            cfg = load_config(
                {
                    "CI_MERGE_REQUEST_IID": "9",
                    "REVIEW_PROJECT_DIR": str(root),
                },
                root=root,
                show_fn=lambda spec: None,
            )
            self.assertEqual(severity_for(cfg, "secret"), "blocker")
            self.assertEqual(severity_for(cfg, "pin-range"), "blocker")
            self.assertTrue(cfg["blocking_jobs"])
            self.assertTrue(cfg["require_blocking_jobs"])
            diff = _hunk("app.py", "KEY=AKIAIOSFODNN7EXAMPLE")
            findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=cfg)
            self.assertTrue(any(item.rule == "secret" and item.severity == "blocker" for item in findings))

    def test_untrusted_empty_jobs_are_ignored(self):
        cfg = {
            "blocking_jobs": ["backend"],
            "severities": {"secret": "blocker"},
            "require_blocking_jobs": True,
        }
        apply_untrusted_policy(cfg, {"blocking_jobs": [], "severities": {"secret": "warn"}})
        self.assertEqual(cfg["blocking_jobs"], ["backend"])
        self.assertEqual(cfg["severities"]["secret"], "blocker")

    def test_mr_env_cannot_opt_out_require_jobs(self):
        cfg = load_config(
            {
                "CI_MERGE_REQUEST_IID": "4",
                "REVIEW_REQUIRE_JOBS": "0",
                "REVIEW_CONFIG": "/nonexistent/reviewer.json",
            },
            root="/tmp",
            show_fn=lambda spec: None,
        )
        self.assertTrue(cfg["require_blocking_jobs"])

    def test_include_file_pins_sha_not_job_variables(self):
        root = Path(__file__).resolve().parents[1]
        for rel in (".gitlab-ci-include.yml", "templates/review.yml"):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertNotIn("${GITLAB_REVIEWER_REF}", text, rel)
            self.assertNotIn("${GITLAB_REVIEWER_REPO}", text, rel)
            self.assertNotIn("$[[ inputs.reviewer_ref ]]", text, rel)
            self.assertNotIn("$[[ inputs.reviewer_repo ]]", text, rel)
            self.assertRegex(text, r"\b[0-9a-f]{40}\b")


class MissingJobsTests(unittest.TestCase):
    def test_missing_product_jobs_hold(self):
        ok, lines = evaluate_jobs([{"name": "backend", "status": "success"}])
        self.assertFalse(ok)
        self.assertTrue(any("frontend: missing" in line for line in lines))
        self.assertTrue(any("quality: missing" in line for line in lines))
        self.assertTrue(any("build: missing" in line for line in lines))
        self.assertTrue(any("security:node: missing" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
