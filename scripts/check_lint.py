#!/usr/bin/env python3
"""Syntax-only lint using the Python standard library."""

from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREES = ("reviewer", "scripts", "tests")


def iter_python_files():
    """
    What: Yield first-party Python files under reviewer, scripts, and tests.
    Why: compileall should not walk .git or a consumer checkout.
    Who: main when it syntax-checks the tree.
    Where: Repository root plus the three named directories.
    How: rglob *.py and skip __pycache__ and hidden folders.
    """
    for tree in TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/__pycache__/" in f"/{rel}/":
                continue
            yield path


def main(argv=None):
    """
    What: Compile each first-party Python file and fail on the first syntax error.
    Why: make lint must stay python3-only with no extra packages.
    Who: Makefile lint target and make ci.
    Where: scripts/check_lint.py.
    How: py_compile.compile doraise True for every yielded path.
    """
    del argv
    failed = []
    for path in iter_python_files():
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failed.append(str(exc))
    if failed:
        print("lint failed:")
        for item in failed:
            print(item)
        return 1
    print("lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
