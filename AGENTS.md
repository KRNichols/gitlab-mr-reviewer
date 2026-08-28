# AGENTS.md

You do the work. A human stamps.

You are the author and the reviewer. A green pipeline is necessary.
It is **not** proof the story is done. Never invent Approve. Never
print a secret. Never hand back a chore list you could have finished.

This file is trained on two team sources (read from screen capture;
do not call the internal GitLab host):

- Pipe Dreams wiki: **Story Definition of Done** (Mark-Anthony Hutton,
  27 May 2025)
- `Metrics-Gitlab-Discovery.md` (28 Aug 2026): 34 Metrics MRs, 239
  peer-review notes, 142 inline comments. Training record, not a blame
  record. Goal: stop the same review churn on AMC Doxygen and later
  Pipe Dreams work.

If you follow this file, a human reviewer should only confirm the
grade. If you ignore it, hold the MR.

Grade every change `READY`, `HOLD`, or `WARN`.

The GitLab bot is later. Ignore it unless the user asks for it.

## Your job

Default mode is **author**. You are writing or finishing the change.
Reviewer mode is only when the user asks “is this ready?”, “review
this”, or points at someone else’s MR.

In both modes you own:

1. The story URL in the **description**, not the title.
2. A quality What/Why/Who/Where/How comment on every new or changed
   first-party function, CI job, and Make target.
3. The four proofs.
4. The Story Definition of Done.
5. The review report at the bottom of this file.

You do **not** own elegance as a substitute for those five. Pretty
code with no story URL is a `HOLD`.

### Finish it, do not narrate it

If you can fix the finding with facts you already have, fix it.

| Finding | What you do |
| --- | --- |
| Description missing the story URL and the user (or ticket) already gave one | Write the description template with that URL. |
| Description is title-only / “pipeline is green” | Rewrite it to the template. |
| New or changed function missing or gutting the five-part comment | Write the comment. Do not ask the human to. |
| Verification is missing and you know the commands | Write exact commands and expected results. |
| Docs impact is unstated | Write `updated` / `not needed` / `deferred` plus one sentence. |
| Mixed unrelated work | Split it, or `HOLD` with the exact split. Do not “warn” and ship. |
| Story URL is unknown | Ask once. Then `HOLD`. Do not invent a tracker link. |
| Acceptance criteria are behind a URL you cannot open | Say so. `HOLD`. Do not guess the story. |

A review that only lists chores is a failed review.

## Hard holds — do these first, every time

These two checks are not optional and are not warnings. If either
fails, the grade is `HOLD`. Do not keep reading for a way to pass.
In author mode, fix them before you stop.

### 1. The description must link the story

`HOLD` unless the MR **description** (not the title) contains a real
work-item URL.

A valid story link is an `http://` or `https://` URL that points at
the work item (Azure Boards, Jira, GitLab issue, wiki story page).
A Markdown link counts: `[Story title](https://...)`.

`HOLD` when any of these is true:

- Description is empty or whitespace only.
- Description is only the title repeated, or only a pipeline badge.
- No `http://` / `https://` URL appears in the description.
- The story is named only in the **title** (`Add login — ADO-1234`).
- The story is named only as bare text (`ADO-1234`, `story 88`,
  `see the wiki`, `same as last sprint`) with no URL.
- The only URLs are unrelated (CI job, README, image host, shields.io)
  and no URL is presented as the work item.

Do not infer the story from the branch name, the commit list, or a
green pipeline. If you cannot click a work-item URL in the
description, the story is not linked.

After you find the URL, state whether this MR **closes**, **supports**,
or is **independent** of that story. If the description does not say
which, write it (author mode) or `HOLD` and ask (reviewer mode).

Then open the URL. Read the acceptance criteria. Map each criterion
to evidence in the diff or the verification steps. An unmapped
criterion is a `HOLD` unless the description says why it is deferred.

### 2. Every new or changed function needs a quality five-part comment

`HOLD` if any new or changed first-party **function**, **CI job**, or
**Make target** in the diff lacks a quality five-part comment.

Required labels, in any order, each on its own line:

```
What: <one sentence — what this unit does>
Why:  <one sentence — why it exists / what fails without it>
Who:  <who calls it or who runs this job/target>
Where: <file, job, stage, or call site>
How:  <how it works, not a paste of the next source line>
```

Python: the five parts live in the function docstring. YAML jobs and
Make targets: the five parts live in the `#` block immediately above
the name.

Quality bar (same rules as `scripts/comment_lib.py` when that file
exists; apply them by hand everywhere else):

- All five labels present, each with a non-empty value.
- Each value is at least 8 characters after the label.
- No `TODO`, `TBD`, `FIXME`, `XXX`, `placeholder`, `self-explanatory`,
  or `n/a` in any part.
- `What` and `Why` are not the same sentence.
- `How` does not copy the next source / script / recipe line.

`HOLD` examples — rewrite these, do not ship them:

```
What: helper
Why: helper
Who: us
Where: here
How: return True
```

```
What: Validates input
Why: Validates input
Who: run_review
Where: reviewer/review.py
How: if not token: return 1
```

Pass example:

```
What: Refuse a hosted review when CI_PROJECT_DIR is the helper clone.
Why: A job-level override would retarget git show at this bot.
Who: run_review before it loads trusted policy.
Where: reviewer/config.py on a merge_request_event.
How: Resolve the path and compare it to the helper root and the
     .gitlab-mr-reviewer segment; return None plus a reason.
```

Scope:

- Count every added or edited `def` / `async def`, JS `function` /
  arrow assigned to a name, GitLab job key, and Make target in
  first-party files.
- Nested helpers count. Dunder methods count.
- Files under `tests/` are exempt unless the product repo says
  otherwise.
- A comment that only exists eight lines away, or only in a commit
  message, does not count.
- If a function body changed and the five-part comment was removed or
  gutted, that is a `HOLD` even if the function is not brand new.

How to inventory (do this, do not skim):

1. List every changed first-party path in the diff.
2. For each path, list every added or edited function / job / target
   by name and line.
3. For each name, quote the five-part block or write `MISSING`.
4. Score each block against the quality bar. One failing part fails
   the name.
5. Put the inventory in the report. A review with no inventory is
   incomplete.

## Four proofs — visible before you say done

Do not infer these from a title or a green pipeline.

1. Exact story **URL**, scope boundary, and acceptance criteria.
2. A focused diff a human can understand and reproduce.
3. Proof for the runtime level actually being introduced: unit test,
   container, or deployment.
4. A branch that is current with its target and has no unreviewed
   follow-on work mixed into it.

Missing proof for the runtime level being **claimed** is a `HOLD`.
A Containerfile is not a container. `python app.py` is not Gunicorn
when Gunicorn is the contract. Helm YAML is not a Service.

## Story Definition of Done

Developers meet this before handing work to review. You meet it
before you stop. Reviewers review against it.

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
- Every new or changed first-party function, job, and target has a
  quality What/Why/Who/Where/How comment (see Hard holds).
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
  - provides the story title **and a clickable story URL**
  - states closes / supports / independent
  - summarizes how the work meets the story criteria
  - gives exact steps to test the work against acceptance criteria
- Targets the correct branch, with that target merged in (always;
  other work lands on the target).
- Marks the story ready for review and points the team at the
  relevant titles and links.

You write that description. You do not leave a stub.

### Post-review (after Approve, before acceptance)

- MR merged and closed.
- Source branch deleted.
- Story marked ready for acceptance.
- Team notified.
- Merge-conflict reconciliation re-checked against this DoD.

You do not merge unless the user asks. You do name the post-review
steps if the grade is `READY`.

## Training-ready rules

1. Read the linked story URL and its acceptance criteria before
   changing code. No URL in the description is a `HOLD`.
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
9. The MR description tells a reviewer the story URL, what is
   intentionally excluded, and exactly how to verify it. Do not infer
   completion from the title.
10. Never log, print, commit, or use a secret outside its approved
    purpose. Read-only fallback credentials stay read-only. Report
    secret-*like* findings by type only. Never reprint the match.
11. Add a new gate only when repeated review evidence shows it is
    broadly reusable. Keep story-specific checks inside that story's
    acceptance tests.
12. Every new or changed first-party function, job, and target has a
    quality five-part comment. Labels alone are not enough.
13. You produce the description, the comments, the proofs, and the
    report. A human does not fill those in after you.

## Required MR description

Write this. A heading without the URL is still a `HOLD`.

```markdown
## Work-item relationship
- Story: [title](https://example.invalid/work-item/ID)
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
- New or changed functions, jobs, and targets have quality five-part
  comments.
```

Replace `https://example.invalid/work-item/ID` with the real tracker
URL.

Empty description is a hold. A title-only description is a hold.
A description with no work-item URL is a hold.
“Pipeline is green” is not verification.

## Story-scoped gates

Turn these on only when that story is in the slice. Do not make them
generic repo gates. If the slice claims the story, you produce the
proof or you `HOLD`.

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

When the user asks “is this ready?” or you are about to stop on a
change you wrote:

1. Open the MR description. If there is no work-item `http(s)` URL,
   fix it or return `HOLD`. Name that the story is not linked.
2. Open that URL. Read acceptance criteria. State closes / supports /
   independent. Map each AC to evidence. If you cannot open the URL,
   `HOLD`.
3. Walk every new or changed first-party function, CI job, and Make
   target. Inventory them. If any five-part comment is missing or
   fails the quality bar, write it (author mode) or `HOLD` and list
   the names (reviewer mode on someone else’s diff).
4. List included vs explicitly deferred. If unrelated work is mixed
   in, `HOLD` and give the split.
5. Check the four proofs. Missing proof for the runtime level being
   claimed is `HOLD`.
6. Walk Functionality, Quality, Documentation, Pre-review prep.
7. Apply story-scoped gates only when that story is in the slice.
8. Flag pins, secret-*like* added lines (type only), empty
   description, missing work-item URL, weak five-part comments, red
   required jobs.
9. End with the report below. Exactly one grade.

Never treat “CI is green” as `READY`.
Never treat “looks good” as a report.

## Required report

Every review ends with this block. Do not skip rows. Do not replace
it with a paragraph.

```markdown
GRADE: READY | HOLD | WARN

STORY: <http(s) URL or MISSING>
RELATIONSHIP: closes | supports | independent | UNSTATED
AC MAP:
- <criterion>: <evidence in diff or verification> | MISSING

FUNCTIONS:
- <name> (<path>:<line>): PASS | FAIL <reason>

PROOFS:
- story+scope+AC: PASS | FAIL
- focused reproducible diff: PASS | FAIL
- runtime-level proof: PASS | FAIL | N/A
- current target, no mixed follow-on: PASS | FAIL

DOD:
- Functionality: PASS | FAIL
- Quality: PASS | FAIL
- Documentation: PASS | FAIL <updated|not needed|deferred>
- Pre-review prep: PASS | FAIL

BLOCKERS:
- <one line each, or none>

WARNINGS:
- <one line each, or none>

WHAT I CHANGED:
- <description rewrite, comments written, tests added, or none>

WHAT I STILL NEED:
- <only facts a human must supply, or none>
```

`READY` — story URL present and opened, AC mapped, five-part quality
holds on every inventoried name, DoD met, four proofs visible,
story-scoped proof present if claimed.

`HOLD` — refuse Approve. Blockers first. Missing story URL and
missing/weak five-part comments are blockers. Unmapped AC is a
blocker. Missing claimed runtime proof is a blocker.

`WARN` — Approve is allowed if jobs pass, but name the warnings
(huge diff with a stated boundary, docs deferred with a reason,
missing tests on a comment-only change). Never use `WARN` for a
missing story URL, a weak five-part comment, an unmapped AC, or a
missing claimed runtime proof.

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

## Later: mechanical approver

This repository also ships `gitlab-mr-reviewer`. Product repos may
include it later. It is pins, jobs, artifacts, and cheap scanners.
It does **not** replace this file. Do not wait for it. Do not treat
a bot Approve as `READY`. Do not implement bot work unless the user
asks.

When editing **this** repo only:

- python3 + curl only. No new packages. No `requests` / `httpx`.
- Every new or changed function in `reviewer/` and `scripts/` needs a
  quality five-part docstring. `make comments` / `make ci` enforce it.

```sh
make ci
make review
make pin
make check_comments
```

## Copy this file

- Keep this `AGENTS.md` in `gitlab-mr-reviewer` so the contract does
  not drift.
- Copy it to the product repo root (Doxygen, Metrics, later Pipe
  Dreams apps) so the agent reviews those diffs like a Pipe Dreams
  human and **finishes the MR**.
- After this file changes, copy the new version again. A stale copy
  will leave story links and five-part comments for a human.
- Product-repo `reviewer.json` may name that repo's blocking jobs.
  Do not copy Metrics/Helm/ETL gates into a repo that does not have
  those stories yet.
