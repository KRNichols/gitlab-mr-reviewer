#!/bin/sh
# WHAT: Thin wrapper that starts the GitLab MR reviewer helper.
# WHY: make review and the GitLab job must share one entry so they cannot drift.
# WHO: make review and the GitLab review job on merge_request_event.
# WHERE: scripts/review-mr.sh at the repository root.
# HOW: Change to the repo root and exec scripts/review_mr.py with python3.
set -e
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
if [ -n "${CI_DEBUG_TRACE:-}" ]; then
  case "$(printf '%s' "$CI_DEBUG_TRACE" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      echo "Refusing to run: CI_DEBUG_TRACE is on and would leak GITLAB_REVIEWER_TOKEN." >&2
      exit 1
      ;;
  esac
fi
PYTHON="${PYTHON:-python3}"
exec "$PYTHON" "$ROOT/scripts/review_mr.py" "$@"
