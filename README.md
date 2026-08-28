# gitlab-mr-reviewer

Standalone GitLab merge-request reviewer bot. It can sit as a **required
reviewer** on a same-project MR: one idempotent summary note, inline
discussions on blocker lines, then Approve or unapprove.

It is pins, comments, jobs, and artifacts. It is **not** a secret review, not
an ATO, and not a GitHub Actions job that Approves GitHub pull requests.

## What it does

On a GitLab `merge_request_event` pipeline, after product jobs:

1. Refuses to run when `CI_DEBUG_TRACE` is on (that would leak the token).
2. Refuses fork / other-project MRs (`CI_MERGE_REQUEST_SOURCE_PROJECT_ID` must
   equal `CI_PROJECT_ID`).
3. Fails closed if `GITLAB_REVIEWER_TOKEN` is missing. It never fakes Approve.
4. Reads the MR diff and this pipeline's jobs (and common artifacts when
   present).
5. Updates **one** summary note in place (no new note each pipeline).
6. Opens or updates inline discussions on blocker lines (GitLab discussions
   API with `position`).
7. Approves when blocking gates pass and there are no blocker findings.
8. On every non-approve MR path, POSTs unapprove (404 is fine). The job fails
   if unapprove is needed and the call is not 2xx. A failed approvals GET is
   treated as a possible stale Approve and is cleared.

`make review` on a laptop is a dry-run: scan the local diff, print one note,
do not call the API.

## Add GITLAB_REVIEWER_TOKEN

Shortest path that stays fail-closed:

1. In **this GitLab project only**, create a **shortest-lived Project Access
   Token**.
2. Role: **Developer only** (not Maintainer, not Owner).
3. Scope: `api` (notes, discussions, approve, unapprove, jobs, artifacts).
4. Expiration: as short as your rotation allows. Rotate before it lapses.
5. Settings → CI/CD → Variables → add `GITLAB_REVIEWER_TOKEN`:
   - **Masked**
   - **Protected**
   - **Not** available to fork pipelines / merge requests from forks
6. Settings → Merge requests → Approval rules: add that `project_*_bot` user
   as an eligible reviewer. Set **Approvals required: 1** and make the rule
   required if you want merge to wait on this bot.
7. Same-project MRs only. Fork MRs are refused even if someone later exposes
   the variable.

The helper builds the API root from `CI_SERVER_HOST` or `CI_SERVER_FQDN`
(`https://<host>/api/v4`), and only then from a validated `CI_SERVER_URL`.
It requires https, allowlists that host, and rejects userinfo. It does **not**
honor a job-level `CI_API_V4_URL` override. The token is passed to curl via a
`0600` header file (`-H @file`), never on argv.

## Make the bot a required MR reviewer

1. Add the project-bot user from the token as a reviewer (step 6 above).
2. Require that approval rule on the target branch.
3. Include the review job so it runs on every `merge_request_event` with
   `when: always` (so a later red push still unapproves).
4. Put product jobs in earlier stages. The include uses GitLab's built-in
   `.post` stage.

## Include this bot in another GitLab project

```yaml
include:
  - remote: "https://raw.githubusercontent.com/KRNichols/gitlab-mr-reviewer/main/.gitlab-ci-include.yml"
```

The included `review` job clones this repository **at a SHA shipped in the
include file**, then runs `python3 .gitlab-mr-reviewer/scripts/review_mr.py`
against **your** `CI_PROJECT_DIR`. You do not copy the Python files.

Do **not** set `GITLAB_REVIEWER_REPO` or `GITLAB_REVIEWER_REF` on the review
job. Those job-level overrides are not honored (an MR must not retarget the
clone).

**How consumers bump the pin:** take a newer `.gitlab-ci-include.yml` from this
repo's default branch (or point the remote include at a newer commit of that
file). The include always ships a 40-character SHA that equals **this** commit
(`git rev-parse HEAD`), or the parent when HEAD is the include-only self-pin
follow-up. `make include-pin` / `make ci` fail if the pin is stale. Bump both
`.gitlab-ci-include.yml` and `templates/review.yml` together (`make pin`).

GitLab CI/CD component (when this repo is mirrored to GitLab):

```yaml
include:
  - component: $CI_SERVER_FQDN/<group>/gitlab-mr-reviewer/review@<sha>
```

A full consumer sketch lives in `examples/consumer.gitlab-ci.yml`.

## This repository's own CI

`.gitlab-ci.yml` runs:

- `test` — `make ci` (lint, comment gate, unittests)
- `review` — `make review` on `merge_request_event` only, `when: always`

`make review` locally is a dry-run. `make ci` is python3 only (lint, comments,
include-pin, tests). No extra pip or npm packages. `make pin` rewrites both
include fetch lines to `HEAD` for a self-pin follow-up commit.

## What blocks Approve

| Signal | Default |
| --- | --- |
| `backend` / `frontend` / `quality` / `build` / `security:node` (also `node-audit`) red | Blocker |
| Named blocking job missing | Blocker (`require_blocking_jobs` defaults **true**) |
| `security:pip` / `pip-audit` red | Does **not** block (not a secrets-OK) |
| Inexact pin (`^` `~` range) | Blocker |
| New function/job/target without What/Why/Who/Where/How | Blocker |
| Secret-like added line (AKIA, PEM, `GITLAB_REVIEWER_TOKEN=`, PAT prefixes) | Blocker (type only; no snippet) |
| Empty MR description | Blocker |
| JUnit failures in an artifact | Blocker |
| npm audit high/critical artifact | Blocker |
| Huge diff | Warn |
| Missing tests for changed code | Warn |
| pip-audit artifact findings | Warn (override with `REVIEW_PIP_AUDIT_BLOCKS=1`) |

Only **blockers** hold Approve. Warnings stay in the summary note.

The note says this is pins/jobs/artifacts, **not a secret review**. A green
path that skipped a red pip-audit is **not** secrets-OK. The bot reports
secret-*like* added lines so Approve cannot land on an obvious key; it does
not replace a secret scanner and it never reprints the matching text.

## Configure

Optional `reviewer.json` is loaded from the **default / target branch name**
(`git show` of `origin/<name>:reviewer.json`), not from the MR HEAD and not
from job-overridable `CI_MERGE_REQUEST_TARGET_BRANCH_SHA`. A checkout copy
may add jobs or upgrade a warn to blocker. It cannot downgrade `secret` /
`pin-range` (or other blockers) to warn, empty `blocking_jobs`, turn off
`require_blocking_jobs`, replace `job_aliases` (no dummy→blocker remap), or
empty `pin_files` (union only), or replace `path_rules` (union-add only). A
trusted overlay is hardened the same way against DEFAULTS, so a poisoned
target blob cannot fully weaken gates.

Trusted (protected-ref) knobs:

- `blocking_jobs` / `job_aliases` / `require_blocking_jobs` (default **true**)
- `pip_audit_blocks`
- `huge_diff_lines`
- `coverage_min`
- `pin_files`
- `path_rules` (optional hooks; there are **no** F-18 / Boeing chrome rules)
- `severities` (`blocker` or `warn` per rule)

Environment overrides (MR YAML cannot opt out of required jobs):

- `REVIEW_DRY_RUN=1`
- `REVIEW_BLOCKING_JOBS=extra-job` (union only; empty is ignored)
- `REVIEW_PIP_AUDIT_BLOCKS=1` (tighten only)
- `REVIEW_REQUIRE_JOBS=1` (tighten only on an MR; `=0` is for laptop/unit tests)
- `REVIEW_PROJECT_DIR` (laptop only; ignored when `CI_MERGE_REQUEST_IID` is set)

On an MR, `CI_PROJECT_DIR` is required and must be the consumer checkout, not
`.gitlab-mr-reviewer` and not this helper tree. A job-level empty or clone-root
override fails closed.

`approved-packages.json` is loaded from the **same trusted ref** as
`reviewer.json` (`git show` of `origin/<name>:approved-packages.json`). When
that protected list is non-empty, a checkout that deletes or empties the file
does **not** become allow-all: new names are still checked against the
protected maps (HEAD may add names only). When no protected list exists, the
file is optional and only exact-pin shape is checked.

## Local commands

```sh
make review          # dry-run: one note, no API
make ci              # lint + comments + tests
sh scripts/review-mr.sh --dry-run
```

python3 + curl only. No version ranges. No new packages.

## Security properties (must stay green)

1. Token never on curl argv (header via `0600` file). Refuse `CI_DEBUG_TRACE`.
2. HTTP 0 and non-2xx fail closed. Approve requires a successful MR changes
   fetch. Note write and approval writes must not treat status 0 as success.
3. API root from `CI_SERVER_HOST` / `CI_SERVER_FQDN`, else `CI_SERVER_URL`.
   https. Allowlisted host. No userinfo. `CI_API_V4_URL` ignored.
4. Summary note fences job/finding lines, strips `@`, and never echoes raw
   added diff lines.
5. Shortest-lived Developer Project Access Token, this project, masked and
   protected, not for fork pipelines. Same-project MRs only.
6. Every non-approve MR path POSTs unapprove (ignore 404). Fail the job if
   that call is not 2xx when unapprove is required. Failed approvals GET does
   not leave a stale Approve.
7. Secret-like added lines hold Approve. The note states pins/jobs only. pip-audit
   skip is not secrets-OK. CI stdout does not reprint raw matching snippets.
8. `reviewer.json` comes from the default/target **branch name**. An MR
   checkout cannot downgrade secret/pin-range, empty `blocking_jobs`, remap
   `job_aliases`, or empty `pin_files`. Trusted overlays cannot fully weaken
   DEFAULTS even if `CI_MERGE_REQUEST_TARGET_BRANCH_SHA` is job-overridable.
9. `require_blocking_jobs` defaults true. Missing backend/frontend/quality/build
   /node-audit holds Approve. `manual` is not a pass. MR YAML cannot set
   `REVIEW_REQUIRE_JOBS=0`.
10. The include job clones a SHA shipped in the include file. That SHA must
    equal HEAD (or HEAD^ when HEAD only edits the include files). Job-level
    `GITLAB_REVIEWER_REPO` / `GITLAB_REVIEWER_REF` are not used.
11. `approved-packages.json` comes from the default/target branch. An MR that
    deletes or empties it cannot turn pin-allowlist into allow-all when the
    protected list is non-empty. MR-tree `path_rules` are union-add only.
12. On an MR, `REVIEW_PROJECT_DIR` cannot retarget `git show` of policy or
    the allowlist. The project root is `CI_PROJECT_DIR` only.
13. On an MR, missing `CI_PROJECT_DIR` fails closed. A root that is the
    include clone (`.gitlab-mr-reviewer`) or this helper checkout is refused
    and cannot Approve.

## What this is not

- Not an Authority to Operate and not a claim that a project can hold CUI or
  classified data.
- Not a secret-scanning product and not an exploit, PoC, or attack playbook.
- Not a GitHub Actions workflow that Approves GitHub pull requests.
