"""Idempotent summary note and inline diff discussions."""

from __future__ import annotations

import re

from reviewer.config import DISCLAIMER, INLINE_PREFIX, SUMMARY_MARKER
from reviewer.findings import blockers_of, warns_of
from reviewer.http import is_2xx

INLINE_RE = re.compile(re.escape(INLINE_PREFIX) + r"([a-f0-9]+) -->")
AT_RE = re.compile(r"@+")


def escape_note_text(text):
    """
    What: Strip at-signs and break Markdown fences in untrusted reviewer text.
    Why: Findings and job lines must not ping users or break the note fence.
    Who: fence_block and build_note.
    Where: Every operator-visible string that came from jobs or scanners.
    How: Replace @, replace triple backticks, and drop control characters.
    """
    cleaned = AT_RE.sub("", str(text or ""))
    cleaned = cleaned.replace("```", "'''")
    return "".join(ch for ch in cleaned if ch >= " " or ch in "\n\t")


def fence_block(lines):
    """
    What: Render a list of lines inside a Markdown code fence.
    Why: Job rows and findings must not be interpreted as Markdown mentions.
    Who: build_note for the Pipeline and Findings sections.
    Where: The idempotent summary note.
    How: Escape each line and wrap the block in triple backticks.
    """
    rows = [escape_note_text(item) for item in (lines or []) if str(item).strip()]
    if not rows:
        rows = ["(none)"]
    return "```\n" + "\n".join(rows) + "\n```"


def action_text(verdict):
    """
    What: One-line action sentence for the MR note.
    Why: The Action section should say what the bot did, not only the verdict word.
    Who: build_note from run_review.
    Where: The ### Action block.
    How: Map approve/hold/unapprove/dry-run onto a fixed sentence.
    """
    if verdict == "approve":
        return "Updating one summary note and Approving this merge request."
    if verdict == "unapprove":
        return "Updating one summary note and unapproving because a later push failed a product gate."
    if verdict == "hold":
        return "Holding. Not approving this merge request. Unapproving if a prior Approve exists."
    if verdict == "dry-run":
        return "Would not call the GitLab API. Local dry-run only."
    if verdict == "missing-token":
        return "Stopping. Missing GITLAB_REVIEWER_TOKEN; never faking an Approve."
    return "Stopping. Reviewer setup or HTTP failed closed; never faking an Approve."


def build_note(verdict, pipeline_lines, findings, action):
    """
    What: Markdown MR note with verdict, fenced pipeline, fenced findings, disclaimer.
    Why: Operators should see one note, not a thread of status chatter or raw diffs.
    Who: run_review when it prints a dry-run or upserts /notes.
    Where: The GitLab merge request discussion, marked for in-place updates.
    How: Render the marker, sections, escaped fences, and the pins/jobs disclaimer.
    """
    findings = findings or []
    blocker_lines = [item.as_line() for item in blockers_of(findings)]
    warn_lines = [item.as_line() for item in warns_of(findings)]
    if not blocker_lines and not warn_lines:
        finding_block = fence_block(["none"])
    else:
        chunks = []
        if blocker_lines:
            chunks.append("blockers:")
            chunks.extend(blocker_lines)
        if warn_lines:
            chunks.append("warnings:")
            chunks.extend(warn_lines)
        finding_block = fence_block(chunks)
    return "\n".join(
        [
            SUMMARY_MARKER,
            "## Reviewer bot",
            f"**Verdict:** {escape_note_text(verdict)}",
            "",
            "### Pipeline",
            fence_block(pipeline_lines),
            "",
            "### Findings",
            finding_block,
            "",
            "### Action",
            escape_note_text(action),
            "",
            "### Scope",
            DISCLAIMER,
        ]
    )


def find_summary_note(notes):
    """
    What: First existing MR note that already carries the summary marker.
    Why: The next pipeline must PUT that note instead of POSTing a second one.
    Who: upsert_summary_note after GET /notes.
    Where: Note bodies that start with or contain SUMMARY_MARKER.
    How: Skip system notes; return the first matching dict or None.
    """
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        if note.get("system"):
            continue
        body = str(note.get("body") or "")
        if SUMMARY_MARKER in body:
            return note
    return None


def upsert_summary_note(request, mr_root, token, note, existing_notes=None):
    """
    What: Update the marked summary note in place, or create it once.
    Why: A required reviewer must not spam a new note on every pipeline.
    Who: run_review after it builds the note body.
    Where: PUT /notes/:id when found, else POST /notes.
    How: Fail closed on non-2xx; return (ok, status, method).
    """
    found = find_summary_note(existing_notes)
    if found and found.get("id") is not None:
        url = f"{mr_root}/notes/{found['id']}"
        status, _payload = request("PUT", url, token, {"body": note})
        return is_2xx(status), status, "PUT"
    status, _payload = request("POST", f"{mr_root}/notes", token, {"body": note})
    return is_2xx(status), status, "POST"


def inline_body(finding):
    """
    What: Discussion body for one blocker, with fingerprint and no raw snippet.
    Why: Inline threads must be idempotent and must not reprint added lines.
    Who: upsert_inline_discussions when it POSTs or PUTs a diff note.
    Where: GitLab discussions API body field.
    How: HTML comment fingerprint plus escaped rule and message.
    """
    fp = finding.fingerprint()
    text = escape_note_text(f"{finding.rule}: {finding.message}")
    return f"{INLINE_PREFIX}{fp} -->\n**{finding.severity}** {text}"


def _fingerprint_of(body):
    """
    What: Read the inline fingerprint from a discussion note body.
    Why: Existing bot threads are updated or resolved by that hash.
    Who: upsert_inline_discussions when it walks GET /discussions.
    Where: The HTML comment prefix on bot-authored inline notes.
    How: Regex the prefix and return the hex group, or empty.
    """
    match = INLINE_RE.search(str(body or ""))
    return match.group(1) if match else ""


def upsert_inline_discussions(request, mr_root, token, findings, diff_refs, discussions=None):
    """
    What: Open or update blocker discussions on exact new-file diff lines.
    Why: A required reviewer must pin blockers to the line, not only a summary.
    Who: run_review after the summary note, before Approve.
    Where: POST /discussions with position, PUT existing notes, resolve stale.
    How: Skip warns and lineless items; require SHAs; fail closed on write errors.
    """
    refs = diff_refs or {}
    if not (refs.get("base_sha") and refs.get("start_sha") and refs.get("head_sha")):
        return True, 200
    wanted = {}
    for item in blockers_of(findings):
        if item.path and item.line is not None:
            wanted[item.fingerprint()] = item
    existing = {}
    for thread in discussions or []:
        if not isinstance(thread, dict):
            continue
        notes = thread.get("notes") or []
        if not notes:
            continue
        first = notes[0] if isinstance(notes[0], dict) else {}
        fp = _fingerprint_of(first.get("body"))
        if fp:
            existing[fp] = thread
    for fp, finding in wanted.items():
        body = inline_body(finding)
        if fp in existing:
            thread = existing[fp]
            notes = thread.get("notes") or []
            note_id = (notes[0] or {}).get("id") if notes else None
            disc_id = thread.get("id")
            if disc_id is None or note_id is None:
                continue
            if str((notes[0] or {}).get("body") or "") == body:
                continue
            url = f"{mr_root}/discussions/{disc_id}/notes/{note_id}"
            status, _payload = request("PUT", url, token, {"body": body})
            if not is_2xx(status):
                return False, status
            continue
        position = {
            "base_sha": refs["base_sha"],
            "start_sha": refs["start_sha"],
            "head_sha": refs["head_sha"],
            "position_type": "text",
            "new_path": finding.path,
            "old_path": finding.old_path or finding.path,
            "new_line": int(finding.line),
        }
        status, _payload = request(
            "POST",
            f"{mr_root}/discussions",
            token,
            {"body": body, "position": position},
        )
        if not is_2xx(status):
            return False, status
    for fp, thread in existing.items():
        if fp in wanted:
            continue
        disc_id = thread.get("id")
        if disc_id is None:
            continue
        status, _payload = request(
            "PUT",
            f"{mr_root}/discussions/{disc_id}",
            token,
            {"resolved": True},
        )
        if status and not is_2xx(status) and status != 404:
            return False, status
    return True, 200
