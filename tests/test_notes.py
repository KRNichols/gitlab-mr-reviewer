"""Note escaping and idempotent summary updates."""

from __future__ import annotations

import unittest

from reviewer.findings import Finding
from reviewer.notes import (
    build_note,
    escape_note_text,
    find_summary_note,
    upsert_inline_discussions,
    upsert_summary_note,
)
from reviewer.config import DISCLAIMER, SUMMARY_MARKER


class NotesTests(unittest.TestCase):
    def test_escape_strips_at_and_fences(self):
        text = escape_note_text("hello @user see ```secret```")
        self.assertNotIn("@", text)
        self.assertNotIn("```", text)
        self.assertIn("user", text)

    def test_build_note_fences_and_disclaimer(self):
        finding = Finding("pin-range", "react specifier is not an exact pin", path="package.json", line=3)
        note = build_note("HOLD", ["backend: success", "ping @root"], [finding], "Holding.")
        self.assertIn(SUMMARY_MARKER, note)
        self.assertIn("**Verdict:** HOLD", note)
        self.assertIn("### Pipeline", note)
        self.assertIn("### Findings", note)
        self.assertIn(DISCLAIMER, note)
        self.assertIn("not a secret review", note.lower())
        self.assertIn("```", note)
        self.assertNotIn("@root", note)
        self.assertNotIn("@", note.split("### Scope")[0])

    def test_build_note_does_not_echo_raw_added_line(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        finding = Finding("secret", "possible aws-access-key on an added line", path="app.py")
        note = build_note("HOLD", [], [finding], "Holding.")
        self.assertNotIn(secret, note)
        self.assertNotIn("KEY=AKIA", note)

    def test_find_summary_note(self):
        notes = [
            {"id": 1, "body": "human", "system": False},
            {"id": 7, "body": SUMMARY_MARKER + "\nold", "system": False},
        ]
        found = find_summary_note(notes)
        self.assertEqual(found["id"], 7)

    def test_upsert_updates_existing_note(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, token, body))
            return 200, {}

        existing = [{"id": 7, "body": SUMMARY_MARKER + "\nold", "system": False}]
        ok, status, method = upsert_summary_note(
            request, "https://gitlab.example.com/api/v4/projects/1/merge_requests/2", "tok", "NEW", existing
        )
        self.assertTrue(ok)
        self.assertEqual(method, "PUT")
        self.assertTrue(any(item[0] == "PUT" and "/notes/7" in item[1] for item in calls))
        self.assertFalse(any(item[0] == "POST" and item[1].rstrip("/").endswith("/notes") for item in calls))

    def test_upsert_posts_when_missing(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, token, body))
            return 201, {"id": 9}

        ok, status, method = upsert_summary_note(
            request, "https://gitlab.example.com/api/v4/projects/1/merge_requests/2", "tok", "NEW", []
        )
        self.assertTrue(ok)
        self.assertEqual(method, "POST")

    def test_upsert_fails_closed_on_status_zero(self):
        def request(method, url, token, body=None):
            return 0, {}

        ok, status, method = upsert_summary_note(
            request, "https://gitlab.example.com/api/v4/projects/1/merge_requests/2", "tok", "NEW", []
        )
        self.assertFalse(ok)
        self.assertEqual(status, 0)

    def test_inline_posts_position_for_blocker_line(self):
        calls = []

        def request(method, url, token, body=None):
            calls.append((method, url, token, body))
            return 201, {}

        finding = Finding("pin-range", "not an exact pin", path="package.json", line=4)
        refs = {"base_sha": "aaa", "start_sha": "bbb", "head_sha": "ccc"}
        ok, status = upsert_inline_discussions(
            request,
            "https://gitlab.example.com/api/v4/projects/1/merge_requests/2",
            "tok",
            [finding],
            refs,
            [],
        )
        self.assertTrue(ok)
        posts = [item for item in calls if item[0] == "POST" and item[1].endswith("/discussions")]
        self.assertEqual(len(posts), 1)
        body = posts[0][3]
        self.assertEqual(body["position"]["new_line"], 4)
        self.assertEqual(body["position"]["new_path"], "package.json")
        self.assertNotIn("old_line", body["position"])


if __name__ == "__main__":
    unittest.main()
