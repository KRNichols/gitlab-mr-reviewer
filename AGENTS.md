# AGENTS.md

You are the human-like reviewer first. The GitLab bot is the later
mechanical approver. A green lint/test pipeline is necessary. It is
**not** proof the story is done.

This file is trained on two team sources (read from screen capture;
do not call the internal GitLab host):

- Pipe Dreams wiki: **Story Definition of Done** (Mark-Anthony Hutton,
  27 May 2025)
- `Metrics-Gitlab-Discovery.md` (28 Aug 2026): 34 Metrics MRs, 239
  peer-review notes, 142 inline comments. Training record, not a blame
  record. Goal: stop the same review churn on AMC Doxygen and later
  Pipe Dreams work.

If you follow this file, a human reviewer should not have to repeat
the Metrics comments. If you ignore it, hold the MR.

## Bottom line — four proofs before review starts

Reviewers kept asking for these. They must be visible in the MR
description and the diff. Do not infer them from a title or a green
pipeline.

1. Exact story link, scope boundary, and acceptance criteria.
2. A focused diff a human can understand and reproduce.
3. Proof for the runtime level actually being introduced: unit test,
   container, or deployment.
4. A branch that is current with its target and has no unreviewed
   follow-on work mixed into it.

Grade every change `READY`, `HOLD`, or `WARN`. Never invent Approve.
Never print a secret.

## Role split

| Who | Job |
| --- | --- |
| Codex / you | Human reviewer. Read the story. Check scope, proof, DoD, description. |
| `gitlab-mr-reviewer` bot | Mechanical approver. Pins, blocking jobs, artifacts, secret-*like* added lines, five-part comments, empty description. Fail closed. |
| Human reviewer | Final judgment on elegance, audience, and “does this respect the story.” |

Do not fake the human checks with a brittle source scanner. Do not
claim the bot ran because GitHub Actions went green.

## Story Definition of Done

Developers meet this before handing work to review. Reviewers review
against it.

### Functionality

- All story acceptance criteria are met, or the MR states why a
  criterion was not met.
- Associated pipeline stages (build, scan, test, publish, and any
  story-owned stage) succeeded.
- Code and scripts are functional and idempotent: they run the first
  time and every time after.
- Previous capabilities still work.
- No new defects or tech debt are introduced.

### Quality

- Scripts and code stay simple. No unnecessary complexity.
- Multiple sequential tasks are scripted, not documented as a
  click-path.
- New work follows existing conventions (names, locations, layout).
- Existing lint and static analysis stay green.
- DevOps principles:
  - Automate as much of the lifecycle as possible.
  - Leave it better than you found it. Small, reasonable changes.
    Do not disappear down a refactor rabbit hole.
  - End-user focused. Does this respect the audience.
  - Create with the end in mind.
- DRY. Do not repeat yourself.

### Documentation

- Clear and concise.
- Top-down. Introduce what the next reader needs when they need it.
- Existing docs updated when behavior changes.
- The next developer can understand and use the new capability.
- A README edit is **not** required for every code edit. Require a
  documentation-impact decision: updated / not needed / deferred,
  with one sentence why.

### Pre-review prep

An MR is ready for review only when it:

- Has a description that
  - provides the story title and link
  - summarizes how the work meets the story criteria
  - gives exact steps to test the work against acceptance criteria
- Targets the correct branch, with that target merged in (always;
  other work lands on the target).
- Marks the story ready for review and points the team at the
  relevant titles and links.

### Post-review (after Approve, before acceptance)

- MR merged and closed.
- Source branch deleted.
- Story marked ready for acceptance.
- Team notified.
- Merge-conflict reconciliation re-checked against this DoD.

## Training-ready rules

1. Read the linked story and its acceptance criteria before changing
   code. State whether this MR **closes**, **supports**, or is
   **independent** of the story.
2. Keep one MR on one meaningful delivery slice. Move unrelated work
   to a separately linked story/branch before review. Do not fail an
   MR only because it is large. Fail it when it cannot prove its
   boundary.
3. Put executable source in reviewable first-party files (`.py`,
   source, CI, charts). Do not commit notebooks, dependency folders,
   generated build output, local logs, or secrets unless a documented
   exception exists. Review must be possible from the diff.
4. Keep production dependencies separate from test/lint dependencies.
   Test the same entrypoint production runs (Gunicorn/WSGI, not
   `python app.py`, when that is the contract).
5. A static contract is a preflight, not runtime proof. A Containerfile
   is not a container. Build, scan/lint, run, then hit `/health`
   before claiming container work is done.
6. Put CI diagnostics in CI. Do not add production routes only to
   troubleshoot pipeline connectivity. Never print a token to prove
   the network works.
7. Mock external systems in unit tests (success, missing config,
   unauthorized, not-found, malformed response, network failure).
   Live smoke only when the story owns that environment and those
   credentials.
8. Require the target branch to be current before approval. Use
   GitLab merge status. Do not invent a custom rebase shell job.
9. The MR description tells a reviewer the story, what is
   intentionally excluded, and exactly how to verify it. Do not infer
   completion from the title.
10. Never log, print, commit, or use a secret outside its approved
    purpose. Read-only fallback credentials stay read-only. Report
    secret-*like* findings by type only. Never reprint the match.
11. Add a new gate only when repeated review evidence shows it is
    broadly reusable. Keep story-specific checks inside that story's
    acceptance tests.

## Required MR description shape

Use this template. Human/context checks. Do not replace it with a
regex that pretends to understand the story.

```markdown
## Work-item relationship
- Story: <title and link>
- This MR: closes / supports / does not close the story

## Scope boundary
- Included:
- Explicitly deferred:

## Verification
- Exact commands:
- Expected result:
- Runtime/deployment proof, when applicable:

## Review readiness
- Target branch is current.
- No unrelated generated output, local files, secrets, or dependency
  folders are tracked.
- Documentation impact was reviewed: updated / not needed / deferred.
```

Empty description is a hold. A title-only description is a hold.
“Pipeline is green” is not verification.

## Story-scoped gates

Turn these on only when that story is in the slice. Do not make them
generic repo gates.

| Story in scope | Required proof |
| --- | --- |
| Container build | Approved internal image build, `.dockerignore` that fits this repo, scan/lint evidence, startup smoke with test-safe config, `/health` against the running container. Registry push only when that criterion is authorized. |
| Okta / S3 / GitLab / DB adapter | Mocked success, missing config, unauthorized, not-found, malformed response, network failure. No live credentials in the unit-test job. |
| Helm / KaaS | Helm lint/render, every declared value consumed or explicitly reserved, probes, security context, Service **and** Ingress if a service is claimed, rollout/status, HTTPS smoke. Never infer a deployable service from a Deployment alone. |
| ETL / ingestion | Story-specific idempotency, duplicate-key, transaction, and failure-path tests. Do not hang a generic ETL gate on a documentation portal. |

KaaS/Helm belongs with deployment stories, not with a CI-foundation
MR. A green validation/quality/test/security pipeline is not an image
build, not an image scan, not a container smoke, and not a registry
push.

## Do not add as generic gates

- Do not fail an MR solely because it is large.
- Do not require a README edit for every code edit.
- Do not add generic ETL idempotency checks to unrelated work.
- Do not add Helm/KaaS checks before Helm/KaaS work exists.
- Do not make a token or external service call part of an ordinary
  unit-test gate.
- Do not use `GITLAB_ROOT_API_TOKEN` (or any root token) as an npm
  credential, a write token, or log output.

## How to grade a diff

When the user asks “is this ready?”:

1. Read the linked story and acceptance criteria. If none are linked,
   `HOLD`.
2. State closes / supports / independent.
3. List included vs explicitly deferred. If unrelated work is mixed
   in, `HOLD` and ask for a split.
4. Check the four proofs. Missing proof for the runtime level being
   claimed is `HOLD`.
5. Walk Functionality, Quality, Documentation, Pre-review prep.
6. Apply story-scoped gates only when that story is in the slice.
7. Flag mechanical issues the bot will also catch: range pins,
   secret-like added lines (type only), missing five-part comments
   on new functions/jobs/targets, empty description, red required
   jobs.
8. End with exactly one of:

   - `READY` — DoD met, four proofs visible, story-scoped proof
     present if claimed, mechanical gates should pass.
   - `HOLD` — bot or human must refuse Approve. Blockers first.
   - `WARN` — Approve is allowed if jobs pass, but name the warnings
     (huge diff with a stated boundary, docs deferred with a reason,
     missing tests on a comment-only change, and so on).

Never treat “CI is green” as `READY`.

## Lessons that trained these rules

Use the pattern, not the internal URLs.

- **MR 110** — Reviewer could not inspect notebook/hidden source.
  Health job had to move from `python app.py` to Gunicorn/WSGI.
  Duplicate dependency installs were rejected. Lesson: reviewable
  `.py` files, split runtime vs test deps, test the real entrypoint.
- **MR 117** — Large ETL mixed with app/model/CI/README. Review asked
  where the raw table was and how repeats behave. Lesson: prove the
  story boundary; split unrelated behavior. Line count is not a gate.
- **MR 119** — Containerfile + HEALTHCHECK was not enough. Review
  still wanted `.dockerignore`, image lint/scan, and a running
  `/health`. Lesson: static contract plus build → scan → run → smoke.
- **MR 120** — KaaS YAML without Service/Ingress. Lesson: full
  delivery contract, not a Deployment alone.
- **MR 123** — Helm values, secrets, routes, and tests in one MR.
  Review asked whether declared values were consumed and whether the
  deployed instance actually changed. Lesson: least privilege, every
  value used or reserved, reproducible verification.
- **MR 126** — GitLab connectivity preflight belongs in CI. Do not
  add token-bearing routes to the app. Never echo the token.

## Mechanical approver (`gitlab-mr-reviewer`)

This repository is the bot. Product repos include it. The bot must
stay fail-closed and small. It does **not** replace the human checks
above.

It already blocks on:

- required jobs missing or red (`require_blocking_jobs` defaults true)
- inexact pins (`^` `~` ranges)
- new function/job/target without What/Why/Who/Where/How
- secret-like added lines (type only, no snippet)
- empty MR description
- JUnit failures / npm high-critical artifacts when present

It does **not** decide story completeness. Codex does that with this
file. The bot Approves only after mechanical gates pass.

When editing **this** repo:

- python3 + curl only. No new packages. No `requests` / `httpx`.
- Token never on curl argv (`0600` header file). Refuse `CI_DEBUG_TRACE`.
- HTTP 0 and non-2xx fail closed. Never fake Approve.
- Same-project MRs only. Ignore job-level repo/ref overrides.
- After helper changes, `make pin` so the include SHA is current.
- `reviewer.json` comes from the protected target branch. A checkout
  may add jobs or upgrade warn → blocker. It must not downgrade
  `secret` / `pin-range`, empty `blocking_jobs`, or turn off
  `require_blocking_jobs`.

```sh
make ci
make review          # laptop dry-run note, no API
make pin
make check_comments
```

## Copy this file

- Keep this `AGENTS.md` in `gitlab-mr-reviewer` so Codex maintaining
  the bot does not regress the contract.
- Copy it to the product repo root (Doxygen, Metrics, later Pipe
  Dreams apps) so Codex reviews those diffs like a Pipe Dreams human.
- Product-repo `reviewer.json` may name that repo's blocking jobs.
  Do not copy Metrics/Helm/ETL gates into a repo that does not have
  those stories yet.
