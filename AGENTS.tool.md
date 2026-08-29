# AGENTS.tool.md

This file is for **gitlab-mr-reviewer only**. Do not copy it into
Doxygen, Metrics, or other product repos. Those repos copy
`AGENTS.md` and use their own comment checker, Make targets, and
package mirrors.

Product `AGENTS.md` is the contract. This file is the tool-repo
overlay: commands that exist here and nowhere else.

## Authority

Same boundary as `AGENTS.md`. Read-only review by default. Never
invent Approve. Never merge. `READY` means eligible for human review.

The bot in this repository is later work. Do not implement bot
behavior unless the user asks.

## Tool-repo stack

python3 + curl only. No new packages. No `requests` / `httpx`. No
version ranges.

```sh
make ci
make lint
make comments
make include-pin
make test
make review
make pin
```

`make ci` is lint, comments, include-pin, then test. A green `make
test` with skipped comment or include-pin jobs is a `HOLD`.

## Comment checker here

This repository's comment-quality command is:

```sh
python3 scripts/check_comments.py --all
```

Same gate via `make comments`. The helper library is
`scripts/comment_lib.py`. That path is **not** the product-repo
standard. Doxygen and Metrics run their own checkers.

Named test functions in this repo are in scope when the checker
walks them. Do not exempt `tests/` by policy in `AGENTS.md`.

Passing threshold: the command exits 0 and prints no failure lines.

## Pipeline proof here

For every CI or include change:

1. Lint `.gitlab-ci.yml`, `.gitlab-ci-include.yml`, and
   `templates/review.yml` as they will merge onto the target.
2. Confirm the include clone SHA equals HEAD, or HEAD^ when HEAD only
   edits the include files (`make include-pin`).
3. Confirm the hosted MR pipeline actually ran `test` / `lint` /
   `comments` / `include-pin` equivalents as blocking jobs. A green
   subset that skipped the include-pin gate is a `HOLD`.

A prior green-but-incomplete pipeline on this repository is why the
inventory rule exists.

## Package boundary here

No pip or npm install is part of this helper. Do not add one. If a
future change adds a lockfile or registry, it must use the approved
internal mirror only, with no public fallback, and must not use
`GITLAB_ROOT_API_TOKEN` as auth.

## What you still do

Follow `AGENTS.md` for story URL, five-part comments, four proofs,
DoD, and the report. Then add the tool-repo rows:

- comment checker command = `python3 scripts/check_comments.py --all`
- merged CI lint and include-pin status
- no new third-party Python package
