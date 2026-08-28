"""Exact-pin helpers shared with the diff scanner."""

from __future__ import annotations

import json
import re
from pathlib import Path

from reviewer.config import git_show_text, trusted_ref_specs

PY_EXACT = re.compile(r"^==\d+\.\d+\.\d+$")
NODE_EXACT = re.compile(r"^\d+\.\d+\.\d+$")
REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~][^;#]+)?")
EMPTY_ALLOWLIST = {"backend": {}, "frontend": {}}


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


def parse_allowlist_text(text):
    """
    What: Parse approved-packages.json text into backend and frontend maps.
    Why: git show and checkout reads must share one fail-closed parser.
    Who: load_allowlist and read_trusted_allowlist.
    Where: A protected-ref blob or the working-tree file.
    How: json.loads an object; fold backend names; return None on missing junk.
    """
    if text is None:
        return None
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    backend_in = data.get("backend")
    frontend_in = data.get("frontend")
    backend = {}
    if isinstance(backend_in, dict):
        backend = {pep503(name): str(spec) for name, spec in backend_in.items()}
    frontend = {}
    if isinstance(frontend_in, dict):
        frontend = {str(name): str(spec) for name, spec in frontend_in.items()}
    return {"backend": backend, "frontend": frontend}


def allowlist_nonempty(allow):
    """
    What: True when either ecosystem map in an allowlist has at least one name.
    Why: A non-empty protected list must not collapse into the allow-all path.
    Who: merge_allowlists when it chooses trusted versus checkout maps.
    Where: backend and frontend dicts after parse_allowlist_text.
    How: Treat missing maps as empty and require one populated mapping.
    """
    if not isinstance(allow, dict):
        return False
    return bool(allow.get("backend") or allow.get("frontend"))


def _union_allow_map(base, extra):
    """
    What: Copy base name/spec pairs and add extra keys that are not already set.
    Why: HEAD may add packages but must not drop or retarget a protected name.
    Who: merge_allowlists for backend and frontend maps.
    Where: In-memory allowlist dicts after trusted and tree parses.
    How: Keep every base key; insert extra keys only when the name is new.
    """
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if key not in merged:
            merged[key] = value
    return merged


def merge_allowlists(trusted, tree):
    """
    What: Combine a protected allowlist with a checkout copy without allow-all.
    Why: Deleting or emptying approved-packages.json on an MR must not skip names.
    Who: load_review_allowlist after both blobs are parsed.
    Where: Trusted maps first, then optional HEAD additions.
    How: Keep trusted when it is non-empty; union-add tree; else use tree only.
    """
    protected = trusted if isinstance(trusted, dict) else EMPTY_ALLOWLIST
    checkout = tree if isinstance(tree, dict) else EMPTY_ALLOWLIST
    if allowlist_nonempty(protected):
        return {
            "backend": _union_allow_map(protected.get("backend"), checkout.get("backend")),
            "frontend": _union_allow_map(protected.get("frontend"), checkout.get("frontend")),
        }
    if allowlist_nonempty(checkout):
        return {
            "backend": dict(checkout.get("backend") or {}),
            "frontend": dict(checkout.get("frontend") or {}),
        }
    return {"backend": {}, "frontend": {}}


def read_trusted_allowlist(root, env=None, show_fn=None):
    """
    What: Load approved-packages.json from the first trusted git-show spec.
    Why: The pin allowlist must come from the same protected refs as reviewer.json.
    Who: load_review_allowlist before it considers the working-tree file.
    Where: origin/target and origin/default approved-packages.json blobs.
    How: Walk trusted_ref_specs; parse the first object; skip empties and junk.
    """
    reader = show_fn or (lambda spec: git_show_text(root, spec))
    found_empty = None
    for spec in trusted_ref_specs(env, "approved-packages.json"):
        parsed = parse_allowlist_text(reader(spec))
        if parsed is None:
            continue
        if allowlist_nonempty(parsed):
            return parsed
        found_empty = parsed
    return found_empty


def load_allowlist(path):
    """
    What: Load backend and frontend pin maps, or empty maps when absent.
    Why: This standalone bot has no portal allowlist unless a consumer adds one.
    Who: load_review_allowlist when it reads the checkout file.
    Where: approved-packages.json at the consumer project root.
    How: Read JSON when the file exists; otherwise return two empty dicts.
    """
    target = Path(path)
    if not target.is_file():
        return {"backend": {}, "frontend": {}}
    parsed = parse_allowlist_text(target.read_text(encoding="utf-8"))
    return parsed if parsed is not None else {"backend": {}, "frontend": {}}


def load_review_allowlist(root, env=None, show_fn=None):
    """
    What: Load the pin allowlist from the protected ref, then a hardened checkout.
    Why: An MR that drops approved-packages.json must not turn pin-allowlist off.
    Who: run_review and scan_diff when they grade newly added package names.
    Where: git show of approved-packages.json, then the file at project root.
    How: Merge trusted maps with HEAD using merge_allowlists; empty stays fail-open.
    """
    base = Path(root) if root is not None else Path(".")
    trusted = read_trusted_allowlist(base, env, show_fn=show_fn)
    tree_path = base / "approved-packages.json"
    tree = load_allowlist(tree_path) if tree_path.is_file() else {"backend": {}, "frontend": {}}
    return merge_allowlists(trusted, tree)
