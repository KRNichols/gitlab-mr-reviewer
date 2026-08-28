"""Parse unified diffs and GitLab change payloads into file records."""

from __future__ import annotations

import re

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(text):
    """
    What: Split a unified diff into path, added rows, and new-file line numbers.
    Why: Inline discussions and scanners need a line, not a raw blob.
    Who: scan_diff and parse_changes.
    Where: git diff output or a reconstructed GitLab change patch.
    How: Cut on diff --git, honor +++ b/, and count hunk plus/minus lines.
    """
    files = []
    current = None
    old_line = 0
    new_line = 0
    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            parts = line.split()
            path = ""
            if len(parts) >= 4:
                right = parts[3]
                path = right[2:] if right.startswith("b/") else right
            current = {
                "path": path,
                "old_path": path,
                "added": [],
                "removed": [],
                "added_count": 0,
                "removed_count": 0,
            }
            continue
        if current is None:
            continue
        if line.startswith("--- a/"):
            current["old_path"] = line[6:]
            continue
        if line.startswith("+++ b/"):
            current["path"] = line[6:]
            continue
        hunk = HUNK_RE.match(line)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(2))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"].append({"text": line[1:], "new_line": new_line})
            current["added_count"] += 1
            new_line += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            current["removed"].append({"text": line[1:], "old_line": old_line})
            current["removed_count"] += 1
            old_line += 1
            continue
        if line.startswith("\\"):
            continue
        if line.startswith(" "):
            old_line += 1
            new_line += 1
    if current is not None:
        files.append(current)
    return files


def parse_changes(payload):
    """
    What: Turn a GitLab changes JSON payload into the same file records.
    Why: Hosted review reads /merge_requests/:iid/changes, not a local git diff.
    Who: run_review after a successful changes GET.
    Where: payload.changes[] with new_path, old_path, and diff.
    How: Wrap a bare hunk in a fake diff --git header, then parse_diff.
    """
    changes = []
    if isinstance(payload, dict):
        changes = payload.get("changes") or payload.get("diffs") or []
    elif isinstance(payload, list):
        changes = payload
    files = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = str(item.get("new_path") or item.get("old_path") or "")
        old_path = str(item.get("old_path") or path)
        diff = item.get("diff") or ""
        if not str(diff).startswith("diff --git "):
            diff = f"diff --git a/{old_path} b/{path}\n--- a/{old_path}\n+++ b/{path}\n{diff}"
        parsed = parse_diff(diff)
        for record in parsed:
            if path:
                record["path"] = path
            if old_path:
                record["old_path"] = old_path
            files.append(record)
    return files


def diff_refs_of(payload):
    """
    What: Pull base/start/head SHAs from a merge request or changes payload.
    Why: The discussions position object needs those three SHAs.
    Who: upsert_inline_discussions before it POSTs a diff thread.
    Where: payload.diff_refs on GET merge_request or GET changes.
    How: Read the three keys when the payload is a dict; else return empty.
    """
    if not isinstance(payload, dict):
        return {}
    refs = payload.get("diff_refs") or {}
    if not isinstance(refs, dict):
        return {}
    return {
        "base_sha": str(refs.get("base_sha") or ""),
        "start_sha": str(refs.get("start_sha") or ""),
        "head_sha": str(refs.get("head_sha") or ""),
    }


def changed_line_total(files):
    """
    What: Count added plus removed lines across parsed file records.
    Why: Huge-diff is a warn finding once the total crosses the configured cap.
    Who: scan_meta after the diff is parsed.
    Where: Each record's added_count and removed_count.
    How: Sum the two counters for every file.
    """
    total = 0
    for item in files or []:
        total += int(item.get("added_count") or 0)
        total += int(item.get("removed_count") or 0)
    return total
