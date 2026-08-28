#!/usr/bin/env python3
"""CLI entry for the five-part comment quality gate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comment_lib import run_check


def main(argv=None):
    """
    What: Process-exit wrapper around the five-part comment inventory.
    Why: make comments needs a status code, not a Python tuple.
    Who: Makefile comments target and make ci.
    Where: scripts/check_comments.py invoked with python3.
    How: Call run_check, print each failure, return 1 when the list is not empty.
    """
    failures, paths = run_check(list(sys.argv[1:] if argv is None else argv))
    if failures:
        print("comment check failed:")
        for item in failures:
            print(item)
        return 1
    print("comment check passed (%d file(s))" % len(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
