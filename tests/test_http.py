"""Red-team HTTP: token off argv, https API root, fail closed on 0."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewer.http import (
    assert_url_allowed,
    debug_trace_on,
    derive_api_root,
    gitlab_curl,
    is_2xx,
    write_token_header_file,
)


class HttpTests(unittest.TestCase):
    def test_is_2xx_rejects_zero_and_errors(self):
        self.assertFalse(is_2xx(0))
        self.assertFalse(is_2xx("0"))
        self.assertFalse(is_2xx(404))
        self.assertFalse(is_2xx(500))
        self.assertFalse(is_2xx(None))
        self.assertTrue(is_2xx(200))
        self.assertTrue(is_2xx(201))

    def test_debug_trace_tokens(self):
        self.assertTrue(debug_trace_on({"CI_DEBUG_TRACE": "true"}))
        self.assertTrue(debug_trace_on({"CI_DEBUG_TRACE": "1"}))
        self.assertFalse(debug_trace_on({}))
        self.assertFalse(debug_trace_on({"CI_DEBUG_TRACE": "false"}))

    def test_derive_api_root_from_server_url_only(self):
        env = {
            "CI_SERVER_URL": "https://gitlab.example.com",
            "CI_API_V4_URL": "https://evil.example/api/v4",
        }
        self.assertEqual(derive_api_root(env), "https://gitlab.example.com/api/v4")

    def test_derive_api_root_rejects_http_and_userinfo(self):
        with self.assertRaises(ValueError):
            derive_api_root({"CI_SERVER_URL": "http://gitlab.example.com"})
        with self.assertRaises(ValueError):
            derive_api_root({"CI_SERVER_URL": "https://user:pass@gitlab.example.com"})
        with self.assertRaises(ValueError):
            derive_api_root({"CI_SERVER_URL": ""})

    def test_assert_url_allowed_host_and_https(self):
        root = "https://gitlab.example.com/api/v4"
        self.assertTrue(assert_url_allowed(f"{root}/projects/1", root))
        self.assertFalse(assert_url_allowed("https://evil.example/api/v4/projects/1", root))
        self.assertFalse(assert_url_allowed("http://gitlab.example.com/api/v4/projects/1", root))
        self.assertFalse(
            assert_url_allowed("https://user:pass@gitlab.example.com/api/v4/projects/1", root)
        )

    def test_header_file_is_0600_and_contains_token(self):
        path = write_token_header_file("s3cret-token")
        try:
            mode = oct(os.stat(path).st_mode & 0o777)
            self.assertEqual(mode, "0o600")
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("PRIVATE-TOKEN: s3cret-token", text)
        finally:
            os.remove(path)

    def test_gitlab_curl_keeps_token_off_argv(self):
        recorded = {}

        def fake_run(cmd, capture_output=True, text=True, check=False):
            recorded["cmd"] = cmd
            class Proc:
                stdout = "{}\n200"
                returncode = 0

            return Proc()

        root = "https://gitlab.example.com/api/v4"
        with patch("reviewer.http.subprocess.run", side_effect=fake_run):
            status, payload = gitlab_curl(
                "GET",
                f"{root}/projects/1",
                "super-secret-token",
                api_root=root,
            )
        self.assertEqual(status, 200)
        cmd = recorded["cmd"]
        joined = " ".join(cmd)
        self.assertNotIn("super-secret-token", joined)
        self.assertNotIn("PRIVATE-TOKEN: super-secret-token", joined)
        header_flags = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-H"]
        self.assertTrue(any(item.startswith("@") for item in header_flags))

    def test_gitlab_curl_refuses_off_root_as_status_zero(self):
        status, payload = gitlab_curl(
            "GET",
            "https://evil.example/api/v4/projects/1",
            "tok",
            api_root="https://gitlab.example.com/api/v4",
        )
        self.assertEqual(status, 0)
        self.assertEqual(payload, {})


if __name__ == "__main__":
    unittest.main()
