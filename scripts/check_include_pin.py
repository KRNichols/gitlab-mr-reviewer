#!/usr/bin/env python3
"""Fail when the include clone SHA is not this shipped commit."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_RELS = (".gitlab-ci-include.yml", "templates/review.yml")
FETCH_PIN_RE = re.compile(
    r"git -C \.gitlab-mr-reviewer fetch --depth 1 origin ([0-9a-f]{40})"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN = (
    "${GITLAB_REVIEWER_REF}",
    "${GITLAB_REVIEWER_REPO}",
    "$[[ inputs.reviewer_ref ]]",
    "$[[ inputs.reviewer_repo ]]",
)


def include_paths(root=None):
    """
    What: Resolve the two consumer include files under a repository root.
    Why: The remote include and the CI component must ship the same clone SHA.
    Who: check_includes and write_include_pins.
    Where: .gitlab-ci-include.yml and templates/review.yml.
    How: Join each relative name onto root, defaulting to this repository.
    """
    base = Path(root) if root is not None else ROOT
    return [base / rel for rel in INCLUDE_RELS]


def extract_clone_pin(text):
    """
    What: Read the 40-hex SHA from a git fetch origin line in an include file.
    Why: Consumers clone that SHA; a missing or variable pin is hole E or C.
    Who: read_include_pins and write_include_pins.
    Where: The fetch --depth 1 origin line in each include YAML file.
    How: Search the fetch line first; return None when no full SHA is present.
    """
    if not text:
        return None
    match = FETCH_PIN_RE.search(text)
    return match.group(1) if match else None


def read_include_pins(root=None):
    """
    What: Collect the clone SHA from each include file plus the raw text.
    Why: Both files must agree, and forbidden clone overrides must be scanned.
    Who: check_includes when it grades the working tree.
    Where: The two paths from include_paths.
    How: Read UTF-8 text and pair each path with extract_clone_pin output.
    """
    rows = []
    for path in include_paths(root):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        rows.append((path, text, extract_clone_pin(text)))
    return rows


def git_rev_parse(root, spec="HEAD"):
    """
    What: Resolve a git revision to a 40-character SHA, or None on failure.
    Why: The include pin must be compared to HEAD of this checkout.
    Who: check_includes and write_include_pins.
    Where: The repository at root, spec HEAD or HEAD^.
    How: Run git rev-parse and accept only a full lowercase hex object name.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", spec],
            cwd=str(root or ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha if SHA_RE.fullmatch(sha) else None


def commit_paths(root, spec="HEAD"):
    """
    What: List paths changed by one commit relative to its first parent.
    Why: A pin-only follow-up may name HEAD^ when HEAD only edits include files.
    Who: check_includes after it reads HEAD and HEAD^.
    Where: git diff-tree on the named spec.
    How: Return non-empty name-only paths, or an empty list when git fails.
    """
    try:
        proc = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", spec],
            cwd=str(root or ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def is_ancestor(root, pin, head):
    """
    What: True when pin is git-ancestor of head in this repository.
    Why: Helper-only commits must not start a pin rewrite loop against HEAD.
    Who: check_includes when HEAD did not edit the include files.
    Where: git merge-base --is-ancestor pin head at root.
    How: Run merge-base and treat a zero status as ancestry.
    """
    if not pin or not head:
        return False
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", pin, head],
            cwd=str(root or ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def include_rels_changed(root, spec="HEAD"):
    """
    What: True when this commit edits either consumer include file.
    Why: Strict pin==HEAD applies to pin rewrites, not helper-only follow-ups.
    Who: check_includes before it chooses the ancestry fallback.
    Where: The two INCLUDE_RELS paths in commit_paths output.
    How: Intersect changed names with the include relative path set.
    """
    changed = set(commit_paths(root, spec))
    return bool(changed & set(INCLUDE_RELS))


def pin_equals_head(pin, head, parent=None, parent_changed=None):
    """
    What: True when the include pin is HEAD, or HEAD is the include-only pin bump.
    Why: A commit cannot contain its own SHA, so the shipping helper is HEAD or HEAD^.
    Who: check_includes and the pin unit tests.
    Where: Compared SHAs from the include files and git rev-parse.
    How: Accept pin==HEAD; else pin==parent only when HEAD touches include files.
    """
    if not pin or not head or not SHA_RE.fullmatch(pin) or not SHA_RE.fullmatch(head):
        return False
    if pin == head:
        return True
    if not parent or pin != parent or not SHA_RE.fullmatch(parent):
        return False
    changed = set(parent_changed or [])
    return bool(changed) and changed <= set(INCLUDE_RELS)


def write_include_pins(root, sha):
    """
    What: Rewrite both include fetch lines to clone the given 40-hex SHA.
    Why: make pin must bump the remote include and the component together.
    Who: main when invoked with --write.
    Where: The fetch origin line in each include YAML file.
    How: Substitute the existing fetch SHA, or refuse when the line is missing.
    """
    if not SHA_RE.fullmatch(str(sha or "")):
        raise ValueError("pin must be a 40-character lowercase SHA")
    for path in include_paths(root):
        text = path.read_text(encoding="utf-8")
        if extract_clone_pin(text) is None:
            raise ValueError("missing fetch pin in %s" % path)
        updated = FETCH_PIN_RE.sub(
            "git -C .gitlab-mr-reviewer fetch --depth 1 origin " + sha,
            text,
            count=1,
        )
        path.write_text(updated, encoding="utf-8")


def check_includes(root=None):
    """
    What: Return failure strings when include pins are stale, split, or variable.
    Why: Consumers still running db45b6d would miss later red-team closures.
    Who: main and the unittest that grades this repository.
    Where: Include files versus git rev-parse HEAD in root.
    How: Require one shared SHA; strict pin==HEAD only when include files change.
    """
    base = Path(root) if root is not None else ROOT
    failures = []
    rows = read_include_pins(base)
    pins = []
    for path, text, pin in rows:
        rel = path.name if path.parent == base else path.relative_to(base).as_posix()
        if not path.is_file():
            failures.append("%s: missing include file" % rel)
            continue
        for token in FORBIDDEN:
            if token in text:
                failures.append("%s: uses %s" % (rel, token))
        if pin is None:
            failures.append("%s: missing 40-hex clone pin" % rel)
        pins.append(pin)
    unique = {item for item in pins if item}
    if len(unique) != 1:
        failures.append("include pins differ or are missing")
        return failures
    pin = unique.pop()
    head = git_rev_parse(base, "HEAD")
    parent = git_rev_parse(base, "HEAD^")
    changed = commit_paths(base, "HEAD") if parent else []
    if include_rels_changed(base):
        if not pin_equals_head(pin, head, parent, changed):
            failures.append("include pin %s != HEAD %s" % (pin, head or "(unknown)"))
    elif head and pin != head and not is_ancestor(base, pin, head):
        failures.append("include pin %s is not an ancestor of HEAD %s" % (pin, head))
    return failures


def main(argv=None):
    """
    What: Process-exit wrapper to check or rewrite the include clone SHA.
    Why: make ci must fail closed on a stale pin; make pin rewrites both files.
    Who: Makefile include-pin and pin targets.
    Where: scripts/check_include_pin.py invoked with python3.
    How: --write uses HEAD; otherwise print each failure and return status 1.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--write"]:
        sha = git_rev_parse(ROOT, "HEAD")
        if not sha:
            print("include pin write failed: cannot resolve HEAD", file=sys.stderr)
            return 1
        write_include_pins(ROOT, sha)
        print("include pin written:", sha)
        return 0
    if args:
        print("usage: check_include_pin.py [--write]", file=sys.stderr)
        return 2
    failures = check_includes(ROOT)
    if failures:
        print("include pin check failed:")
        for item in failures:
            print(item)
        return 1
    print("include pin check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
