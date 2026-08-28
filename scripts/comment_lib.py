"""Fail the build when first-party functions lack a five-part comment."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PARTS = ("WHAT", "WHY", "WHO", "WHERE", "HOW")
LABEL_RE = re.compile(
    r"^\s*(?:#\s*)?(?:\*\s*)?(WHAT|WHY|WHO|WHERE|HOW)\s*:\s*(.*)$",
    re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|XXX|placeholder|self-explanatory|n/?a)\b",
    re.IGNORECASE,
)
CONFIG_RELS = (
    ".gitlab-ci.yml",
    ".gitlab-ci-include.yml",
    "templates/review.yml",
    "Makefile",
    "scripts/review-mr.sh",
)
YAML_TOP_SKIP = frozenset(
    {
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
        "---",
    }
)


def repo_rel(path):
    """
    What: Format a filesystem path relative to this reviewer repository root.
    Why: Failure lines must be stable file:line:name:reason, not absolute.
    Who: report printers in the comment gate.
    Where: scripts/check_comments.py output.
    How: Path.relative_to(ROOT) with forward slashes so CI and local match.
    """
    return path.resolve().relative_to(ROOT).as_posix()


def normalize(text):
    """
    What: Collapse a comment or source snippet for equality checks.
    Why: HOW-versus-body and WHAT-versus-WHY compares should ignore punctuation.
    Who: quality_reasons when it compares the five parts to the next statement.
    Where: Checker comparisons only.
    How: Lowercase, squeeze whitespace, strip trailing punctuation marks.
    """
    squeezed = re.sub(r"\s+", " ", text or "").strip()
    return squeezed.lower().strip(".;:")


def parse_five_parts(comment):
    """
    What: Pull WHAT/WHY/WHO/WHERE/HOW values out of a house-style comment.
    Why: Python docstrings and hash comments use the same five labels.
    Who: check_python_file and the config-block checkers.
    Where: Immediately under a def or above a job/target.
    How: Scan lines for Label: text; keep reading unlabeled continuations.
    """
    parts = {}
    current = None
    for raw in (comment or "").splitlines():
        match = LABEL_RE.match(raw.rstrip())
        if match:
            current = match.group(1).upper()
            parts[current] = match.group(2).strip()
            continue
        if current is None:
            continue
        extra = raw.strip()
        if extra.startswith("*"):
            extra = extra[1:].strip()
        if extra:
            parts[current] = (parts[current] + " " + extra).strip()
    return parts


def quality_reasons(comment, parts, body):
    """
    What: List why a five-part comment fails the quality bar.
    Why: Labels alone are not enough; HOW must not copy the next source line.
    Who: check_python_file and the config-block checkers.
    Where: scripts during make comments.
    How: Missing or short parts, placeholders, WHAT equals WHY, HOW copies body.
    """
    reasons = []
    missing = [name for name in PARTS if not (parts.get(name) or "").strip()]
    if missing:
        return ["missing " + ",".join(missing)]
    for name in PARTS:
        if len(parts[name].strip()) < 8:
            reasons.append(name + " too short")
    blob = (comment or "") + " " + " ".join(parts.values())
    if PLACEHOLDER_RE.search(blob):
        reasons.append("placeholder/TODO/self-explanatory")
    if normalize(parts.get("WHAT", "")) == normalize(parts.get("WHY", "")):
        reasons.append("WHAT equals WHY")
    body_n = normalize(body)
    how_n = normalize(parts.get("HOW", ""))
    if body_n and how_n and (how_n == body_n or (len(body_n) > 20 and body_n in how_n)):
        reasons.append("HOW copies body")
    return reasons


def first_python_body(source, node):
    """
    What: First executable statement of a Python function, as source text.
    Why: HOW must not copy the next source line.
    Who: quality_reasons for Python defs.
    Where: Function body after the five-part docstring.
    How: Skip the leading docstring Expr; ast.get_source_segment on the next stmt.
    """
    body = list(getattr(node, "body", []) or [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return ""
    snippet = ast.get_source_segment(source, body[0]) or ""
    return snippet.strip()


def python_docstring(node):
    """
    What: Return the leading docstring of a Python function, or empty.
    Why: House comments live in the def docstring, not a block above.
    Who: check_python_file.
    Where: ast FunctionDef and AsyncFunctionDef nodes.
    How: ast.get_docstring with clean False; None becomes an empty string.
    """
    return ast.get_docstring(node, clean=False) or ""


def check_python_file(path, source):
    """
    What: Inventory Python functions and fail those with a weak five-part comment.
    Why: make comments must exit 1 on missing What/Why/Who/Where/How.
    Who: main for each first-party py file.
    Where: reviewer/*.py and scripts/*.py.
    How: ast.walk FunctionDef; report file:line:name:reason.
    """
    failures = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{repo_rel(path)}:{exc.lineno or 1}:<parse>:syntax error"]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        comment = python_docstring(node)
        parts = parse_five_parts(comment)
        body = first_python_body(source, node)
        reasons = quality_reasons(comment, parts, body)
        if reasons:
            line = getattr(node, "lineno", 1)
            failures.append(f"{repo_rel(path)}:{line}:{node.name}:{'; '.join(reasons)}")
    return failures


def preceding_hash_comment(lines, idx):
    """
    What: Collect the hash comment block immediately above a config item.
    Why: YAML jobs and make targets keep house comments above the name.
    Who: The config-block checkers below.
    Where: Lines just before a job or target.
    How: Skip blanks, then take a run of hash comments.
    """
    i = idx - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0 or not lines[i].lstrip().startswith("#"):
        return ""
    end = i
    while i >= 0 and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i -= 1
    chunk = [line for line in lines[i + 1 : end + 1] if line.lstrip().startswith("#")]
    return "\n".join(chunk)


def next_indented_body(lines, idx):
    """
    What: First non-comment line that belongs to a YAML or make block.
    Why: HOW must not copy the next real key or script line.
    Who: check_yaml_jobs and check_makefile.
    Where: Lines after a job or target.
    How: Walk forward and return the first non-empty, non-hash line.
    """
    i = idx + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        return stripped
    return ""


def report_config(path, line, name, comment, body):
    """
    What: Build failure lines for one config block using the function quality bar.
    Why: Jobs and targets must fail for the same reasons a function fails.
    Who: Each config checker after it finds a name and its comment.
    Where: file:line:name:reason, same shape as Python reports.
    How: parse_five_parts plus quality_reasons; empty list when the block is clean.
    """
    parts = parse_five_parts(comment)
    reasons = quality_reasons(comment, parts, body)
    if not reasons:
        return []
    return [f"{repo_rel(path)}:{line}:{name}:{'; '.join(reasons)}"]


def check_yaml_jobs(path, source):
    """
    What: Inventory GitLab pipeline jobs and grade their comments.
    Why: Include and hosted jobs must stay documented for operators.
    Who: check_path for .gitlab-ci.yml and the include templates.
    Where: Top-level job keys, skipping workflow and stages.
    How: Find job names, take the hash block above each, score with quality_reasons.
    """
    lines = source.splitlines()
    failures = []
    for idx, line in enumerate(lines):
        if line.startswith(" ") or line.startswith("\t"):
            continue
        match = re.match(r"^([A-Za-z0-9_.:-]+):\s*$", line)
        if not match or match.group(1) in YAML_TOP_SKIP:
            continue
        name = match.group(1)
        comment = preceding_hash_comment(lines, idx)
        body = next_indented_body(lines, idx)
        failures.extend(report_config(path, idx + 1, name, comment, body))
    return failures


def check_makefile(path, source):
    """
    What: Inventory Makefile targets and grade the comment above each one.
    Why: make ci names must stay documented the same way functions are.
    Who: check_path for the repo-root Makefile.
    Where: Target lines that are not dot-specials like .PHONY.
    How: Regex for name: at column 0; body is the first tab recipe or prereq list.
    """
    lines = source.splitlines()
    failures = []
    for idx, line in enumerate(lines):
        if line.startswith("\t") or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_./?*%@+-]+):(\s.*)?$", line)
        if not match:
            continue
        name = match.group(1)
        if name.startswith("."):
            continue
        rest = (match.group(2) or "").strip()
        body = rest or next_indented_body(lines, idx)
        comment = preceding_hash_comment(lines, idx)
        failures.extend(report_config(path, idx + 1, name, comment, body))
    return failures


def check_shell_header(path, source):
    """
    What: Grade the five-part header at the top of a shell wrapper.
    Why: review-mr.sh is the shared entry and must stay documented.
    Who: check_path for scripts/review-mr.sh.
    Where: The leading hash comments before set -e.
    How: Parse the file header as one comment and score it as a config block.
    """
    header = []
    for line in source.splitlines():
        if line.startswith("#"):
            header.append(line)
            continue
        if not line.strip():
            if header:
                continue
        break
    body = "exec python3 scripts/review_mr.py"
    return report_config(path, 1, path.name, "\n".join(header), body)


def _is_first_party(path):
    """
    What: True when a path is first-party Python or a documented config file.
    Why: Tests and caches must not fail the comment check.
    Who: git_delta_paths and all_first_party.
    Where: reviewer/, scripts/*.py, Makefile, and GitLab YAML.
    How: Suffix plus directory prefix; skip tests and pycache.
    """
    rel = repo_rel(path)
    if rel in CONFIG_RELS:
        return True
    if "/tests/" in f"/{rel}/" or rel.startswith("tests/"):
        return False
    if "/__pycache__/" in rel:
        return False
    if rel.startswith("reviewer/") and path.suffix == ".py":
        return True
    if rel.startswith("scripts/") and path.suffix == ".py":
        return True
    return False


def git_delta_paths(base=None):
    """
    What: List first-party files changed in the working tree or last commit.
    Why: Comments-grade can stay a delta when operators do not pass --all.
    Who: run_check when --all is unset.
    Where: git diff --name-only against BASE, or HEAD when the tree is dirty.
    How: Collect diff, cached, and untracked names, then keep first-party files.
    """

    def run(args):
        """
        What: Run a git command at this reviewer repository root.
        Why: Path collection must use this checkout, not the caller cwd.
        Who: git_delta_paths.
        Where: ROOT of gitlab-mr-reviewer.
        How: subprocess.check_output text True with cwd ROOT.
        """
        return subprocess.check_output(args, cwd=ROOT, text=True)

    porcelain = run(["git", "status", "--porcelain"]).strip()
    if base:
        names = run(["git", "diff", "--name-only", base]).splitlines()
        names += run(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
    elif porcelain:
        names = run(["git", "diff", "--name-only", "HEAD"]).splitlines()
        names += run(["git", "diff", "--name-only", "--cached"]).splitlines()
        names += run(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
    else:
        names = run(["git", "diff", "--name-only", "HEAD~1", "HEAD"]).splitlines()
    paths = []
    seen = set()
    for name in names:
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        path = ROOT / name
        if path.is_file() and _is_first_party(path):
            paths.append(path)
    return paths


def all_first_party():
    """
    What: Every first-party source and config file for the whole-tree sweep.
    Why: make ci uses --all so a docs-only helper cannot skip the comment bar.
    Who: run_check when --all is passed.
    Where: reviewer/, scripts/, Makefile, and GitLab YAML.
    How: rglob Python files plus CONFIG_RELS, then _is_first_party.
    """
    paths = list((ROOT / "reviewer").rglob("*.py"))
    paths += list((ROOT / "scripts").glob("*.py"))
    paths += [ROOT / rel for rel in CONFIG_RELS]
    return [item for item in paths if item.is_file() and _is_first_party(item)]


def check_path(path):
    """
    What: Dispatch a file to the Python or config comment checker.
    Why: One entry so main can print a single failure list.
    Who: run_check loop.
    Where: Each first-party path.
    How: Suffix py uses check_python_file; YAML, Makefile, and shell have their own.
    """
    source = path.read_text(encoding="utf-8")
    rel = repo_rel(path)
    if rel == "scripts/review-mr.sh":
        return check_shell_header(path, source)
    if path.name == "Makefile":
        return check_makefile(path, source)
    if path.suffix in {".yml", ".yaml"} and rel in CONFIG_RELS:
        return check_yaml_jobs(path, source)
    if path.suffix == ".py":
        return check_python_file(path, source)
    return []


def run_check(args):
    """
    What: Run the five-part comment quality gate over chosen paths.
    Why: make comments must fail when a helper is undocumented.
    Who: check_comments.py entry.
    Where: scripts/comment_lib.py.
    How: --all sweeps the tree; otherwise git_delta_paths; return failure lines.
    """
    use_all = "--all" in args
    base = None
    if "--base" in args:
        idx = args.index("--base")
        if idx + 1 < len(args):
            base = args[idx + 1]
    paths = all_first_party() if use_all else git_delta_paths(base)
    failures = []
    for path in sorted(paths):
        failures.extend(check_path(path))
    return failures, paths
