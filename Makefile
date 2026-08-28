# WHAT: Local names for lint, comments, tests, and the reviewer bot.
# WHY: Humans and hosted CI must type the same words or the gates drift.
# WHO: Developers and the GitLab test and review jobs.
# WHERE: Repository root. Each target calls one script.
# HOW: Prerequisite expansion for ci; recipe lines for the named targets.

.PHONY: ci lint comments include-pin pin test review

# WHAT: Portable product contract for this reviewer repository.
# WHY: GitLab and a laptop must run the same four gates before merge.
# WHO: make ci and the hosted test job.
# WHERE: lint, comments, include-pin, then test.
# HOW: Expand the four prerequisite targets in that gate order.
ci: lint comments include-pin test

# WHAT: Syntax check for first-party Python without extra packages.
# WHY: A syntax error should fail before reviewer tests run.
# WHO: make ci and the GitLab test job.
# WHERE: scripts/check_lint.py over reviewer, scripts, and tests.
# HOW: Delegate to the lint helper using the repository python3.
lint:
	python3 scripts/check_lint.py

# WHAT: Five-part comment inventory on this bot's first-party tree.
# WHY: New helpers and CI names must stay documented for operators.
# WHO: make ci and anyone adding a function or job.
# WHERE: scripts/check_comments.py in whole-tree mode.
# HOW: Run the comment checker with the all-files flag.
comments:
	python3 scripts/check_comments.py --all

# WHAT: Fail when the include clone SHA is not HEAD (or a pin-only follow-up).
# WHY: A stale pin leaves consumers on pre-closure helper code.
# WHO: make ci and anyone cutting a bot release.
# WHERE: scripts/check_include_pin.py over both include YAML files.
# HOW: Compare the shared fetch SHA to git rev-parse HEAD.
include-pin:
	python3 scripts/check_include_pin.py

# WHAT: Rewrite both include fetch lines to the current HEAD object name.
# WHY: Operators need one command after a squash so the pin ships that commit.
# WHO: A maintainer preparing the self-pin follow-up commit.
# WHERE: .gitlab-ci-include.yml and templates/review.yml fetch origin lines.
# HOW: Delegate --write so both files receive the same 40-hex SHA.
pin:
	python3 scripts/check_include_pin.py --write

# WHAT: Stdlib unittest suite for reviewer behavior and red-team holes.
# WHY: Approve, unapprove, token, and note rules must stay proven.
# WHO: make ci and local development.
# WHERE: tests/ via python3 unittest discover.
# HOW: Discover tests from the repository root with verbose output.
test:
	python3 -m unittest discover -s tests -t . -v

# WHAT: GitLab MR reviewer bot, local dry-run or hosted note plus Approve.
# WHY: A required human reviewer should not sit idle when product gates already passed.
# WHO: Developers on a laptop and the GitLab review job on merge_request_event.
# WHERE: scripts/review-mr.sh then scripts/review_mr.py.
# HOW: Same helper both places. No live MR means dry-run; missing token on an MR fails closed.
review:
	sh scripts/review-mr.sh
