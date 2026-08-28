"""GitLab HTTP via python3 plus curl, with the token kept off argv."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from urllib.parse import quote, urlparse


def is_2xx(status):
    """
    What: True when an HTTP status is a success in the 200 class.
    Why: Status 0 and every non-2xx must fail closed before Approve.
    Who: gitlab_curl callers, post_note, and set_approval.
    Where: Integer status parsed from curl's trailing http_code.
    How: Accept 200 inclusive through 299 inclusive.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    return 200 <= code <= 299


def debug_trace_on(env):
    """
    What: True when GitLab would print every shell command, including curl.
    Why: CI_DEBUG_TRACE would leak the token from the 0600 header file path.
    Who: run_review before it writes any token material to disk.
    Where: CI_DEBUG_TRACE on the hosted job environment.
    How: Treat 1/true/yes/on as enabled after a case-fold strip.
    """
    flag = str((env or {}).get("CI_DEBUG_TRACE") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _has_userinfo(raw, parsed):
    """
    What: True when a URL embeds credentials in the authority.
    Why: userinfo in CI_SERVER_URL is an injection and must be refused.
    Who: derive_api_root and assert_url_allowed.
    Where: The raw string plus urllib.parse result.
    How: Look for username/password fields or an @ in the netloc.
    """
    if parsed.username or parsed.password:
        return True
    netloc = parsed.netloc or ""
    if "@" in netloc:
        return True
    rest = (raw or "").split("://", 1)[-1]
    hostpart = rest.split("/", 1)[0]
    return "@" in hostpart


def derive_api_root(env):
    """
    What: Build https://<CI_SERVER_URL host and path>/api/v4 and nothing else.
    Why: A job-level CI_API_V4_URL override can point curl at an attacker host.
    Who: run_review on the live merge-request path only.
    Where: CI_SERVER_URL from GitLab, never CI_API_V4_URL.
    How: Require https, reject userinfo, allowlist that host, append /api/v4.
    """
    data = env or {}
    raw = str(data.get("CI_SERVER_URL") or "").strip()
    if not raw:
        raise ValueError("CI_SERVER_URL is required")
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValueError("CI_SERVER_URL must use https")
    if _has_userinfo(raw, parsed):
        raise ValueError("CI_SERVER_URL must not include userinfo")
    host = parsed.hostname
    if not host:
        raise ValueError("CI_SERVER_URL host is missing")
    path = (parsed.path or "").rstrip("/")
    port = ""
    if parsed.port and parsed.port != 443:
        port = f":{parsed.port}"
    return f"https://{host}{port}{path}/api/v4"


def assert_url_allowed(url, api_root):
    """
    What: True when a request URL stays on the derived https API root.
    Why: curl must never follow a caller-supplied host or userinfo URL.
    Who: gitlab_curl before it starts the child process.
    Where: Every notes, discussions, jobs, and approve URL.
    How: Parse the URL, require https, reject userinfo, prefix-match the root.
    """
    raw = str(url or "")
    root = str(api_root or "")
    if not raw or not root:
        return False
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        return False
    if _has_userinfo(raw, parsed):
        return False
    root_parsed = urlparse(root)
    if parsed.hostname != root_parsed.hostname:
        return False
    return raw.startswith(root)


def project_base(env, api_root):
    """
    What: /projects/:id prefix for merge-request routes on the allowlisted host.
    Why: Notes, changes, and approvals all hang off the same project URL.
    Who: run_review when it builds live endpoints.
    Where: URL-encoded CI_PROJECT_ID under the derived API root.
    How: Quote the project id and join it to /projects/.
    """
    project = quote(str((env or {}).get("CI_PROJECT_ID") or ""), safe="")
    return f"{api_root.rstrip('/')}/projects/{project}"


def write_token_header_file(token):
    """
    What: Write PRIVATE-TOKEN into a 0600 tempfile and return that path.
    Why: curl argv must never contain the token, including in -H values.
    Who: gitlab_curl and gitlab_download for the life of one request.
    Where: A tempfile in the process temp dir, deleted by the caller.
    How: mkstemp, fchmod 0600, write one header line, then close the fd.
    """
    fd, path = tempfile.mkstemp(prefix="glrev-hdr-", text=True)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"PRIVATE-TOKEN: {token}\n")
    return path


def _split_status(raw):
    """
    What: Split curl stdout into a JSON payload and the trailing status code.
    Why: -w appends http_code after the body; status 0 means curl never ran.
    Who: gitlab_curl after subprocess.run returns.
    Where: Combined stdout from curl -w newline-percent-http_code.
    How: rsplit on the last newline; default status 0 when the trailer is junk.
    """
    text = raw or ""
    if "\n" in text:
        payload_text, status_text = text.rsplit("\n", 1)
    else:
        payload_text, status_text = text, "0"
    try:
        status = int(status_text.strip() or "0")
    except ValueError:
        status = 0
    return status, payload_text.strip()


def gitlab_curl(method, url, token, body=None, api_root=None):
    """
    What: One GitLab REST call using python3 plus curl, token off argv.
    Why: The hosted image is python:3.12 and must not grow HTTP libraries.
    Who: run_review when the caller does not inject curl_fn.
    Where: Allowlisted https URLs under the derived /api/v4 root.
    How: Header file 0600, refuse bad URLs as status 0, parse JSON plus status.
    """
    if api_root and not assert_url_allowed(url, api_root):
        return 0, {}
    header_path = None
    try:
        header_path = write_token_header_file(token)
        cmd = [
            "curl",
            "-sS",
            "-X",
            str(method or "GET"),
            "-H",
            f"@{header_path}",
            "-H",
            "Accept: application/json",
            "-w",
            "\n%{http_code}",
        ]
        if body is not None:
            cmd.extend(
                [
                    "-H",
                    "Content-Type: application/json",
                    "--data-binary",
                    json.dumps(body),
                ]
            )
        cmd.append(url)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError:
            return 0, {}
        status, payload_text = _split_status(proc.stdout or "")
        if not payload_text:
            return status, {}
        try:
            return status, json.loads(payload_text)
        except json.JSONDecodeError:
            return status, {"raw": payload_text}
    finally:
        if header_path:
            try:
                os.remove(header_path)
            except OSError:
                pass


def gitlab_download(url, token, dest, api_root=None):
    """
    What: Download a job artifact zip with the token kept in a 0600 header file.
    Why: Artifact parsers need bytes, not JSON, and still must hide the token.
    Who: consume_artifacts when a job advertises artifacts_file.
    Where: GET /projects/:id/jobs/:id/artifacts on the allowlisted host.
    How: curl -o dest, write the status to stdout, return the integer code.
    """
    if api_root and not assert_url_allowed(url, api_root):
        return 0
    header_path = None
    try:
        header_path = write_token_header_file(token)
        cmd = [
            "curl",
            "-sS",
            "-X",
            "GET",
            "-H",
            f"@{header_path}",
            "-o",
            str(dest),
            "-w",
            "%{http_code}",
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError:
            return 0
        try:
            return int((proc.stdout or "0").strip() or "0")
        except ValueError:
            return 0
    finally:
        if header_path:
            try:
                os.remove(header_path)
            except OSError:
                pass
