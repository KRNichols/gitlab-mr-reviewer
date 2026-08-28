"""Load reviewer.json defaults plus environment overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "blocking_jobs": [
        "backend",
        "frontend",
        "quality",
        "build",
        "security:node",
        "test",
        "lint",
    ],
    "non_blocking_jobs": ["security:pip", "pip-audit"],
    "job_aliases": {
        "security-node": "security:node",
        "security_node": "security:node",
        "node-audit": "security:node",
        "security-pip": "security:pip",
        "security_pip": "security:pip",
        "pip-audit": "security:pip",
    },
    "require_blocking_jobs": False,
    "pip_audit_blocks": False,
    "huge_diff_lines": 800,
    "coverage_min": 0,
    "pin_files": [
        "requirements.txt",
        "requirements-dev.txt",
        "backend/requirements.txt",
        "backend/requirements-dev.txt",
        "package.json",
        "frontend/package.json",
        "approved-packages.json",
    ],
    "path_rules": [],
    "severities": {
        "pin-range": "blocker",
        "pin-allowlist": "blocker",
        "five-part": "blocker",
        "secret": "blocker",
        "empty-description": "blocker",
        "huge-diff": "warn",
        "missing-test": "warn",
        "junit": "blocker",
        "npm-audit": "blocker",
        "pip-audit": "warn",
        "coverage": "warn",
        "path-rule": "blocker",
        "changes-fetch": "blocker",
        "approvals-fetch": "blocker",
    },
}

SUMMARY_MARKER = "<!-- gitlab-mr-reviewer:summary -->"
INLINE_PREFIX = "<!-- gitlab-mr-reviewer:inline:"
DISCLAIMER = (
    "This bot grades pins, five-part comments, pipeline jobs, and artifacts. "
    "It is not a secret review and not an ATO. A red pip-audit job that does "
    "not block Approve is not a secrets-OK."
)


def _truthy(value):
    """
    What: True when an environment string is a common yes token.
    Why: CI flags arrive as 1/true/yes and must not be compared loosely.
    Who: load_config when it reads REVIEW_* overrides.
    Where: REVIEW_DRY_RUN, REVIEW_PIP_AUDIT_BLOCKS, REVIEW_REQUIRE_JOBS.
    How: Strip, lowercase, and match the small truthy set.
    """
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _copy_defaults():
    """
    What: Deep-ish copy of DEFAULTS so callers cannot mutate the module map.
    Why: Tests load config many times and must not leak writes across cases.
    Who: load_config at the start of every merge.
    Where: In-memory dict before reviewer.json is applied.
    How: json round-trip the DEFAULTS object.
    """
    return json.loads(json.dumps(DEFAULTS))


def project_dir(env=None):
    """
    What: Consumer checkout path the scanners should treat as the project root.
    Why: An include clone lives beside the product tree, not inside it.
    Who: load_config, local diff, allowlist, and missing-test checks.
    Where: REVIEW_PROJECT_DIR, then CI_PROJECT_DIR, then this repository root.
    How: Prefer the override, else the GitLab checkout, else parents of reviewer/.
    """
    data = os.environ if env is None else env
    explicit = str(data.get("REVIEW_PROJECT_DIR") or data.get("CI_PROJECT_DIR") or "").strip()
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parents[1]


def load_config(env=None, root=None):
    """
    What: Merge shipped defaults, optional reviewer.json, and env overrides.
    Why: Include consumers need job-name knobs without forking the helper.
    Who: run_review and the unit tests that inject a tiny env mapping.
    Where: reviewer.json at the project root plus REVIEW_* variables.
    How: Copy defaults, update from JSON when present, then apply env flags.
    """
    data = os.environ if env is None else env
    cfg = _copy_defaults()
    base = Path(root) if root is not None else project_dir(data)
    chosen = str(data.get("REVIEW_CONFIG") or "").strip()
    path = Path(chosen) if chosen else base / "reviewer.json"
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                if key == "severities" and isinstance(value, dict):
                    cfg["severities"].update(value)
                else:
                    cfg[key] = value
    blocking = str(data.get("REVIEW_BLOCKING_JOBS") or "").strip()
    if blocking:
        cfg["blocking_jobs"] = [item.strip() for item in blocking.split(",") if item.strip()]
    if "REVIEW_PIP_AUDIT_BLOCKS" in data:
        cfg["pip_audit_blocks"] = _truthy(data.get("REVIEW_PIP_AUDIT_BLOCKS"))
    if "REVIEW_REQUIRE_JOBS" in data:
        cfg["require_blocking_jobs"] = _truthy(data.get("REVIEW_REQUIRE_JOBS"))
    huge = str(data.get("REVIEW_HUGE_DIFF_LINES") or "").strip()
    if huge.isdigit():
        cfg["huge_diff_lines"] = int(huge)
    return cfg


def severity_for(cfg, rule, default="blocker"):
    """
    What: Look up blocker-versus-warn for one rule name.
    Why: Operators can downgrade huge-diff without touching pin-range.
    Who: scan_diff and artifact parsers when they construct a Finding.
    Where: cfg['severities'] after load_config.
    How: Return the mapped value when present, else the supplied default.
    """
    ranks = (cfg or {}).get("severities") or {}
    value = str(ranks.get(rule) or default).strip().lower()
    return "warn" if value == "warn" else "blocker"
