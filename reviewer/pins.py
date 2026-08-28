"""Exact-pin helpers shared with the diff scanner."""

from __future__ import annotations

import json
import re
from pathlib import Path

PY_EXACT = re.compile(r"^==\d+\.\d+\.\d+$")
NODE_EXACT = re.compile(r"^\d+\.\d+\.\d+$")
REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~][^;#]+)?")


def pep503(name):
    """
    What: Fold a Python distribution name to its PEP 503 comparison key.
    Why: Flask and flask must hit the same allowlist slot.
    Who: load_allowlist and the pin scanner name check.
    Where: approved-packages.json backend keys versus requirement lines.
    How: Lowercase the name and squeeze runs of dash, underscore, or dot.
    """
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def is_exact_pin(spec, kind):
    """
    What: True when a specifier is an exact X.Y.Z pin for that ecosystem.
    Why: Caret, tilde, and range pins must become blocker findings.
    Who: The pin scanner inside scan_diff.
    Where: requirement lines and package.json version strings.
    How: Match ==X.Y.Z for python and bare X.Y.Z for node after stripping.
    """
    text = (spec or "").strip()
    if kind == "python":
        return bool(PY_EXACT.fullmatch(text))
    return bool(NODE_EXACT.fullmatch(text))


def load_allowlist(path):
    """
    What: Load backend and frontend pin maps, or empty maps when absent.
    Why: This standalone bot has no portal allowlist unless a consumer adds one.
    Who: scan_diff when it grades newly added package names.
    Where: approved-packages.json at the consumer project root.
    How: Read JSON when the file exists; otherwise return two empty dicts.
    """
    target = Path(path)
    if not target.is_file():
        return {"backend": {}, "frontend": {}}
    data = json.loads(target.read_text(encoding="utf-8"))
    backend = {pep503(name): str(spec) for name, spec in (data.get("backend") or {}).items()}
    frontend = {str(name): str(spec) for name, spec in (data.get("frontend") or {}).items()}
    return {"backend": backend, "frontend": frontend}
