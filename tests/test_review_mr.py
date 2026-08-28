"""Brutal run_review matrix: approve, unapprove, token, inline, idempotent, secrets."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from reviewer.config import DISCLAIMER, SUMMARY_MARKER
from reviewer.findings import Finding
from reviewer.jobs import evaluate_jobs
from reviewer.review import (
    decide,
    is_dry_run,
    is_mr_context,
    is_same_project_mr,
    run_review,
    setup_steps,
)


def _jobs(backend="success", node="success", pip="success", **extra):
    rows = [
        {"name": "backend", "status": backend, "id": 1},
        {"name": "frontend", "status": extra.get("frontend", "success"), "id": 2},
        {"name": "quality", "status": extra.get("quality", "success"), "id": 3},
        {"name": "build", "status": extra.get("build", "success"), "id": 4},
        {"name": "security:node", "status": node, "id": 5},
        {"name": "security:pip", "status": pip, "id": 6},
    ]
    return rows


def _mr_env(token="tok", **extra):
    env = {
        "CI_MERGE_REQUEST_IID": "12",
        "CI_PROJECT_ID": "42",
        "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": "42",
        "CI_MERGE_REQUEST_PROJECT_ID": "42",
        "CI_PIPELINE_ID": "99",
        "CI_SERVER_URL": "https://gitlab.example.com",
        "CI_MERGE_REQUEST_DESCRIPTION": "Ready for review.",
        "REVIEW_CONFIG": "/nonexistent/reviewer.json",
        "CI_PROJECT_DIR": "/tmp",
    }
    env.update(extra)
    if token:
        env["GITLAB_REVIEWER_TOKEN"] = token
    elif "GITLAB_REVIEWER_TOKEN" in env:
        del env["GITLAB_REVIEWER_TOKEN"]
    return env


def _tails(calls):
    tails = []
    for item in calls:
        url = item[1]
        tail = url.rstrip("/").split("/")[-1].split("?")[0]
        tails.append(tail)
    return tails


class FakeAPI:
    def __init__(self, jobs=None, approved=False, notes=None, discussions=None, changes=None, statuses=None):
        self.calls = []
        self.jobs = jobs if jobs is not None else _jobs()
        self.approved = approved
        self.notes = notes if notes is not None else []
        self.discussions = discussions if discussions is not None else []
        self.changes = changes if changes is not None else {"changes": [], "diff_refs": _refs()}
        self.statuses = statuses or {}

    def __call__(self, method, url, token, body=None, api_root=None):
        self.calls.append((method, url, token, body))
        key = None
        path = url.split("?", 1)[0].rstrip("/")
        if "unapprove" in path:
            key = "unapprove"
        elif path.endswith("/approve"):
            key = "approve"
        elif "/notes" in path:
            key = "notes_write" if method in {"POST", "PUT"} else "notes"
        elif "/discussions" in path:
            key = "discussions_write" if method in {"POST", "PUT"} else "discussions"
        elif path.endswith("/changes"):
            key = "changes"
        elif path.endswith("/approvals"):
            key = "approvals"
        elif "/jobs" in path:
            key = "jobs"
        elif path.split("/")[-2] == "merge_requests":
            key = "mr"
        if key in self.statuses:
            return self.statuses[key], {}
        if key == "changes":
            return 200, self.changes
        if key == "jobs":
            return 200, self.jobs
        if key == "approvals":
            return 200, {"approved": self.approved}
        if key == "notes":
            return 200, self.notes
        if key == "discussions":
            return 200, self.discussions
        if key == "mr":
            return 200, {"description": "Ready for review.", "diff_refs": _refs()}
        if key in {"notes_write", "discussions_write", "approve", "unapprove"}:
            return 201, {}
        return 404, {}


def _seed_git(root, branch="main"):
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "seed"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "branch", "-M", branch], cwd=str(root), check=True, capture_output=True)


def _refs():
    return {"base_sha": "aaa", "start_sha": "bbb", "head_sha": "ccc"}


def _pin_changes():
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " {\n"
        '   "name": "app",\n'
        '+  "react": "^18.3.1",\n'
        " }\n"
    )
    return {
        "diff_refs": _refs(),
        "changes": [{"old_path": "package.json", "new_path": "package.json", "diff": diff}],
    }


class ReviewTests(unittest.TestCase):
    def test_dry_run_without_mr(self):
        self.assertFalse(is_mr_context({}))
        self.assertTrue(is_dry_run({}))
        env = {"REVIEW_DRY_RUN": "1", "CI_MERGE_REQUEST_IID": "12"}
        self.assertTrue(is_dry_run(env))

    def test_dry_run_exits_zero_and_prints_one_note(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_review(
                {
                    "REVIEW_DRY_RUN": "1",
                    "REVIEW_CONFIG": "/nonexistent/reviewer.json",
                    "REVIEW_PROJECT_DIR": "/tmp",
                },
                local_diff_fn=lambda root: "",
            )
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("DRY-RUN", out)
        self.assertIn(SUMMARY_MARKER, out)
        self.assertIn(DISCLAIMER, out)
        self.assertEqual(out.count("## Reviewer bot"), 1)

    def test_missing_token_on_mr_fails_and_does_not_approve(self):
        api = FakeAPI()
        err = io.StringIO()
        with redirect_stderr(err):
            code = run_review(_mr_env(token=""), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertEqual(api.calls, [])
        self.assertIn("GITLAB_REVIEWER_TOKEN", err.getvalue())
        self.assertNotIn("approve", _tails(api.calls))

    def test_setup_steps_name_the_token_hardening(self):
        text = setup_steps()
        self.assertIn("GITLAB_REVIEWER_TOKEN", text)
        self.assertIn("Project Access Token", text)
        self.assertIn("Developer", text)
        self.assertIn("masked", text)
        self.assertIn("protected", text)
        self.assertIn("fork", text)
        self.assertIn("same-project", text)

    def test_debug_trace_refuses_before_api(self):
        api = FakeAPI()
        env = _mr_env()
        env["CI_DEBUG_TRACE"] = "true"
        err = io.StringIO()
        with redirect_stderr(err):
            code = run_review(env, curl_fn=api)
        self.assertEqual(code, 1)
        self.assertEqual(api.calls, [])
        self.assertIn("CI_DEBUG_TRACE", err.getvalue())

    def test_fork_mr_is_refused(self):
        api = FakeAPI()
        env = _mr_env()
        env["CI_MERGE_REQUEST_SOURCE_PROJECT_ID"] = "99"
        self.assertFalse(is_same_project_mr(env))
        code = run_review(env, curl_fn=api)
        self.assertEqual(code, 1)
        self.assertFalse(any(item[0] == "POST" and item[1].rstrip("/").endswith("/approve") for item in api.calls))

    def test_pip_audit_does_not_block(self):
        ok, lines = evaluate_jobs(_jobs(pip="failed"))
        self.assertTrue(ok)
        self.assertTrue(any("does not block" in line for line in lines))
        self.assertTrue(any("not a secrets-OK" in line for line in lines))

    def test_node_audit_blocks(self):
        ok, lines = evaluate_jobs(_jobs(node="failed"))
        self.assertFalse(ok)
        self.assertTrue(any("security:node" in line and "hold" in line for line in lines))

    def test_manual_job_holds_approve(self):
        api = FakeAPI(jobs=_jobs(backend="manual"))
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))

    def test_missing_product_jobs_hold_approve(self):
        api = FakeAPI(jobs=[{"name": "backend", "status": "success", "id": 1}])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))
        self.assertIn("missing", buf.getvalue())

    def test_product_jobs_block(self):
        for field in ("backend", "frontend", "quality", "build"):
            kwargs = {field: "failed"} if field == "backend" else {field: "failed"}
            if field == "backend":
                rows = _jobs(backend="failed")
            else:
                rows = _jobs(**{field: "failed"})
            ok, lines = evaluate_jobs(rows)
            self.assertFalse(ok, field)
            self.assertTrue(any(field in line and "hold" in line for line in lines), field)

    def test_decide_approve_hold_unapprove(self):
        self.assertEqual(decide(False, "", False, [], False), "dry-run")
        self.assertEqual(decide(True, "", True, [], False), "missing-token")
        self.assertEqual(decide(True, "tok", True, [], False), "approve")
        self.assertEqual(decide(True, "tok", False, [], True), "unapprove")
        self.assertEqual(decide(True, "tok", False, [Finding("x", "y")], False), "hold")
        self.assertEqual(decide(True, "tok", True, [Finding("x", "y")], False), "hold")
        self.assertEqual(decide(True, "tok", True, [], False, changes_ok=False), "hold")

    def test_approve_path_posts_note_and_approve(self):
        api = FakeAPI(jobs=_jobs(pip="failed"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 0)
        tails = _tails(api.calls)
        self.assertIn("approve", tails)
        self.assertNotIn("unapprove", tails)
        self.assertTrue(any(method == "POST" and item.endswith("/notes") for method, item, *_ in ((c[0], c[1]) for c in api.calls)))
        self.assertIn("not a secret review", buf.getvalue())
        for method, url, token, body in api.calls:
            self.assertNotIn(token, url)

    def test_idempotent_summary_note_puts_existing(self):
        api = FakeAPI(notes=[{"id": 7, "body": SUMMARY_MARKER + "\nold", "system": False}])
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 0)
        puts = [c for c in api.calls if c[0] == "PUT" and "/notes/7" in c[1]]
        posts = [c for c in api.calls if c[0] == "POST" and c[1].rstrip("/").endswith("/notes")]
        self.assertEqual(len(puts), 1)
        self.assertEqual(posts, [])

    def test_inline_discussion_on_pin_range(self):
        api = FakeAPI(changes=_pin_changes(), jobs=_jobs())
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        discs = [c for c in api.calls if c[0] == "POST" and c[1].endswith("/discussions")]
        self.assertTrue(discs)
        position = discs[0][3]["position"]
        self.assertEqual(position["new_path"], "package.json")
        self.assertIn("new_line", position)
        self.assertNotIn("^18.3.1", json_safe(discs[0][3]))

    def test_unapprove_on_later_red_push(self):
        api = FakeAPI(jobs=_jobs(backend="failed"), approved=True)
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        tails = _tails(api.calls)
        self.assertIn("unapprove", tails)
        self.assertNotIn("approve", tails)

    def test_hold_always_posts_unapprove(self):
        api = FakeAPI(jobs=_jobs(backend="failed"), approved=False)
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertIn("unapprove", _tails(api.calls))
        self.assertNotIn("approve", _tails(api.calls))

    def test_unapprove_404_is_ok(self):
        api = FakeAPI(jobs=_jobs(backend="failed"), statuses={"unapprove": 404})
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)

    def test_unapprove_http_zero_fails_job(self):
        api = FakeAPI(jobs=_jobs(backend="failed"), statuses={"unapprove": 0})
        err = io.StringIO()
        with redirect_stderr(err):
            code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertIn("Unapprove failed closed", err.getvalue())

    def test_changes_fetch_failure_blocks_approve(self):
        api = FakeAPI(statuses={"changes": 0})
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))

    def test_approvals_fetch_failure_unapproves(self):
        api = FakeAPI(statuses={"approvals": 500})
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))

    def test_note_status_zero_does_not_approve(self):
        api = FakeAPI(statuses={"notes_write": 0})
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))

    def test_ignores_ci_api_v4_url_override(self):
        api = FakeAPI()
        env = _mr_env()
        env["CI_API_V4_URL"] = "https://evil.example/api/v4"
        code = run_review(env, curl_fn=api)
        self.assertEqual(code, 0)
        for _method, url, _token, _body in api.calls:
            self.assertTrue(url.startswith("https://gitlab.example.com/api/v4"))
            self.assertNotIn("evil.example", url)

    def test_http_server_url_is_refused(self):
        api = FakeAPI()
        env = _mr_env()
        env["CI_SERVER_URL"] = "http://gitlab.example.com"
        code = run_review(env, curl_fn=api)
        self.assertEqual(code, 1)
        self.assertEqual(api.calls, [])

    def test_secret_in_diff_holds_and_does_not_print_snippet(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        diff = (
            "@@ -1,1 +1,2 @@\n"
            " print(1)\n"
            f"+KEY={secret}\n"
        )
        api = FakeAPI(
            changes={
                "diff_refs": _refs(),
                "changes": [{"old_path": "app.py", "new_path": "app.py", "diff": diff}],
            }
        )
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))
        self.assertNotIn(secret, buf.getvalue())
        self.assertNotIn(secret, err.getvalue())
        self.assertIn("not a secret review", buf.getvalue())

    def test_pip_audit_red_still_approves_without_secrets(self):
        api = FakeAPI(jobs=_jobs(pip="failed"))
        code = run_review(_mr_env(), curl_fn=api)
        self.assertEqual(code, 0)
        self.assertIn("approve", _tails(api.calls))

    def test_mr_empty_allowlist_cannot_approve_when_protected_list_exists(self):
        trusted = json.dumps({"backend": {"flask": "==3.0.0"}, "frontend": {}})
        diff = (
            "@@ -1,1 +1,2 @@\n"
            " flask==3.0.0\n"
            "+requests==2.31.0\n"
        )
        changes = {
            "diff_refs": _refs(),
            "changes": [
                {
                    "old_path": "requirements.txt",
                    "new_path": "requirements.txt",
                    "diff": diff,
                }
            ],
        }

        def show_fn(spec):
            if spec.endswith("approved-packages.json"):
                return trusted
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "approved-packages.json").write_text("{}", encoding="utf-8")
            api = FakeAPI(changes=changes)
            env = _mr_env(
                CI_PROJECT_DIR=str(root),
                CI_DEFAULT_BRANCH="main",
            )
            code = run_review(env, curl_fn=api, show_fn=show_fn)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))

    def test_mr_deleted_allowlist_cannot_approve_when_protected_list_exists(self):
        trusted = json.dumps({"frontend": {"react": "18.3.1"}})
        diff = (
            "@@ -1,3 +1,4 @@\n"
            " {\n"
            '   "name": "app",\n'
            '+  "lodash": "4.17.21",\n'
            " }\n"
        )
        changes = {
            "diff_refs": _refs(),
            "changes": [
                {
                    "old_path": "package.json",
                    "new_path": "package.json",
                    "diff": diff,
                }
            ],
        }

        def show_fn(spec):
            if spec.endswith("approved-packages.json"):
                return trusted
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI(changes=changes)
            env = _mr_env(
                CI_PROJECT_DIR=str(root),
                CI_DEFAULT_BRANCH="main",
            )
            code = run_review(env, curl_fn=api, show_fn=show_fn)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))

    def test_mr_review_project_dir_cannot_retarget_trusted_allowlist(self):
        diff = (
            "@@ -1,1 +1,2 @@\n"
            " flask==3.0.0\n"
            "+requests==2.31.0\n"
        )
        changes = {
            "diff_refs": _refs(),
            "changes": [
                {
                    "old_path": "requirements.txt",
                    "new_path": "requirements.txt",
                    "diff": diff,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            hosted = Path(tmp) / "hosted"
            decoy = Path(tmp) / "decoy"
            hosted.mkdir()
            decoy.mkdir()
            (hosted / "approved-packages.json").write_text(
                json.dumps({"backend": {"flask": "==3.0.0"}, "frontend": {}}),
                encoding="utf-8",
            )
            _seed_git(hosted)
            api = FakeAPI(changes=changes)
            env = _mr_env(
                CI_PROJECT_DIR=str(hosted),
                REVIEW_PROJECT_DIR=str(decoy),
                CI_DEFAULT_BRANCH="main",
            )
            code = run_review(env, curl_fn=api)
        self.assertEqual(code, 1)
        self.assertNotIn("approve", _tails(api.calls))
        self.assertIn("unapprove", _tails(api.calls))


def json_safe(value):
    if isinstance(value, dict):
        return " ".join(json_safe(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(json_safe(item) for item in value)
    return str(value)


if __name__ == "__main__":
    unittest.main()
