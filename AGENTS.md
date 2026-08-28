# AGENTS.md

Train Codex (or any coding agent) on this repository and on any product
repo that uses `gitlab-mr-reviewer` as a required GitLab merge-request
reviewer.

This file is the readiness contract. If Codex follows it, the GitLab bot
should Approve. If Codex ignores it, the bot should hold.

## What this project is

`gitlab-mr-reviewer` is a **fail-closed GitLab MR policy bot**.

On a GitLab `merge_request_event` pipeline it:

1. Reads the MR diff, this pipeline's jobs, and common artifacts.
2. Updates **one** summary note (idempotent).
3. Opens or updates inline discussions on blocker lines.
4. **Approves** only when every blocking gate is green.
5. **Unapproves** on every non-approve path (404 is fine; other failures
   are not).

It is **not**:

- a secret scanner or an ATO
- a GitHub Actions job that Approves GitHub pull requests
- a style-only linter
- allowed to fake Approve when the API call fails

The hosted runtime is `python3` + `curl` only. No new pip or npm packages.

## Ready-to-merge checklist

Codex must treat an MR as **not ready** until all of these are true.

### Product gates

- Named blocking jobs exist on the pipeline and passed.
  Defaults: `backend`, `frontend`, `quality`, `build`, `security:node`
  (alias `node-audit`). This repo itself blocks on `test`.
- A missing required blocking job is a hold, not a skip.
- `security:pip` / `pip-audit` may stay red. That does **not** block
  Approve and is **not** a secrets-OK. Never tell the user a green path
  that skipped pip-audit is clean for secrets.

### Comments

Every new function, CI job, Make target, or public helper needs a
five-part comment **immediately above it**:

- What
- Why
- Who
- Where
- How

Unlabeled `What:` / `Why:` lines in a paragraph do not count. Use the
explicit field names. `make check_comments` / `make ci` must stay green.

### Pins

- No version ranges (`^`, `~`, `>=`) in pin files
  (`requirements*.txt`, `package.json`, `approved-packages.json`).
- Exact pins only.
- Do not empty `pin_files` or turn the allowlist into allow-all.

### Diff and description

- MR description is not empty.
- Added lines must not look like secrets (`AKIA…`, PEM headers,
  `GITLAB_REVIEWER_TOKEN=`, PAT prefixes). Report type only. Never
  reprint the matching text.
- Huge diffs are a warning, not a silent pass. Split them when you can.
- Changed code without tests is a warning. Add tests unless the change
  is comment-only or pin-only.

### Artifacts when present

- JUnit failures → hold
- npm audit high/critical → hold
- pip-audit findings → warn unless `REVIEW_PIP_AUDIT_BLOCKS=1`

### Runtime / security (do not regress)

- Token never on curl argv. Use a `0600` header file (`-H @file`).
- Refuse `CI_DEBUG_TRACE`.
- HTTP status `0` and every non-2xx fail closed. Never treat them as
  Approve.
- API root from `CI_SERVER_HOST` / `CI_SERVER_FQDN`, else a validated
  `CI_SERVER_URL`. https only. No userinfo. Ignore job-level
  `CI_API_V4_URL`.
- Same-project MRs only. Fork MRs are refused.
- Do not honor job-level `GITLAB_REVIEWER_REPO` or `GITLAB_REVIEWER_REF`.
- Include files ship a 40-character clone SHA. After you change helper
  code, run `make pin` and commit the include follow-up so
  `make include-pin` is green.
- `reviewer.json` is loaded from the **protected target branch**, not
  from the MR HEAD. A checkout may add jobs or upgrade warn → blocker.
  It must not downgrade `secret` / `pin-range` to warn, empty
  `blocking_jobs`, turn off `require_blocking_jobs`, or replace
  `job_aliases`.

## How Codex should work in this repo

1. Read this file, `README.md`, and `reviewer.json` before editing.
2. Prefer the smallest change that preserves fail-closed Approve.
3. Add or update tests under `tests/` for every behavior change.
4. Run:

   ```sh
   make ci
   make review
   ```

   `make review` is a laptop dry-run: one printed note, no GitLab API.
5. If you touch `.gitlab-ci-include.yml` or `templates/review.yml` clone
   SHA lines, run `make pin` and include that follow-up commit.
6. Do not add dependencies. Do not introduce `requests`, `httpx`, or
   other HTTP libraries. Stay on python3 + curl.
7. Do not push to `main`. Open a branch and a pull request.
8. Do not claim the GitLab bot ran because GitHub Actions went green.
   GitHub CI is not this bot.

## How Codex should evaluate *product* code

When the user asks "is this ready?" against an app that includes this
bot, grade the diff the same way the bot will:

1. List changed files.
2. Flag missing five-part comments on new functions / jobs / targets.
3. Flag range pins and secret-like added lines (type only).
4. Flag empty description, missing tests, huge diffs.
5. State which blocking CI jobs must be green on the GitLab MR.
6. End with one of:

   - `READY` — bot should Approve if those jobs pass
   - `HOLD` — bot should unapprove; list blockers first
   - `WARN` — Approve is allowed, but name the warnings

Never invent an Approve. Never print secret material.

## File map

- `reviewer/` — policy engine (http, diff, scan, jobs, artifacts, notes)
- `scripts/review_mr.py` — helper entrypoint the GitLab job execs
- `.gitlab-ci-include.yml` — remote include other GitLab projects use
- `templates/review.yml` — GitLab CI/CD component form
- `reviewer.json` — default gates for *this* repo
- `tests/` — unittest suite (`python3 -m unittest`)
- `examples/consumer.gitlab-ci.yml` — copy-paste include for a product repo

## Commands

```sh
make ci                 # lint + comments + include-pin + tests
make review             # dry-run note, no API
make pin                # rewrite include clone SHAs to HEAD
make check_comments     # five-part comment gate only
python3 -m unittest discover -s tests -t .
```
