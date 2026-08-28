"""Orchestrate dry-run, note, inline threads, Approve, and unapprove."""

from __future__ import annotations

import os
import subprocess
import sys

from reviewer.artifacts import consume_artifacts
from reviewer.config import DISCLAIMER, load_config, project_dir
from reviewer.diff import diff_refs_of, parse_changes, parse_diff
from reviewer.findings import Finding, blockers_of
from reviewer.http import (
    debug_trace_on,
    derive_api_root,
    gitlab_curl,
    is_2xx,
    project_base,
)
from reviewer.jobs import evaluate_jobs
from reviewer.notes import action_text, build_note, upsert_inline_discussions, upsert_summary_note
from reviewer.pins import load_allowlist
from reviewer.scan import scan_diff, scan_meta

VERDICT_LABELS = {
    "dry-run": "DRY-RUN",
    "missing-token": "MISSING-TOKEN",
    "approve": "APPROVE",
    "hold": "HOLD",
    "unapprove": "UNAPPROVE",
    "setup": "SETUP",
}


def is_mr_context(env=None):
    """
    What: True when GitLab has attached a merge request IID.
    Why: The bot only talks to the API on a real MR pipeline.
    Who: is_dry_run, decide, and run_review.
    Where: CI_MERGE_REQUEST_IID on the hosted review job.
    How: Read the env mapping and treat any non-empty IID as MR context.
    """
    data = os.environ if env is None else env
    return bool(str(data.get("CI_MERGE_REQUEST_IID") or "").strip())


def is_dry_run(env=None):
    """
    What: True for a laptop run or when REVIEW_DRY_RUN is an explicit yes.
    Why: Hosted Approve must never fire from a local checkout by accident.
    Who: run_review before it chooses the API path.
    Where: REVIEW_DRY_RUN plus the merge-request IID check.
    How: Treat 1/true/yes as dry-run, and also dry-run when this is not an MR.
    """
    data = os.environ if env is None else env
    flag = str(data.get("REVIEW_DRY_RUN") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return not is_mr_context(data)


def is_same_project_mr(env=None):
    """
    What: True when the MR source project is this CI project.
    Why: Fork pipelines must not receive the token or an Approve.
    Who: run_review before any live GitLab write.
    Where: CI_PROJECT_ID versus CI_MERGE_REQUEST_SOURCE_PROJECT_ID.
    How: Require both ids and equality; also match the target id when present.
    """
    data = os.environ if env is None else env
    project = str(data.get("CI_PROJECT_ID") or "").strip()
    source = str(data.get("CI_MERGE_REQUEST_SOURCE_PROJECT_ID") or "").strip()
    target = str(data.get("CI_MERGE_REQUEST_PROJECT_ID") or "").strip()
    if not project or not source:
        return False
    if source != project:
        return False
    if target and target != project:
        return False
    return True


def setup_steps():
    """
    What: Setup lines a missing or unsafe token must print.
    Why: A red review job should teach the operator instead of faking Approve.
    Who: run_review when an MR has no GITLAB_REVIEWER_TOKEN.
    Where: stderr on the hosted review job.
    How: Name shortest-lived Developer token, masked+protected, same-project only.
    """
    return "\n".join(
        [
            "GitLab MR reviewer setup failed closed. It never fakes an Approve.",
            "1. Create a shortest-lived GitLab Project Access Token on THIS project only.",
            "2. Role Developer only (not Maintainer). Scope: api. Expiration as short as your rotation allows.",
            "3. Add CI/CD variable GITLAB_REVIEWER_TOKEN: masked AND protected.",
            "4. Uncheck availability to fork pipelines / merge requests from forks.",
            "5. Add that project-bot user as a required MR reviewer under Settings → Merge requests.",
            "6. This bot reviews same-project MRs only (source project id == CI_PROJECT_ID).",
            "A missing GITLAB_REVIEWER_TOKEN fails this job. Do not invent a token or skip the Approve call.",
        ]
    )


def decide(is_mr, token, jobs_ok, findings, was_approved, changes_ok=True):
    """
    What: Pick dry-run, missing-token, approve, unapprove, or hold.
    Why: The note and the API call must share one verdict word.
    Who: run_review after jobs, findings, and the changes fetch are known.
    Where: Token presence, job ok, blocker list, prior approval, changes_ok.
    How: Missing token first, then dry-run, refuse approve without changes, then approve, then unapprove.
    """
    if is_mr and not str(token or "").strip():
        return "missing-token"
    if not is_mr:
        return "dry-run"
    blockers = blockers_of(findings)
    can_approve = bool(jobs_ok and not blockers and changes_ok)
    if can_approve:
        return "approve"
    if was_approved:
        return "unapprove"
    return "hold"


def local_diff(root):
    """
    What: Working-tree plus index unified diff for a laptop dry-run.
    Why: make review on a checkout has no GitLab changes endpoint.
    Who: run_review on the dry-run path.
    Where: git diff HEAD and git diff --cached from the project root.
    How: Concatenate both command outputs; empty string when git is missing.
    """
    chunks = []
    for args in (["git", "diff", "HEAD"], ["git", "diff", "--cached"]):
        try:
            proc = subprocess.run(
                args,
                cwd=str(root),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            continue
        chunks.append(proc.stdout or "")
    return "".join(chunks)


def jobs_payload(payload):
    """
    What: Normalize a jobs response to a list of {name, status} dicts.
    Why: Tests return a bare list; GitLab may wrap the same rows.
    Who: run_review after GET /pipelines/:id/jobs.
    Where: The JSON array or a dict with a jobs key.
    How: Prefer a list payload, else payload.jobs, else an empty list.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get("jobs") or payload.get("data") or []
        if isinstance(rows, list):
            return rows
    return []


def notes_payload(payload):
    """
    What: Normalize a notes or discussions response to a list.
    Why: GitLab returns a list; tests may wrap it.
    Who: run_review after GET /notes and GET /discussions.
    Where: The JSON array or a dict with notes/discussions.
    How: Prefer a list, else the first list-valued notes or discussions key.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("notes", "discussions", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
    return []


def was_approved_from(payload, fetch_ok):
    """
    What: Prior-approval flag, pessimistic when the approvals GET failed.
    Why: A stale Approve must not survive a later red push or a failed GET.
    Who: run_review after GET /merge_requests/:iid/approvals.
    Where: The GitLab approvals payload or a failed HTTP status.
    How: If fetch failed, return True so decide unapproves; else read approved.
    """
    if not fetch_ok:
        return True
    if not isinstance(payload, dict):
        return False
    if "approved" in payload:
        return bool(payload.get("approved"))
    return bool(payload.get("approved_by") or [])


def set_approval(request, mr_root, token, approve):
    """
    What: POST approve, or POST unapprove on every non-approve live path.
    Why: Status 0 must not look like success; a stale Approve must be cleared.
    Who: run_review after the note and inline threads.
    Where: /merge_requests/:iid/approve and /unapprove.
    How: Require 2xx for approve; for unapprove accept 2xx or 404 only.
    """
    if approve:
        status, _payload = request("POST", f"{mr_root}/approve", token)
        return is_2xx(status), status
    status, _payload = request("POST", f"{mr_root}/unapprove", token)
    if status == 404:
        return True, status
    return is_2xx(status), status


def _print_note(note):
    """
    What: Write the escaped summary note to stdout and nothing else.
    Why: CI logs must not reprint raw added diff lines or secret snippets.
    Who: run_review on both dry-run and live paths.
    Where: Job stdout.
    How: Print the already-escaped note body.
    """
    print(note)


def run_review(env=None, curl_fn=None, download_fn=None, local_diff_fn=None):
    """
    What: Run the reviewer: dry-run locally, or note plus Approve on a GitLab MR.
    Why: make review and the hosted job must share one helper and one verdict.
    Who: scripts/review-mr.sh and the unit tests that inject curl_fn.
    Where: Local git diff, or GitLab changes/jobs/approvals/notes/discussions.
    How: Fail closed on trace, fork, token, HTTP 0, and non-2xx; never fake Approve.
    """
    data = os.environ if env is None else env
    if debug_trace_on(data):
        print(
            "Refusing to run: CI_DEBUG_TRACE is on and would leak GITLAB_REVIEWER_TOKEN.",
            file=sys.stderr,
        )
        return 1
    token = str(data.get("GITLAB_REVIEWER_TOKEN") or "").strip()
    mr = is_mr_context(data)
    dry = is_dry_run(data)
    cfg = load_config(data)
    root = project_dir(data)
    if mr and not dry and not is_same_project_mr(data):
        print(
            "Refusing: this bot reviews same-project merge requests only "
            "(CI_MERGE_REQUEST_SOURCE_PROJECT_ID must equal CI_PROJECT_ID).",
            file=sys.stderr,
        )
        return 1
    if mr and not token and not dry:
        print(setup_steps(), file=sys.stderr)
        return 1
    if dry:
        diff_text = (local_diff_fn or local_diff)(root)
        files = parse_diff(diff_text)
        allow = load_allowlist(root / "approved-packages.json")
        findings = scan_diff(files, allow=allow, cfg=cfg, root=root)
        local_desc = data.get("CI_MERGE_REQUEST_DESCRIPTION")
        if local_desc is None:
            local_desc = "(dry-run)"
        findings.extend(scan_meta(files, local_desc, cfg))
        note = build_note(
            "DRY-RUN",
            ["(dry-run: no pipeline jobs)"],
            findings,
            action_text("dry-run"),
        )
        _print_note(note)
        return 0
    try:
        api_root = derive_api_root(data)
    except ValueError as exc:
        print(f"Refusing unsafe API root: {exc}", file=sys.stderr)
        return 1

    def request(method, url, tok, body=None):
        """
        What: Live HTTP wrapper that pins every call to the derived API root.
        Why: Injected tests share this signature; production always allowlists.
        Who: The rest of run_review after derive_api_root succeeds.
        Where: notes, discussions, jobs, changes, approve, unapprove.
        How: Call curl_fn or gitlab_curl with api_root set.
        """
        caller = curl_fn or gitlab_curl
        try:
            return caller(method, url, tok, body, api_root)
        except TypeError:
            return caller(method, url, tok, body)

    base = project_base(data, api_root)
    from urllib.parse import quote

    iid = quote(str(data.get("CI_MERGE_REQUEST_IID") or ""), safe="")
    pipeline = quote(str(data.get("CI_PIPELINE_ID") or ""), safe="")
    mr_root = f"{base}/merge_requests/{iid}"
    mr_status, mr_payload = request("GET", mr_root, token)
    changes_status, changes_payload = request("GET", f"{mr_root}/changes", token)
    jobs_status, jobs_raw = request(
        "GET",
        f"{base}/pipelines/{pipeline}/jobs?per_page=100",
        token,
    )
    approvals_status, approvals_payload = request("GET", f"{mr_root}/approvals", token)
    notes_status, notes_raw = request("GET", f"{mr_root}/notes?per_page=100", token)
    disc_status, disc_raw = request("GET", f"{mr_root}/discussions?per_page=100", token)
    changes_ok = is_2xx(changes_status)
    jobs_fetch_ok = is_2xx(jobs_status)
    approvals_ok = is_2xx(approvals_status)
    findings = []
    if not changes_ok:
        findings.append(
            Finding(
                "changes-fetch",
                "merge request changes fetch failed; Approve is refused",
                "blocker",
            )
        )
        files = []
        refs = {}
    else:
        files = parse_changes(changes_payload)
        refs = diff_refs_of(changes_payload) or diff_refs_of(mr_payload)
    description = ""
    if is_2xx(mr_status) and isinstance(mr_payload, dict):
        description = mr_payload.get("description") or ""
        if not refs:
            refs = diff_refs_of(mr_payload)
    description = description or data.get("CI_MERGE_REQUEST_DESCRIPTION") or ""
    allow = load_allowlist(root / "approved-packages.json")
    findings.extend(scan_diff(files, allow=allow, cfg=cfg, root=root))
    findings.extend(scan_meta(files, description, cfg))
    jobs = jobs_payload(jobs_raw) if jobs_fetch_ok else []
    if not jobs_fetch_ok:
        findings.append(
            Finding(
                "changes-fetch",
                "pipeline jobs fetch failed; product gates cannot be proven",
                "blocker",
            )
        )
        jobs_ok = False
        job_lines = ["jobs fetch failed"]
    else:
        jobs_ok, job_lines = evaluate_jobs(jobs, cfg)
        findings.extend(
            consume_artifacts(
                jobs,
                token,
                base,
                cfg,
                download_fn=download_fn,
                api_root=api_root,
            )
        )
    was_approved = was_approved_from(approvals_payload, approvals_ok)
    if not approvals_ok:
        findings.append(
            Finding(
                "approvals-fetch",
                "approvals fetch failed; a stale Approve will be cleared",
                "blocker",
            )
        )
    verdict = decide(mr, token, jobs_ok, findings, was_approved, changes_ok=changes_ok)
    note = build_note(
        VERDICT_LABELS.get(verdict, verdict.upper()),
        job_lines,
        findings,
        action_text(verdict),
    )
    existing_notes = notes_payload(notes_raw) if is_2xx(notes_status) else []
    note_ok, note_status, _method = upsert_summary_note(
        request, mr_root, token, note, existing_notes
    )
    if not note_ok:
        print(f"Summary note write failed closed (HTTP {note_status}).", file=sys.stderr)
        un_ok, un_status = set_approval(request, mr_root, token, approve=False)
        if not un_ok:
            print(f"Unapprove failed closed (HTTP {un_status}).", file=sys.stderr)
        return 1
    discussions = notes_payload(disc_raw) if is_2xx(disc_status) else []
    inline_ok, inline_status = upsert_inline_discussions(
        request, mr_root, token, findings, refs, discussions
    )
    if not inline_ok:
        print(f"Inline discussion write failed closed (HTTP {inline_status}).", file=sys.stderr)
        un_ok, un_status = set_approval(request, mr_root, token, approve=False)
        if not un_ok:
            print(f"Unapprove failed closed (HTTP {un_status}).", file=sys.stderr)
        _print_note(note)
        return 1
    if verdict == "approve":
        ok, status = set_approval(request, mr_root, token, approve=True)
        _print_note(note)
        if not ok:
            print(f"Approve failed closed (HTTP {status}).", file=sys.stderr)
            return 1
        return 0
    ok, status = set_approval(request, mr_root, token, approve=False)
    _print_note(note)
    if not ok:
        print(f"Unapprove failed closed (HTTP {status}).", file=sys.stderr)
        return 1
    return 1


def main(argv=None):
    """
    What: CLI entry that honors --dry-run then calls run_review.
    Why: make review and sh scripts/review-mr.sh --dry-run share this process.
    Who: The review-mr.sh wrapper.
    Where: sys.argv, copying os.environ so --dry-run cannot leak into the parent.
    How: Set REVIEW_DRY_RUN=1 when --dry-run is present, then exit with run_review.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    data = dict(os.environ)
    if "--dry-run" in args:
        data["REVIEW_DRY_RUN"] = "1"
    return run_review(data)


if __name__ == "__main__":
    raise SystemExit(main())
