"""Diff scanners: pins, five-part comments, secrets, tests, description, size."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from reviewer.config import severity_for
from reviewer.findings import Finding
from reviewer.pins import is_exact_pin, load_review_allowlist, pep503

JSON_PIN_RE = re.compile(r"""["']([^"']+)["']\s*:\s*["']([^"']+)["']""")
REQ_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*([<>=!~][^;#]+)?")
PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")
JS_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
JS_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^;]*?\)|[A-Za-z_$][\w$]*)\s*=>"
)
MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_./?*%@+-]+):(\s.*)?$")
CI_JOB_RE = re.compile(r"^([A-Za-z0-9_.:-]+):\s*$")
VERSION_SPEC_RE = re.compile(r"^(?:\^|~|==|!=|<=|>=|<|>)?\d")
FIVE_PARTS = ("what", "why", "who", "where", "how")
SKIP_JSON_KEYS = {
    "name",
    "version",
    "private",
    "type",
    "scripts",
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
    "note",
    "backend",
    "frontend",
    "description",
    "license",
    "main",
    "module",
    "exports",
    "files",
    "author",
    "repository",
}
CI_SKIP_KEYS = {
    "workflow",
    "stages",
    "variables",
    "default",
    "include",
    "image",
    "cache",
    "before_script",
    "after_script",
    "spec",
}
PIN_BASENAMES = {
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "approved-packages.json",
}
SECRET_RULES = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "reviewer-token-assignment",
        re.compile(r"GITLAB_REVIEWER_TOKEN\s*[=:]\s*['\"]?[^\s'\"`]{8,}"),
    ),
    ("gitlab-pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("github-pat", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    (
        "quoted-credential",
        re.compile(
            r"""(?i)\b(api[_-]?key|secret[_-]?key|password|passwd|private[_-]?key)\b\s*[=:]\s*['"][^'"]{8,}['"]"""
        ),
    ),
)


def _posix(path):
    """
    What: Normalize a diff path to a relative posix string.
    Why: Pin-file and test-path checks must not care about slashes or dots.
    Who: Every scanner that reads item['path'].
    Where: Unified-diff b/ paths from parse_diff.
    How: Replace backslashes and strip a leading ./ .
    """
    return (path or "").replace("\\", "/").lstrip("./")


def _rows(added):
    """
    What: Coerce added lines into dicts with text and new_line.
    Why: Older tests pass strings; live parse_diff already uses dicts.
    Who: Each per-file scanner.
    Where: The added list on a parsed file record.
    How: Wrap a bare string as text with a missing line number.
    """
    rows = []
    for row in added or []:
        if isinstance(row, dict):
            rows.append({"text": row.get("text") or "", "new_line": row.get("new_line")})
        else:
            rows.append({"text": str(row), "new_line": None})
    return rows


def _is_test_path(path):
    """
    What: True when this file lives under a unit-test tree.
    Why: Tests may add helpers without a production five-part comment.
    Who: The new-function scanner.
    Where: Paths that contain /tests/ or a tests prefix.
    How: Normalize slashes and look for a tests segment.
    """
    rel = _posix(path)
    return "/tests/" in f"/{rel}/" or rel.startswith("tests/")


def _is_pin_file(path, pin_files):
    """
    What: True when this diff path is a pin manifest the bot must scan.
    Why: Caret ranges only matter in declared requirement and package files.
    Who: scan_diff when it walks each parsed file.
    Where: Configured pin_files plus common requirement basenames.
    How: Compare the posix path and the basename to the known manifests.
    """
    rel = _posix(path)
    name = rel.split("/")[-1]
    if rel in (pin_files or []):
        return True
    if name in PIN_BASENAMES:
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def _kind_for_path(path):
    """
    What: Pick python or node pin rules from the file path.
    Why: ==X.Y.Z is exact for wheels; X.Y.Z without a caret is exact for npm.
    Who: The pin scanner inside scan_diff.
    Where: requirements files versus package.json and the allowlist doc.
    How: requirements names are python; package.json is node.
    """
    rel = _posix(path)
    if rel.endswith(".txt") or "requirements" in rel.split("/")[-1]:
        return "python"
    if rel.endswith("package.json"):
        return "node"
    return ""


def _name_allowed(name, allow):
    """
    What: True when this package already sits on the committed allowlist.
    Why: A new first-party name is a finding only when a list exists.
    Who: The pin scanner inside scan_diff.
    Where: approved-packages.json backend and frontend maps.
    How: PEP 503 fold for Python names; case-insensitive match for Node names.
    """
    backend = allow.get("backend") or {}
    frontend = allow.get("frontend") or {}
    if not backend and not frontend:
        return True
    if pep503(name) in backend:
        return True
    if name in frontend:
        return True
    folded = {item.lower(): item for item in frontend}
    return name.lower() in folded


def _looks_like_spec(spec):
    """
    What: True when a JSON value looks like a version specifier.
    Why: package.json also holds true and portal strings that are not pins.
    Who: The pin scanner when it reads name/spec pairs.
    Where: Added lines in PIN files.
    How: Accept a leading comparator or caret/tilde, then a digit.
    """
    return bool(VERSION_SPEC_RE.match((spec or "").strip()))


def _has_five_part(text):
    """
    What: True when a snippet names What, Why, Who, Where, and How.
    Why: A new function without that house comment is a hold finding.
    Who: The new-function and new-CI-target scanners.
    Where: Added lines near a new def, job, or make target.
    How: Case-fold the snippet and require each of the five labels.
    """
    lower = (text or "").lower()
    return all(part in lower for part in FIVE_PARTS)


def _pin_findings(path, added, allow, cfg):
    """
    What: Findings for inexact pins and names missing from an allowlist.
    Why: A caret react pin must hold Approve on the consuming merge request.
    Who: scan_diff for each pin-file hunk.
    Where: Added lines only, never the whole committed manifest.
    How: Parse requirement and JSON pairs, then is_exact_pin plus the allowlist.
    """
    findings = []
    kind_hint = _kind_for_path(path)
    for row in _rows(added):
        stripped = row["text"].strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-r "):
            continue
        pairs = []
        json_hit = JSON_PIN_RE.search(row["text"])
        if json_hit:
            key, spec = json_hit.group(1), json_hit.group(2)
            if key not in SKIP_JSON_KEYS and _looks_like_spec(spec):
                kind = kind_hint or ("python" if spec.strip().startswith("==") else "node")
                pairs.append((key, spec, kind))
        elif kind_hint == "python" or REQ_PIN_RE.match(stripped):
            match = REQ_PIN_RE.match(stripped)
            if match and (kind_hint == "python" or _looks_like_spec(match.group(2) or "")):
                spec = (match.group(2) or "").strip()
                pairs.append((match.group(1), spec, "python"))
        for name, spec, kind in pairs:
            if spec and not is_exact_pin(spec, kind):
                findings.append(
                    Finding(
                        "pin-range",
                        f"{name} specifier is not an exact pin",
                        severity_for(cfg, "pin-range"),
                        path=path,
                        line=row["new_line"],
                    )
                )
            if not _name_allowed(name, allow):
                findings.append(
                    Finding(
                        "pin-allowlist",
                        f"{name} is not on the approved list",
                        severity_for(cfg, "pin-allowlist"),
                        path=path,
                        line=row["new_line"],
                    )
                )
    return findings


def _comment_findings(path, added, cfg):
    """
    What: Findings for newly added functions or CI names without five parts.
    Why: The quality gate expects What/Why/Who/Where/How on every new helper.
    Who: scan_diff for first-party source, skipping /tests/.
    Where: Added def, function, make target, and GitLab job lines.
    How: Take a window of nearby added lines and look for the five labels.
    """
    rel = _posix(path)
    if _is_test_path(rel):
        return []
    findings = []
    rows = _rows(added)
    texts = [row["text"] for row in rows]
    name_is_ci = rel.endswith(".yml") or rel.endswith(".yaml") or rel.endswith("Makefile")
    for idx, row in enumerate(rows):
        name = None
        match = PY_DEF_RE.match(row["text"])
        if match:
            name = match.group(1)
        if name is None:
            match = JS_FUNC_RE.match(row["text"])
            if match:
                name = match.group(1)
        if name is None:
            match = JS_ARROW_RE.match(row["text"])
            if match:
                name = match.group(1)
        if name is None and name_is_ci:
            if rel.endswith("Makefile"):
                match = MAKE_TARGET_RE.match(row["text"])
                if match and not match.group(1).startswith("."):
                    name = match.group(1)
            else:
                match = CI_JOB_RE.match(row["text"])
                if match and match.group(1) not in CI_SKIP_KEYS:
                    name = match.group(1)
        if not name:
            continue
        window = texts[max(0, idx - 8) : idx + 16]
        if not _has_five_part("\n".join(window)):
            findings.append(
                Finding(
                    "five-part",
                    f"{name} is missing a five-part comment",
                    severity_for(cfg, "five-part"),
                    path=path,
                    line=row["new_line"],
                )
            )
    return findings


def classify_secret(text):
    """
    What: Return a secret-pattern name when the added line looks credential-like.
    Why: Approve must hold on keys, PEM, and token assignments without reprinting.
    Who: _secret_findings and the red-team secret tests.
    Where: Added line text only; the type name is the only thing retained.
    How: Run the compiled pattern table and return the first matching rule id.
    """
    blob = text or ""
    for rule_id, pattern in SECRET_RULES:
        if pattern.search(blob):
            return rule_id
    return ""


def _secret_findings(path, added, cfg):
    """
    What: Blocker findings for secret-like added lines, type only.
    Why: A leaked key must hold Approve; the note must not echo the secret.
    Who: scan_diff for every changed file.
    Where: Added lines in the merge-request diff.
    How: classify_secret, then record rule plus type with no snippet.
    """
    findings = []
    for row in _rows(added):
        kind = classify_secret(row["text"])
        if not kind:
            continue
        findings.append(
            Finding(
                "secret",
                f"possible {kind} on an added line",
                severity_for(cfg, "secret", "blocker"),
                path=path,
                line=row["new_line"],
            )
        )
    return findings


def _path_rule_findings(path, added, cfg):
    """
    What: Optional consumer path_rules from reviewer.json, no default chrome locks.
    Why: F-18 overlay rules stay out of this standalone bot unless opted in.
    Who: scan_diff after the generic scanners.
    Where: cfg['path_rules'] list of path_glob, pattern, message, severity.
    How: fnmatch the path, search joined added text, emit a Finding.
    """
    findings = []
    rel = _posix(path)
    blob = "\n".join(row["text"] for row in _rows(added))
    for rule in cfg.get("path_rules") or []:
        if not isinstance(rule, dict):
            continue
        glob = str(rule.get("path_glob") or rule.get("path_prefix") or "")
        if glob.endswith("/"):
            matched = rel.startswith(glob.lstrip("./"))
        else:
            matched = fnmatch.fnmatch(rel, glob) if glob else True
        if not matched:
            continue
        pattern = str(rule.get("pattern") or "")
        if not pattern or not re.search(pattern, blob):
            continue
        findings.append(
            Finding(
                "path-rule",
                str(rule.get("message") or "path rule matched"),
                str(rule.get("severity") or severity_for(cfg, "path-rule")),
                path=path,
            )
        )
    return findings


def _guess_test_paths(path):
    """
    What: Candidate test file paths for a changed production source file.
    Why: Missing-test is a warn when no sibling test exists in the tree or diff.
    Who: _missing_test_findings.
    Where: Python and JavaScript production paths outside /tests/.
    How: Map foo.py to test_foo.py and foo.js to foo.test.js style names.
    """
    rel = _posix(path)
    name = rel.split("/")[-1]
    stem, _, ext = name.rpartition(".")
    if not stem:
        return []
    guesses = [
        f"tests/test_{stem}.py",
        f"test/test_{stem}.py",
        f"tests/{stem}_test.py",
        f"{stem}.test.js",
        f"{stem}.test.jsx",
        f"{stem}.test.ts",
        f"{stem}.spec.js",
    ]
    parent = "/".join(rel.split("/")[:-1])
    if parent:
        guesses.extend(
            [
                f"{parent}/test_{stem}.py",
                f"{parent}/tests/test_{stem}.py",
                f"{parent}/{stem}.test.js",
                f"{parent}/__tests__/{name}",
            ]
        )
    if ext in {"py"}:
        guesses.append(f"tests/test_{stem}.py")
    return guesses


def _missing_test_findings(files, root, cfg):
    """
    What: Warn when a production source file changes without a matching test.
    Why: A required reviewer should surface untested edits without blocking pins.
    Who: scan_diff after per-file scanners.
    Where: Consumer project root plus paths already in this diff.
    How: Skip tests and docs; look for a guessed test path on disk or in the diff.
    """
    findings = []
    changed = {_posix(item.get("path")) for item in files or []}
    base = Path(root) if root else None
    for item in files or []:
        path = _posix(item.get("path"))
        if not path or _is_test_path(path):
            continue
        if not path.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
            continue
        if path.startswith("scripts/check_") or path.endswith("comment_lib.py"):
            continue
        guesses = _guess_test_paths(path)
        found = False
        for guess in guesses:
            if guess in changed:
                found = True
                break
            if base and (base / guess).is_file():
                found = True
                break
        if not found:
            findings.append(
                Finding(
                    "missing-test",
                    "changed production file has no matching test path",
                    severity_for(cfg, "missing-test", "warn"),
                    path=path,
                )
            )
    return findings


def scan_meta(files, description, cfg):
    """
    What: Findings for an empty MR description and a huge diff.
    Why: A required reviewer should not Approve a blank story or a dump.
    Who: run_review after the diff is parsed.
    Where: Merge-request description plus changed-line total.
    How: Treat whitespace as empty; warn when the line total exceeds the cap.
    """
    from reviewer.diff import changed_line_total

    findings = []
    if not str(description or "").strip():
        findings.append(
            Finding(
                "empty-description",
                "merge request description is empty",
                severity_for(cfg, "empty-description"),
            )
        )
    total = changed_line_total(files)
    cap = int(cfg.get("huge_diff_lines") or 800)
    if cap and total > cap:
        findings.append(
            Finding(
                "huge-diff",
                f"diff is {total} lines; cap is {cap}",
                severity_for(cfg, "huge-diff", "warn"),
            )
        )
    return findings


def scan_diff(files, allow=None, cfg=None, root=None, env=None, show_fn=None):
    """
    What: Turn a parsed (or raw) diff into findings without retaining snippets.
    Why: Pins, comments, and secret-like lines must hold; raw added text must not leak.
    Who: run_review on the local git diff or the GitLab changes payload.
    Where: Pin manifests, new functions, added lines, and optional path_rules.
    How: Load the trusted allowlist when omitted, then run each scanner on added rows.
    """
    from reviewer.diff import parse_diff

    if isinstance(files, str):
        files = parse_diff(files)
    cfg = cfg or {}
    if allow is None:
        allow = load_review_allowlist(Path(root or "."), env=env, show_fn=show_fn)
    findings = []
    for item in files or []:
        path = str(item.get("path") or "")
        added = list(item.get("added") or [])
        if _is_pin_file(path, cfg.get("pin_files") or []):
            findings.extend(_pin_findings(path, added, allow, cfg))
        findings.extend(_comment_findings(path, added, cfg))
        findings.extend(_secret_findings(path, added, cfg))
        findings.extend(_path_rule_findings(path, added, cfg))
    findings.extend(_missing_test_findings(files, root, cfg))
    return findings
