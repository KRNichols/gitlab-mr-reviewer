"""Finding records the reviewer collects from diffs, jobs, and artifacts."""

from __future__ import annotations

import hashlib


class Finding:
    """
    What: One reviewer finding with severity, rule name, and optional diff line.
    Why: Approve reads blockers only; inline threads need a path and line.
    Who: scan_diff, artifact parsers, evaluate_jobs helpers, and note builders.
    Where: Passed through run_review into the summary note and discussions.
    How: Store fields, hash a fingerprint, and never keep the raw added line.
    """

    def __init__(
        self,
        rule,
        message,
        severity="blocker",
        path="",
        line=None,
        old_path="",
        old_line=None,
    ):
        """
        What: Store one finding without keeping the raw added source line.
        Why: Approve and inline threads need severity and location, not a snippet.
        Who: Every scanner and artifact parser that constructs a Finding.
        Where: In-memory records later rendered into the escaped summary note.
        How: Copy rule, message, severity, and optional path/line onto the instance.
        """
        self.rule = str(rule or "")
        self.message = str(message or "")
        self.severity = "warn" if severity == "warn" else "blocker"
        self.path = str(path or "")
        self.line = line
        self.old_path = str(old_path or "") or self.path
        self.old_line = old_line

    def fingerprint(self):
        """
        What: Short stable hash of rule, path, line, and message.
        Why: Inline discussions update in place instead of opening a new thread.
        Who: upsert_inline_discussions when it matches existing bot threads.
        Where: Embedded in an HTML comment on each inline note body.
        How: SHA-256 the joined fields and keep the first sixteen hex characters.
        """
        raw = f"{self.rule}|{self.path}|{self.line}|{self.message}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def as_line(self):
        """
        What: Single summary line without raw source and without @ mentions.
        Why: The MR note must fence findings and must not ping users.
        Who: build_note when it renders the Findings section.
        Where: The idempotent summary note body.
        How: Join path, rule, and escaped message; drop at-signs.
        """
        loc = self.path or "(repo)"
        if self.line is not None:
            loc = f"{loc}:{self.line}"
        text = f"{loc} [{self.severity}/{self.rule}] {self.message}"
        return text.replace("@", "")


def blockers_of(findings):
    """
    What: Return findings whose severity is blocker.
    Why: Only blockers hold Approve; warns stay visible in the note.
    Who: decide and the inline discussion publisher.
    Where: After scan_diff and artifact parsing in run_review.
    How: Filter the list for severity equal to blocker.
    """
    return [item for item in findings or [] if item.severity == "blocker"]


def warns_of(findings):
    """
    What: Return findings whose severity is warn.
    Why: The summary note still lists warnings after a green Approve.
    Who: build_note when it splits Findings into two fences.
    Where: The Markdown note posted or updated on the merge request.
    How: Filter the list for severity equal to warn.
    """
    return [item for item in findings or [] if item.severity == "warn"]
