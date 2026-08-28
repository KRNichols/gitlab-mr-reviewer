#!/usr/bin/env python3
"""CLI entry for the GitLab merge-request reviewer helper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reviewer.http import assert_url_allowed, derive_api_root, gitlab_curl, is_2xx
from reviewer.jobs import evaluate_jobs
from reviewer.notes import build_note
from reviewer.review import (
    decide,
    is_dry_run,
    is_mr_context,
    is_same_project_mr,
    main,
    run_review,
    setup_steps,
)
from reviewer.scan import classify_secret, scan_diff
from reviewer.diff import parse_diff

__all__ = [
    "assert_url_allowed",
    "build_note",
    "classify_secret",
    "decide",
    "derive_api_root",
    "evaluate_jobs",
    "gitlab_curl",
    "is_2xx",
    "is_dry_run",
    "is_mr_context",
    "is_same_project_mr",
    "main",
    "parse_diff",
    "run_review",
    "scan_diff",
    "setup_steps",
]


if __name__ == "__main__":
    raise SystemExit(main())
