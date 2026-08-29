# AGENTS.md

The agent completes author checks. A human independently reviews
and approves.

You grade readiness and you finish local work. A green pipeline is
necessary. It is **not** proof the story is done. Never invent
Approve. Never merge. Never print a secret. Never hand back a chore
list you could have finished in the working tree.

This file is a reusable review contract. Copy it to the root of any
repository where an agent should finish author checks before a human
reviews. Adapt tracker names, CI job names, and package sources to
that repository. Do not copy helper-repo commands that do not exist
there.

If you follow this file, a human reviewer independently confirms the
grade. If you ignore it, hold the change.

Grade every change `READY`, `HOLD`, or `WARN`.

`READY` means ready for **human review**. It never means approved,
merged, or accepted.

A mechanical reviewer bot is later work. Ignore it unless the user
asks for it.

## Authority boundary

Review is **read-only by default**. Do not edit hosted descriptions,
work-item status, branches, pipeline variables, approvals, or merge
state unless the user explicitly authorizes that remote write.

Remote writes, status changes, merge-request edits, pushes, and
merges require explicit user authorization. A review request is not
authorization.

Author mode is only when the user asks you to implement, finish, fix,
or write the change. Even then, finish the work in the working tree.
Do not push, retitle, rewrite a hosted description, click Approve, or
merge unless the user said to do that remote action.

Never direct-push or commit to protected `main` (or the repository's
protected default branch). Use a branch and a merge request. That
rule holds in author mode too.

## Evidence boundary

Read the project's hosting system with ordinary credentials
(`GITLAB_TOKEN` on GitLab, the equivalent read token elsewhere).
Open story URLs, descriptions, diffs, pipelines, and job logs when
those facts are required.

A root, owner, or break-glass token is a **read-only fallback** only
when normal access is blocked. Never use it to write, as package
authentication, or in logs.

Do not refuse to open a story URL or a pipeline because training
material was once a screenshot. Live review needs live evidence.

Never print a token, an authorization header, or a raw secret match.

## Pipeline proof

A green subset is a `HOLD`.

For every CI change, and before `READY` on any merge request:

1. Lint the **merged** CI configuration (target plus source), not only
   the source-branch file.
2. Inventory required jobs from that merged config and from product
   docs / policy files when they exist.
3. Open the actual merge-request pipeline. Verify every **required**
   job **ran**, is **blocking**, and was **not skipped**. Required
   means the story, the merged CI rules, or project docs named it as
   a gate for this slice.
4. A required job that is `manual`, `allow_failure`, skipped by
   `rules`, missing from an include, or renamed is a `HOLD`.
5. An optional job is not a `HOLD`. Manual or `allow_failure` deploy
   jobs must not block a CI-only change when that deploy is not in
   the story or policy for this slice.

A pipeline that is green because a **required** job never ran is
incomplete. Do not treat that as Functionality or Pre-review prep.

## Package boundary

Install from the registry this repository already uses. If the
project pins an internal, private, or approved mirror, do not add a
public fallback. Verify the effective pip, npm, or language-equivalent
registry before installing dependencies.

Never use a root or break-glass token as package auth, a write token,
or log output.

## Discover before you invent

Before you add an image build, a registry push, a secret mount, a
runner, a pipeline include, Terraform, or Helm, find what this
GitLab group already shipped. Match it. Do not invent a second
stack.

Search **GitLab only**. Do not open Jira. Do not open Confluence.

The agent must understand the live setup: image config, CI includes,
runners, pipelines, Terraform, Helm, secret *names*, secret store
type, and where those names are consumed. Understanding is required.
Guessing is a `HOLD`.

Do not refuse discovery because secrets exist. Open the GitLab
docs and config. Use names, paths, and store type. Never reprint a
secret **value** in chat, in the report, or in a committed file.

### Where truth lives

Search in this order. Stop when the fact is proven. If a GitLab
page will not open, say so and ask for the URL.

1. This repository on **this branch** and on `main`. They can
   disagree. Say so.
2. The GitLab work item: acceptance criteria, description, notes,
   linked items, named example projects.
3. The **GitLab group this project belongs to**: sibling repos,
   wiki, CI includes, merged merge requests, review comments,
   runners, image pipelines, Terraform, and Helm that already
   exist there.
4. The user, only for a blank GitLab left empty.

Another agent session is not a source of truth. Live GitLab beats
memory.

It has been done before in this group. Find that work and continue
it.

### Discovery card

Fill this before the first image, secret, runner, Terraform, or
Helm edit.

```
DISCOVERY
- GitLab docs opened (repo, main, work item, group wiki, sibling
  projects, merged MRs, review comments):
- GitLab pages that would not open:
- Containerfile / Dockerfile path:
- Base image source:
- Output registry:
- Image name / tag contract:
- Jobs that actually build an image:
- Jobs that actually push an image:
- Jobs that only lint the Containerfile (not a build):
- Jobs that only build frontend/static assets (not an image):
- Runners / tags already used in this group:
- Runtime process and port:
- Health endpoint:
- Package / pip mirror in Containerfile vs CI vs main:
- Secret names referenced:
- Secret store type (CI variable, file mount, vault, k8s, unknown):
- Where those names are consumed:
- Existing Terraform in this group (reuse / none / MISSING):
- Existing Helm in this group (reuse / none / MISSING):
- Example pipeline / include the work item or group already uses:
- What I will reuse:
- What is missing and I must ask before inventing:
```

`HOLD` and ask when the story needs a fact and the card still says
MISSING / unknown:

- No output registry.
- No image name.
- No build/push job and no group include to copy.
- Secret store type unknown and you were about to add a new one.
- Terraform or Helm would be new while the group already has one.
- `main` and this branch disagree and you picked one silently.
- A required GitLab page would not open.

Never:

- Invent a public registry next to an approved internal one.
- Treat a Containerfile lint job or a frontend build as an image
  build.
- Add a new runner, include, Terraform root, or Helm chart when
  the group already has a working one.
- Commit secret values. `.env.example` stays placeholders.
- Call live identity, object storage, or secret APIs from unit tests.
- Search Jira or Confluence for these facts.

A static Containerfile plus a health comment is still not a build.

## Your job
