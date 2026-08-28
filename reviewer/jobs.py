"""Grade pipeline jobs. pip-audit is non-blocking unless configured."""

from __future__ import annotations

from reviewer.config import DEFAULTS

PASS_STATUSES = {"success", "passed", "ok", "manual"}


def normalize_job_name(raw, aliases):
    """
    What: Map a CI job name through the configured alias table.
    Why: node-audit and security:node must grade as the same gate.
    Who: evaluate_jobs when it indexes the pipeline payload.
    Where: job_aliases in reviewer.json plus shipped defaults.
    How: Strip the raw name and replace it when an alias exists.
    """
    name = str(raw or "").strip()
    table = aliases or {}
    return table.get(name, name)


def evaluate_jobs(jobs, cfg=None):
    """
    What: Grade this pipeline's jobs against the product gates.
    Why: pip-audit may stay red; node-audit and named product jobs may not.
    Who: run_review after it fetches /pipelines/:id/jobs.
    Where: blocking_jobs, non_blocking_jobs, and require_blocking_jobs.
    How: Alias names, require present blockers to pass, and never treat pip as secrets-OK.
    """
    cfg = cfg or DEFAULTS
    aliases = cfg.get("job_aliases") or {}
    blocking = list(cfg.get("blocking_jobs") or [])
    require = bool(cfg.get("require_blocking_jobs"))
    pip_blocks = bool(cfg.get("pip_audit_blocks"))
    seen = {}
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        name = normalize_job_name(job.get("name"), aliases)
        status = str(job.get("status") or "").strip().lower()
        if name:
            seen[name] = status
    lines = []
    ok = True
    present_blockers = 0
    for name in blocking:
        status = seen.get(name)
        if not status:
            if require:
                lines.append(f"{name}: missing")
                ok = False
            else:
                lines.append(f"{name}: absent (optional)")
            continue
        present_blockers += 1
        if status in PASS_STATUSES:
            lines.append(f"{name}: {status}")
        else:
            lines.append(f"{name}: {status} — hold")
            ok = False
    pip_name = None
    for candidate in ("security:pip", "pip-audit"):
        if candidate in seen:
            pip_name = candidate
            break
    if pip_name:
        status = seen[pip_name]
        if pip_blocks and status not in PASS_STATUSES:
            lines.append(f"{pip_name} / pip-audit: {status} — hold (override)")
            ok = False
        else:
            lines.append(
                f"{pip_name} / pip-audit: {status} (does not block; not a secrets-OK)"
            )
    if require and present_blockers == 0 and blocking:
        ok = False
    return ok, lines
