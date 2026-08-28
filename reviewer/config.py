"""Load reviewer.json from the protected ref; harden working-tree overlays."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

DEFAULTS = {
    "blocking_jobs": [
        "backend",
        "frontend",
        "quality",
        "build",
        "security:node",
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
    "require_blocking_jobs": True,
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
SOFT_KEYS = (
    "non_blocking_jobs",
    "huge_diff_lines",
    "coverage_min",
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


def parse_policy_text(text):
    """
    What: Parse reviewer.json bytes or text into a dict, or None on junk.
    Why: git show and working-tree reads must fail closed on invalid JSON.
    Who: load_config after it fetches a trusted blob or a checkout file.
    Where: reviewer.json contents from a ref or the working tree.
    How: json.loads; return None when the payload is not an object.
    """
    if text is None:
        return None
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def trusted_ref_specs(env=None, filename="reviewer.json"):
    """
    What: git show specs for a named blob on the target or default branch name.
    Why: reviewer.json and approved-packages.json must use the same protected refs.
    Who: read_trusted_policy and read_trusted_allowlist when they walk candidates.
    Where: origin/target and origin/default names. Not TARGET_BRANCH_SHA.
    How: Build origin/name then bare name specs and skip empty branch names.
    """
    data = os.environ if env is None else env
    name = str(filename or "reviewer.json").strip() or "reviewer.json"
    specs = []
    target = str(data.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME") or "").strip()
    default = str(data.get("CI_DEFAULT_BRANCH") or "").strip()
    for branch in (target, default):
        if not branch:
            continue
        specs.append(f"origin/{branch}:{name}")
        specs.append(f"{branch}:{name}")
    return specs


def git_show_text(root, spec):
    """
    What: Return `git show spec` stdout from root, or None when git fails.
    Why: Trusted policy must come from the protected ref object, not HEAD.
    Who: read_trusted_policy when no show_fn is injected.
    Where: Consumer CI_PROJECT_DIR, spec like origin/main:reviewer.json.
    How: subprocess git show; treat a non-zero status as a missing blob.
    """
    try:
        proc = subprocess.run(
            ["git", "show", spec],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def read_trusted_policy(root, env=None, show_fn=None):
    """
    What: Load reviewer.json from the first git-show spec that returns an object.
    Why: Default-branch policy is the only file an MR is allowed to inherit fully.
    Who: load_config before it considers the working-tree copy.
    Where: Target SHA or default branch in the consumer repository.
    How: Walk trusted_ref_specs; parse the first blob; skip empties and junk.
    """
    reader = show_fn or (lambda spec: git_show_text(root, spec))
    for spec in trusted_ref_specs(env):
        parsed = parse_policy_text(reader(spec))
        if parsed is not None:
            return parsed
    return None


def _apply_severities_no_downgrade(cfg, incoming):
    """
    What: Merge severity ranks without turning a blocker into a warn.
    Why: Neither checkout nor a job-overridable trusted blob may weaken secret.
    Who: _merge_policy for both trusted and untrusted overlays.
    Where: cfg['severities'] after DEFAULTS are copied.
    How: Skip a non-blocker incoming rank when the current rank is already blocker.
    """
    if not isinstance(incoming, dict):
        return
    ranks = cfg.setdefault("severities", {})
    for rule, sev in incoming.items():
        rank = str(sev or "").strip().lower()
        current = str(ranks.get(rule) or "").strip().lower()
        if current == "blocker" and rank != "blocker":
            continue
        if rank == "blocker":
            ranks[rule] = "blocker"
        elif rank == "warn" and current != "blocker":
            ranks[rule] = "warn"


def _union_path_rules(existing, incoming):
    """
    What: Append new path_rules dicts onto the current list, or keep current.
    Why: An MR must not replace or empty consumer path hooks from HEAD.
    Who: _merge_policy for both trusted and untrusted overlays.
    Where: cfg['path_rules'] during load_config.
    How: Keep existing dicts; add incoming dicts whose JSON form is not present.
    """
    merged = [item for item in (existing or []) if isinstance(item, dict)]
    if not isinstance(incoming, list) or not incoming:
        return merged
    seen = {json.dumps(item, sort_keys=True) for item in merged}
    for rule in incoming:
        if not isinstance(rule, dict):
            continue
        key = json.dumps(rule, sort_keys=True)
        if key in seen:
            continue
        merged.append(rule)
        seen.add(key)
    return merged


def _union_str_list(existing, incoming):
    """
    What: Append new non-empty names onto an existing list, or keep existing.
    Why: An empty incoming list must not wipe blockers or pin manifests.
    Who: _merge_policy when it merges blocking_jobs and pin_files.
    Where: In-memory cfg lists during load_config.
    How: Copy existing names, then add stripped incoming names that are new.
    """
    merged = [str(item) for item in (existing or [])]
    if not isinstance(incoming, list) or not incoming:
        return merged
    for name in incoming:
        text = str(name or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _merge_alias_additions(cfg, incoming):
    """
    What: Add new job alias keys that do not map a dummy name onto a blocker.
    Why: dummy→backend would let a no-op job satisfy a required product gate.
    Who: _merge_policy when the blob is a protected-ref file.
    Where: cfg['job_aliases'] after DEFAULTS (and any earlier merge).
    How: Keep existing keys; add a new key only when its target is not a blocker.
    """
    if not isinstance(incoming, dict):
        return
    blockers = {str(item) for item in (cfg.get("blocking_jobs") or [])}
    table = dict(cfg.get("job_aliases") or {})
    for raw, dest in incoming.items():
        src = str(raw or "").strip()
        dst = str(dest or "").strip()
        if not src or not dst or src in table:
            continue
        if dst in blockers:
            continue
        table[src] = dst
    cfg["job_aliases"] = table


def _merge_policy(cfg, loaded, allow_aliases):
    """
    What: Apply one reviewer.json overlay with DEFAULTS-hardening on both paths.
    Why: Trusted and untrusted blobs share the no-downgrade / no-empty rules.
    Who: apply_trusted_policy and apply_untrusted_policy.
    Where: In-memory cfg inside load_config.
    How: Union jobs, pins, and path_rules, then copy only remaining soft keys.
    """
    if not isinstance(loaded, dict):
        return
    _apply_severities_no_downgrade(cfg, loaded.get("severities") or {})
    cfg["blocking_jobs"] = _union_str_list(cfg.get("blocking_jobs"), loaded.get("blocking_jobs"))
    cfg["pin_files"] = _union_str_list(cfg.get("pin_files"), loaded.get("pin_files"))
    if loaded.get("require_blocking_jobs") in {True, "true", "1", "yes", "on"}:
        cfg["require_blocking_jobs"] = True
    if loaded.get("pip_audit_blocks") in {True, "true", "1", "yes", "on"}:
        cfg["pip_audit_blocks"] = True
    if allow_aliases:
        _merge_alias_additions(cfg, loaded.get("job_aliases"))
    cfg["path_rules"] = _union_path_rules(cfg.get("path_rules"), loaded.get("path_rules"))
    for key in SOFT_KEYS:
        if key in loaded:
            cfg[key] = loaded[key]


def apply_trusted_policy(cfg, loaded):
    """
    What: Overlay a default-branch reviewer.json without weakening shipped defaults.
    Why: A job-overridable trusted ref must not downgrade secret or empty blockers.
    Who: load_config after read_trusted_policy succeeds.
    Where: In-memory cfg before the working-tree harden pass.
    How: Reuse the same no-downgrade merge as untrusted, including pin union.
    """
    _merge_policy(cfg, loaded, allow_aliases=True)


def apply_untrusted_policy(cfg, loaded):
    """
    What: Overlay a working-tree reviewer.json without weakening blockers.
    Why: An MR can add rules but must not remap jobs, empty pins, or drop blockers.
    Who: load_config when HEAD reviewer.json differs from the protected blob.
    Where: The MR checkout file only; never the trusted ref.
    How: Ignore aliases, union pins and path_rules, and refuse job weakening.
    """
    _merge_policy(cfg, loaded, allow_aliases=False)


def load_config(env=None, root=None, show_fn=None):
    """
    What: Merge shipped defaults, protected-ref policy, then a hardened checkout file.
    Why: Include consumers need knobs, but an MR-tree reviewer.json must not weaken gates.
    Who: run_review and the unit tests that inject show_fn or a tiny env mapping.
    Where: git show of the target ref, then reviewer.json at the project root.
    How: Apply both blobs with no severity downgrade and no emptied blockers.
    """
    data = os.environ if env is None else env
    cfg = _copy_defaults()
    base = Path(root) if root is not None else project_dir(data)
    mr = bool(str(data.get("CI_MERGE_REQUEST_IID") or "").strip())
    trusted = read_trusted_policy(base, data, show_fn=show_fn)
    if trusted is not None:
        apply_trusted_policy(cfg, trusted)
    chosen = str(data.get("REVIEW_CONFIG") or "").strip()
    if mr:
        tree_path = base / "reviewer.json"
    elif chosen:
        tree_path = Path(chosen)
    else:
        tree_path = base / "reviewer.json"
    if tree_path.is_file():
        apply_untrusted_policy(cfg, parse_policy_text(tree_path.read_text(encoding="utf-8")))
    blocking = str(data.get("REVIEW_BLOCKING_JOBS") or "").strip()
    if blocking:
        extra = [item.strip() for item in blocking.split(",") if item.strip()]
        if extra:
            merged = list(cfg.get("blocking_jobs") or [])
            for name in extra:
                if name not in merged:
                    merged.append(name)
            cfg["blocking_jobs"] = merged
    if "REVIEW_PIP_AUDIT_BLOCKS" in data and _truthy(data.get("REVIEW_PIP_AUDIT_BLOCKS")):
        cfg["pip_audit_blocks"] = True
    if "REVIEW_REQUIRE_JOBS" in data:
        flag = _truthy(data.get("REVIEW_REQUIRE_JOBS"))
        if flag or not mr:
            cfg["require_blocking_jobs"] = flag
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
