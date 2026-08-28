"""Policy must come from the protected ref; MR-tree cannot weaken blockers."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reviewer.config import (
    DEFAULTS,
    apply_trusted_policy,
    apply_untrusted_policy,
    helper_checkout_root,
    is_bot_clone_root,
    load_config,
    project_dir,
    severity_for,
    trusted_ref_specs,
    validate_mr_project_dir,
)
from reviewer.pins import load_review_allowlist
from reviewer.jobs import PASS_STATUSES, evaluate_jobs
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
    def test_mr_project_dir_ignores_review_override(self):
        decoy = Path("/tmp/decoy-review-root")
        hosted = Path("/tmp/hosted-ci-root")
        chosen = project_dir(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "REVIEW_PROJECT_DIR": str(decoy),
                "CI_PROJECT_DIR": str(hosted),
            }
        )
        self.assertEqual(chosen, hosted)
        fallback = project_dir(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "REVIEW_PROJECT_DIR": str(decoy),
            }
        )
        self.assertIsNone(fallback)

    def test_mr_rejects_missing_or_clone_ci_project_dir(self):
        missing_root, missing_reason = validate_mr_project_dir({"CI_MERGE_REQUEST_IID": "9"})
        self.assertIsNone(missing_root)
        self.assertIn("missing", missing_reason)
        clone = Path("/tmp/consumer/.gitlab-mr-reviewer")
        clone_root, clone_reason = validate_mr_project_dir(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "CI_PROJECT_DIR": str(clone),
            }
        )
        self.assertIsNone(clone_root)
        self.assertIn("clone", clone_reason)
        self.assertTrue(is_bot_clone_root(clone))
        self.assertTrue(is_bot_clone_root(helper_checkout_root()))
        helper_root, helper_reason = validate_mr_project_dir(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "CI_PROJECT_DIR": str(helper_checkout_root()),
            }
        )
        self.assertIsNone(helper_root)
        self.assertIn("clone", helper_reason)
        ok_root, ok_reason = validate_mr_project_dir(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "CI_PROJECT_DIR": "/tmp/hosted-ci-root",
            }
        )
        self.assertEqual(ok_root, Path("/tmp/hosted-ci-root"))
        self.assertIsNone(ok_reason)

    def test_laptop_project_dir_still_honors_review_override(self):
        override = Path("/tmp/laptop-review-root")
        chosen = project_dir({"REVIEW_PROJECT_DIR": str(override)})
        self.assertEqual(chosen, override)

    def test_mr_review_project_dir_cannot_retarget_trusted_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            hosted = Path(tmp) / "hosted"
            decoy = Path(tmp) / "decoy"
            hosted.mkdir()
            decoy.mkdir()
            (hosted / "reviewer.json").write_text(json.dumps({"huge_diff_lines": 424}), encoding="utf-8")
            (decoy / "reviewer.json").write_text(
                json.dumps(
                    {
                        "huge_diff_lines": 1,
                        "path_rules": [{"path_glob": "evil/**", "pattern": "x", "message": "decoy"}],
                    }
                ),
                encoding="utf-8",
            )
            (hosted / "approved-packages.json").write_text(
                json.dumps({"backend": {"flask": "==3.0.0"}}),
                encoding="utf-8",
            )
            for repo in (hosted, decoy):
                subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
                subprocess.run(["git", "-C", str(repo), "config", "user.name", "tester"], check=True)
                subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
                subprocess.run(
                    ["git", "-c", "commit.gpgsign=false", "commit", "-m", "seed"],
                    cwd=str(repo),
                    check=True,
                    capture_output=True,
                )
                subprocess.run(["git", "branch", "-M", "main"], cwd=str(repo), check=True, capture_output=True)
            env = {
                "CI_MERGE_REQUEST_IID": "9",
                "CI_PROJECT_DIR": str(hosted),
                "REVIEW_PROJECT_DIR": str(decoy),
                "CI_DEFAULT_BRANCH": "main",
            }
            root = project_dir(env)
            self.assertEqual(root, hosted)
            cfg = load_config(env, root=root)
            self.assertEqual(cfg["huge_diff_lines"], 424)
            self.assertFalse(any(rule.get("message") == "decoy" for rule in cfg.get("path_rules") or []))
            allow = load_review_allowlist(root, env)
            self.assertIn("flask", allow["backend"])

    def test_defaults_require_blocking_jobs(self):
        self.assertTrue(DEFAULTS["require_blocking_jobs"])
        cfg = load_config({"REVIEW_CONFIG": "/nonexistent/reviewer.json"}, root="/tmp")
        self.assertTrue(cfg["require_blocking_jobs"])
        self.assertEqual(cfg["severities"]["secret"], "blocker")
        self.assertEqual(cfg["severities"]["pin-range"], "blocker")

    def test_trusted_ref_specs_ignore_overridable_target_sha(self):
        specs = trusted_ref_specs(
            {
                "CI_MERGE_REQUEST_TARGET_BRANCH_SHA": "abc123def456",
                "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
                "CI_DEFAULT_BRANCH": "main",
            }
        )
        self.assertFalse(any(item.startswith("abc123def456:") for item in specs))
        self.assertEqual(specs[0], "origin/main:reviewer.json")
        self.assertIn("main:reviewer.json", specs)

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
            "pin_files": list(DEFAULTS["pin_files"]),
            "job_aliases": dict(DEFAULTS["job_aliases"]),
        }
        apply_untrusted_policy(cfg, {"blocking_jobs": [], "severities": {"secret": "warn"}})
        self.assertEqual(cfg["blocking_jobs"], ["backend"])
        self.assertEqual(cfg["severities"]["secret"], "blocker")

    def test_mr_tree_cannot_remap_blockers_via_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reviewer.json").write_text(
                json.dumps({"job_aliases": {"dummy": "backend"}}),
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
            self.assertNotEqual(cfg["job_aliases"].get("dummy"), "backend")
            ok, lines = evaluate_jobs(
                [{"name": "dummy", "status": "success"}],
                cfg=cfg,
            )
            self.assertFalse(ok)
            self.assertTrue(any("backend: missing" in line for line in lines))

    def test_mr_tree_cannot_empty_pin_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reviewer.json").write_text(
                json.dumps({"pin_files": []}),
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
            self.assertTrue(cfg["pin_files"])
            self.assertIn("package.json", cfg["pin_files"])
            self.assertIn("requirements.txt", cfg["pin_files"])
            diff = _hunk("package.json", '  "react": "^18.3.1",')
            findings = scan_diff(parse_diff(diff), allow={"backend": {}, "frontend": {}}, cfg=cfg)
            self.assertTrue(any(item.rule == "pin-range" for item in findings))

    def test_untrusted_cannot_replace_path_rules(self):
        keep = {"path_glob": "secret/**", "pattern": "TODO", "message": "keep"}
        cfg = {
            "blocking_jobs": ["backend"],
            "severities": {"secret": "blocker"},
            "path_rules": [keep],
            "pin_files": list(DEFAULTS["pin_files"]),
            "job_aliases": dict(DEFAULTS["job_aliases"]),
        }
        apply_untrusted_policy(cfg, {"path_rules": []})
        self.assertEqual(cfg["path_rules"], [keep])
        apply_untrusted_policy(
            cfg,
            {"path_rules": [{"path_glob": "docs/**", "pattern": "FIXME", "message": "add"}]},
        )
        self.assertEqual(cfg["path_rules"][0], keep)
        self.assertEqual(len(cfg["path_rules"]), 2)

    def test_untrusted_cannot_replace_pin_files_or_aliases(self):
        cfg = {
            "blocking_jobs": ["backend"],
            "severities": {"secret": "blocker"},
            "pin_files": ["requirements.txt", "package.json"],
            "job_aliases": {"node-audit": "security:node"},
        }
        apply_untrusted_policy(
            cfg,
            {
                "pin_files": [],
                "job_aliases": {"dummy": "backend", "node-audit": "other"},
            },
        )
        self.assertEqual(cfg["pin_files"], ["requirements.txt", "package.json"])
        self.assertEqual(cfg["job_aliases"], {"node-audit": "security:node"})
        self.assertNotIn("dummy", cfg["job_aliases"])

    def test_trusted_cannot_downgrade_or_empty_vs_defaults(self):
        cfg = load_config(
            {
                "CI_MERGE_REQUEST_IID": "9",
                "CI_MERGE_REQUEST_TARGET_BRANCH_SHA": "deadbeef" * 5,
                "CI_DEFAULT_BRANCH": "main",
                "REVIEW_CONFIG": "/nonexistent/reviewer.json",
            },
            root="/tmp",
            show_fn=lambda spec: json.dumps(
                {
                    "severities": {"secret": "warn", "pin-range": "warn"},
                    "blocking_jobs": [],
                    "require_blocking_jobs": False,
                    "pin_files": [],
                    "job_aliases": {"dummy": "backend"},
                }
            ),
        )
        self.assertEqual(cfg["severities"]["secret"], "blocker")
        self.assertEqual(cfg["severities"]["pin-range"], "blocker")
        self.assertTrue(cfg["blocking_jobs"])
        self.assertIn("backend", cfg["blocking_jobs"])
        self.assertTrue(cfg["require_blocking_jobs"])
        self.assertTrue(cfg["pin_files"])
        self.assertNotEqual(cfg["job_aliases"].get("dummy"), "backend")

    def test_trusted_union_adds_jobs_without_dropping_defaults(self):
        cfg = {
            "blocking_jobs": list(DEFAULTS["blocking_jobs"]),
            "severities": dict(DEFAULTS["severities"]),
            "pin_files": list(DEFAULTS["pin_files"]),
            "job_aliases": dict(DEFAULTS["job_aliases"]),
            "require_blocking_jobs": True,
        }
        apply_trusted_policy(cfg, {"blocking_jobs": ["test"], "severities": {"secret": "warn"}})
        self.assertIn("backend", cfg["blocking_jobs"])
        self.assertIn("test", cfg["blocking_jobs"])
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
        pins = []
        for rel in (".gitlab-ci-include.yml", "templates/review.yml"):
            text = (root / rel).read_text(encoding="utf-8")
            self.assertNotIn("${GITLAB_REVIEWER_REF}", text, rel)
            self.assertNotIn("${GITLAB_REVIEWER_REPO}", text, rel)
            self.assertNotIn("$[[ inputs.reviewer_ref ]]", text, rel)
            self.assertNotIn("$[[ inputs.reviewer_repo ]]", text, rel)
            match = re.search(
                r"git -C \.gitlab-mr-reviewer fetch --depth 1 origin ([0-9a-f]{40})",
                text,
            )
            self.assertIsNotNone(match, rel)
            pins.append(match.group(1))
        self.assertEqual(pins[0], pins[1])

    def test_include_pin_matches_head(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from check_include_pin import check_includes, pin_equals_head

        self.assertTrue(
            pin_equals_head(
                "a" * 40,
                "a" * 40,
            )
        )
        self.assertTrue(
            pin_equals_head(
                "b" * 40,
                "c" * 40,
                parent="b" * 40,
                parent_changed=[".gitlab-ci-include.yml", "templates/review.yml"],
            )
        )
        self.assertFalse(
            pin_equals_head(
                "b" * 40,
                "c" * 40,
                parent="b" * 40,
                parent_changed=[".gitlab-ci-include.yml", "reviewer/config.py"],
            )
        )
        self.assertFalse(pin_equals_head("d" * 40, "e" * 40))
        self.assertEqual(check_includes(Path(__file__).resolve().parents[1]), [])


class MissingJobsTests(unittest.TestCase):
    def test_missing_product_jobs_hold(self):
        ok, lines = evaluate_jobs([{"name": "backend", "status": "success"}])
        self.assertFalse(ok)
        self.assertTrue(any("frontend: missing" in line for line in lines))
        self.assertTrue(any("quality: missing" in line for line in lines))
        self.assertTrue(any("build: missing" in line for line in lines))
        self.assertTrue(any("security:node: missing" in line for line in lines))

    def test_manual_status_is_not_pass(self):
        self.assertNotIn("manual", PASS_STATUSES)
        ok, lines = evaluate_jobs(_jobs_manual_backend())
        self.assertFalse(ok)
        self.assertTrue(any("backend: manual" in line and "hold" in line for line in lines))


def _jobs_manual_backend():
    return [
        {"name": "backend", "status": "manual"},
        {"name": "frontend", "status": "success"},
        {"name": "quality", "status": "success"},
        {"name": "build", "status": "success"},
        {"name": "security:node", "status": "success"},
    ]


if __name__ == "__main__":
    unittest.main()
