#!/usr/bin/env python3
"""Build a symbol map: identifier -> file:line.

Deliberately backend-agnostic. The linker needs one thing — where a name lives —
and there are several ways to know that. Coupling to any single one of them is
how a small tool breaks when someone else's project changes its schema.

Backends, tried in the order given:

  graphify   reads `graphify-out/graph.json` (github.com/Graphify-Labs/graphify).
             Richest: knows call edges, communities and SQL tables. Also the most
             likely to change shape, so its schema is read defensively.
  ctags      reads a `tags` file produced by universal-ctags. Ubiquitous, stable,
             no Python dependency.
  scan       a built-in fallback that greps definitions out of the source tree.
             Covers Python, JS/TS, Go, Java, Rust, Ruby, PHP, C/C++ and SQL well
             enough to be useful when nothing else is installed.

Usage:
    python3 symbols.py --repo /path/to/code --out symbols.json
    python3 symbols.py --repo /path/to/code --backend ctags --out symbols.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, Optional, Tuple



# --------------------------------------------------------------------------- #
# repository fingerprint and provenance (schema v3)
# --------------------------------------------------------------------------- #

def _git(repo, *args):
    try:
        import subprocess
        r = subprocess.run(["git", "-C", repo, *args],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def repo_state(repo):
    """(vcs, commit, dirty) — each field None when it cannot be established."""
    commit = _git(repo, "rev-parse", "HEAD")
    if commit is None:
        return None, None, None
    status = _git(repo, "status", "--porcelain", "--untracked-files=normal")
    return "git", commit, (bool(status) if status is not None else None)


def fingerprint(repo, exclude=None, ignore_files=None):
    """A content digest of the tree, independent of everything but content.

    Deliberately not derived from mtime, inode, filesystem order, absolute path
    or path separator: each of those changes without the code changing, and each
    would make the fingerprint claim something it cannot support.

    Records are `relative/path\0sha256(content)\n`, sorted by normalised path,
    then hashed. Same content, same digest — on any machine, in any checkout
    directory, on either path separator.

    Returns (digest, files_counted, warnings). A file that cannot be read is a
    warning and marks the scan partial; it is never silently skipped, because a
    fingerprint over an unknown subset is not a fingerprint.
    """
    import hashlib
    skip = set(_SKIP_DIRS) | set(exclude or [])
    # The index must not invalidate itself. Writing symbols.json inside the
    # repository would otherwise make the tree stale the instant it is written —
    # found by a test that indexed into its own fixture directory.
    ignore = {os.path.realpath(f) for f in (ignore_files or [])}
    root = os.path.realpath(repo)
    records, warnings, counted = [], [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        for fn in sorted(files):
            full = os.path.join(dirpath, fn)
            real = os.path.realpath(full)
            if real in ignore:
                continue
            if os.path.commonpath([root, real]) != root:
                warnings.append({"code": "symlink-outside-repo",
                                 "path": os.path.relpath(full, root)})
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                with open(full, "rb") as fh:
                    digest = hashlib.sha256(fh.read()).hexdigest()
            except Exception as exc:  # noqa: BLE001
                warnings.append({"code": "file-read-error", "path": rel,
                                 "message": type(exc).__name__})
                continue
            records.append(f"{rel}\0{digest}\n")
            counted += 1
    records.sort()
    h = hashlib.sha256("".join(records).encode("utf-8")).hexdigest()
    return f"sha256:{h}", counted, warnings


def config_fingerprint(config):
    import hashlib
    import json as _j
    blob = _j.dumps(config, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _add(out, name, path, line, kind, qualified):
    """Record EVERY definition of a name, not just the first.

    v1 kept one location per symbol and silently discarded the rest, so a
    finding naming `validate` where two modules define it was linked to
    whichever the backend happened to return first — an answer that depended on
    filesystem order and came with no warning. Ambiguity is now data, and the
    linker refuses to guess.
    """
    loc = {"path": path, "line": int(line) if line else None,
           "kind": kind or "", "qualified_name": qualified}
    bucket = out.setdefault(name, [])
    if not any(b["path"] == loc["path"] and b["line"] == loc["line"] for b in bucket):
        bucket.append(loc)


def _repo_commit(repo):
    """Provenance, so a stale index can be detected rather than trusted."""
    try:
        import subprocess
        r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# graphify
# --------------------------------------------------------------------------- #


def from_graphify(repo: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Read graphify's graph.json.

    Node shape observed at graphify 0.9.28:
        label, norm_label, source_file, source_location, community,
        community_name, file_type, id, _origin

    Every field is read with `.get`, because a young project is allowed to
    change its mind and this should degrade rather than crash.
    """
    path = os.path.join(repo, "graphify-out", "graph.json")
    if not os.path.exists(path):
        return {}, f"no graph at {path}"
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unreadable graph.json: {type(exc).__name__}"

    nodes = data.get("nodes")
    if nodes is None and isinstance(data.get("graph"), dict):
        nodes = data["graph"].get("nodes")
    if not isinstance(nodes, list):
        return {}, "graph.json has no node list where expected"

    out: Dict[str, str] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        label = (n.get("label") or n.get("name") or "").strip()
        label = label.rstrip("()").lstrip(".")
        # NOT `label in out`: skipping a name already seen dropped every
        # duplicate definition, so the graphify backend still resolved
        # homonyms by node order — exactly the defect 0.3.0 claimed to remove.
        if not label:
            continue
        src = n.get("source_file") or n.get("source") or n.get("file") or ""
        loc = n.get("source_location") or n.get("line")
        if not src:
            continue
        # `norm_label` is the only qualifying hint graphify exposes; when it
        # adds nothing, record None rather than repeating the bare name and
        # calling it qualified.
        norm = (n.get("norm_label") or "").strip()
        qualified = norm if norm and norm != label and "." in norm else None
        _add(out, label, str(src), loc, n.get("file_type") or "", qualified)
    return out, None


# --------------------------------------------------------------------------- #
# ctags
# --------------------------------------------------------------------------- #


def from_ctags(repo: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Read a universal-ctags `tags` file.

    Generate one with:
        ctags -R --fields=+n -f tags .
    The `+n` field is what carries the line number; without it the map still
    works but points at a file rather than a line.
    """
    path = os.path.join(repo, "tags")
    if not os.path.exists(path):
        return {}, f"no tags file at {path} (ctags -R --fields=+n -f tags .)"
    out: Dict[str, str] = {}
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("!_TAG_"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                name, fname = parts[0], parts[1]
                m = re.search(r"line:(\d+)", line)
                k = re.search(r"\bkind:(\w+)", line)
                # universal-ctags exposes scope through class:/struct:/
                # namespace:/scope:. Without one, there is nothing to qualify
                # with, and None says so honestly.
                sc = re.search(r"\b(?:class|struct|namespace|scope|module):([\w.]+)", line)
                _add(out, name, fname, m.group(1) if m else None,
                     k.group(1) if k else "",
                     f"{sc.group(1)}.{name}" if sc else None)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unreadable tags: {type(exc).__name__}"
    return out, None


# --------------------------------------------------------------------------- #
# built-in scan
# --------------------------------------------------------------------------- #

#: One definition pattern per language family. Intentionally shallow: the goal is
#: "where is this name defined", not a parse tree. Anything needing more should
#: use ctags or graphify.
_DEF_PATTERNS = (
    (".py", re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")),
    (".js", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$]\w*)")),
    (".ts", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+([A-Za-z_$]\w*)")),
    (".tsx", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$]\w*)")),
    (".go", re.compile(r"^\s*(?:func|type)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
    (".rs", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl)\s+([A-Za-z_]\w*)")),
    (".java", re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")),
    (".rb", re.compile(r"^\s*(?:def|class|module)\s+([A-Za-z_][\w?!]*)")),
    (".php", re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:function|class|trait|interface)\s+([A-Za-z_]\w*)")),
    (".c", re.compile(r"^[A-Za-z_][\w\s\*]*\s+\*?([A-Za-z_]\w*)\s*\([^;]*$")),
    (".h", re.compile(r"^[A-Za-z_][\w\s\*]*\s+\*?([A-Za-z_]\w*)\s*\([^;]*$")),
    (".cpp", re.compile(r"^\s*(?:class|struct)\s+([A-Za-z_]\w*)")),
    (".sql", re.compile(r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:table|view|function|index|materialized\s+view)\s+(?:if\s+not\s+exists\s+)?[\"`\[]?([A-Za-z_]\w*)")),
)

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", "vendor", "target", ".next", "graphify-out",
}


def from_scan(repo: str, max_files: int = 20000) -> Tuple[Dict[str, str], Optional[str]]:
    """Grep definitions straight out of the tree. Always available."""
    by_ext = dict(_DEF_PATTERNS)
    out: Dict[str, str] = {}
    seen = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = os.path.splitext(fn)[1]
            rx = by_ext.get(ext)
            if rx is None:
                continue
            seen += 1
            if seen > max_files:
                return out, f"stopped at {max_files} files"
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repo)
            try:
                with open(full, errors="replace") as fh:
                    module = os.path.splitext(os.path.basename(fn))[0]
                    for i, line in enumerate(fh, 1):
                        m = rx.match(line)
                        if m:
                            _add(out, m.group(1), rel, i, ext.lstrip("."),
                                 f"{module}.{m.group(1)}")
            except Exception:  # noqa: BLE001 - an unreadable file is not fatal
                continue
    return out, None


BACKENDS = {"graphify": from_graphify, "ctags": from_ctags, "scan": from_scan}


def build(repo: str, backend: str = "auto") -> Tuple[Dict[str, str], str, list]:
    """Return (symbols, backend_used, notes)."""
    notes = []
    order = ["graphify", "ctags", "scan"] if backend == "auto" else [backend]
    for name in order:
        fn = BACKENDS.get(name)
        if fn is None:
            notes.append(f"unknown backend {name!r}")
            continue
        syms, err = fn(repo)
        if syms:
            return syms, name, notes
        if err:
            notes.append(f"{name}: {err}")
    return {}, "none", notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a symbol -> file:line map.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--backend", default="auto", choices=["auto", "graphify", "ctags", "scan"])
    ap.add_argument("--out", default="symbols.json")
    args = ap.parse_args()

    syms, used, notes = build(os.path.abspath(args.repo), args.backend)
    for n in notes:
        print(f"  note: {n}", file=sys.stderr)
    if not syms:
        print("no symbols found by any backend", file=sys.stderr)
        return 1
    total = sum(len(v) for v in syms.values())
    ambiguous = sum(1 for v in syms.values() if len(v) > 1)
    repo_abs = os.path.abspath(args.repo)
    vcs, commit, dirty = repo_state(repo_abs)
    fp, counted, fp_warnings = fingerprint(repo_abs, ignore_files=[args.out])
    config = {"backend": used, "exclude": sorted(_SKIP_DIRS), "max_files": 20000}
    partial = bool(fp_warnings) or any("stopped at" in n for n in notes)
    with open(args.out, "w") as fh:
        json.dump({
            "schema_version": 3,
            "tracelink_version": "0.4.0",
            # `root` is logical on purpose: an absolute path would make the index
            # unusable from another checkout and leak the author's filesystem.
            "repository": {"root": ".", "vcs": vcs, "commit": commit,
                           "dirty": dirty, "fingerprint": fp,
                           "files_fingerprinted": counted},
            "indexing": {"backend": used, "backend_version": None,
                         "partial": partial,
                         "warnings": fp_warnings + [{"code": "backend-note", "message": n}
                                                    for n in notes],
                         "configuration": config,
                         "configuration_fingerprint": config_fingerprint(config)},
            "symbols": syms,
        }, fh, indent=1)
    print(f"{len(syms)} names, {total} definitions via {used} -> {args.out}")
    print(f"  fingerprint {fp[:19]}...  {counted} files"
          + (f"  commit {commit[:12]}" if commit else "  (not a git repository)")
          + ("  DIRTY" if dirty else ""))
    if partial:
        print("  index is PARTIAL — it does not represent the whole repository")
    if ambiguous:
        print(f"  {ambiguous} name(s) defined in more than one place — "
              f"the linker will not guess between them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
