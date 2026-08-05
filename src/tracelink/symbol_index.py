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


def fingerprint(repo, exclude=None, ignore_files=None, files=None):
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
    if files is not None:
        # SYMBOL-INDEX freshness, not repository freshness. Hashing the whole
        # tree made a README, a CHANGELOG or tracelink's own vault mark the
        # index stale — none of which can change a symbol map. The question is
        # "did anything that feeds the index change", and only the files the
        # backend actually read can answer it.
        root = os.path.realpath(repo)
        records, warnings, counted = [], [], 0
        for rel in sorted({f.replace(os.sep, "/") for f in files}):
            full = os.path.realpath(os.path.join(root, rel))
            if os.path.commonpath([root, full]) != root:
                warnings.append({"code": "path-outside-repo", "path": rel})
                continue
            try:
                with open(full, "rb") as fh:
                    records.append(f"{rel}\0{hashlib.sha256(fh.read()).hexdigest()}\n")
                counted += 1
            except Exception as exc:  # noqa: BLE001
                warnings.append({"code": "file-read-error", "path": rel,
                                 "message": type(exc).__name__})
        h = hashlib.sha256("".join(sorted(records)).encode("utf-8")).hexdigest()
        return f"sha256:{h}", counted, warnings
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



def discover_scope(repo, scope):
    """Recompute the set of files a backend would consider, right now.

    Persisting only the previous file list is not enough: a source file ADDED
    after indexing would never be hashed, so the fingerprint would match and the
    index would be called fresh while a new symbol sat unindexed. The scope has
    to be re-derived, not replayed.

    Returns (files, confidence) where confidence is "exact" when the scope can
    be rebuilt faithfully and "unknown" when it cannot. The three backends do
    not have equal powers here and pretending otherwise would be the same class
    of overclaim this project keeps removing:

      scan      rebuilt exactly from extensions and excludes
      ctags     only as good as the `tags` file on disk right now
      graphify  only as good as `graphify-out/graph.json` right now
    """
    kind = (scope or {}).get("kind")
    root = os.path.realpath(repo)
    if kind == "extensions":
        exts = set(scope.get("extensions") or [])
        skip = set(scope.get("exclude") or []) | set(_SKIP_DIRS)
        files = []
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in names:
                if os.path.splitext(fn)[1] in exts:
                    files.append(os.path.relpath(os.path.join(dirpath, fn), root)
                                 .replace(os.sep, "/"))
        return sorted(files), "exact"
    if kind == "ctags":
        syms, err, considered = from_ctags(root)
        return (considered, "exact") if not err else ([], "unknown")
    if kind == "graphify":
        syms, err, considered = from_graphify(root)
        return (considered, "exact") if not err else ([], "unknown")
    return [], "unknown"


_LINE_LOCATION_RE = re.compile(
    r"^[Ll]?\s*(\d+)"
    r"(?:\s*(?:-|–|—|\.\.)\s*[Ll]?\s*\d+)?$"
)


def _line_number(value):
    """Return the first line from backend location formats, or ``None``.

    Graphify has emitted both JSON numbers and display-oriented strings such
    as ``L88`` and ``L88-L94``. The symbol schema stores one anchor line, so a
    range is represented by its first line. Unknown shapes fail open rather
    than crashing the whole index or guessing a number from arbitrary text.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if not isinstance(value, str):
        return None
    match = _LINE_LOCATION_RE.fullmatch(value.strip())
    if not match:
        return None
    line = int(match.group(1))
    return line if line > 0 else None


def _add(out, name, path, line, kind, qualified):
    """Record EVERY definition of a name, not just the first.

    v1 kept one location per symbol and silently discarded the rest, so a
    finding naming `validate` where two modules define it was linked to
    whichever the backend happened to return first — an answer that depended on
    filesystem order and came with no warning. Ambiguity is now data, and the
    linker refuses to guess.
    """
    loc = {"path": path, "line": _line_number(line),
           "kind": kind or "", "qualified_name": qualified}
    bucket = out.setdefault(name, [])
    if not any(b["path"] == loc["path"] and b["line"] == loc["line"] for b in bucket):
        bucket.append(loc)


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
        return {}, f"no graph at {path}", []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unreadable graph.json: {type(exc).__name__}", []

    nodes = data.get("nodes")
    if nodes is None and isinstance(data.get("graph"), dict):
        nodes = data["graph"].get("nodes")
    if not isinstance(nodes, list):
        return {}, "graph.json has no node list where expected", []

    out: Dict[str, str] = {}
    considered = set()
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
        considered.add(str(src))
    return out, None, sorted(considered)


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
        return {}, f"no tags file at {path} (ctags -R --fields=+n -f tags .)", []
    out: Dict[str, str] = {}
    considered = set()
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
                considered.add(fname)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unreadable tags: {type(exc).__name__}", []
    return out, None, sorted(considered)


# --------------------------------------------------------------------------- #
# built-in scan
# --------------------------------------------------------------------------- #

#: One definition pattern per language family. Intentionally shallow: the goal is
#: "where is this name defined", not a parse tree. Anything needing more should
#: use ctags or graphify.
_DEF_PATTERNS = (
    (".py", re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")),
    # JS/TS also index `export const/let/var NAME = ...` and `export default
    # function NAME()`. These are the dominant definition forms in modern
    # Next.js code, and the 0.7.0 benchmark on a real repository
    # (.dev/benchmark-todi-2026-08-05.md) showed the scan missing getImmobili,
    # DEFAULT_TIMERS and robots for exactly this reason — silently costing
    # link rate, hotspots and consult notes downstream. Anonymous default
    # exports stay out (nothing to name); non-exported const/let/var stay out
    # too, deliberately, or every local binding would drown the map.
    (".js", re.compile(r"^\s*(?:(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class)|export\s+(?:const|let|var))\s+([A-Za-z_$]\w*)")),
    (".ts", re.compile(r"^\s*(?:(?:export\s+(?:default\s+)?)?(?:abstract\s+)?(?:async\s+)?(?:function|class|interface|type|enum)|export\s+(?:const|let|var)(?:\s+enum)?)\s+([A-Za-z_$]\w*)")),
    (".tsx", re.compile(r"^\s*(?:(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|interface|type)|export\s+(?:const|let|var)(?:\s+enum)?)\s+([A-Za-z_$]\w*)")),
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
    considered = []
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
                return out, f"max-files-reached at {max_files}", sorted(considered)
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            considered.append(rel)
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
    return out, None, sorted(considered)


BACKENDS = {"graphify": from_graphify, "ctags": from_ctags, "scan": from_scan}


def build(repo: str, backend: str = "auto"):
    """Return (symbols, backend_used, notes)."""
    notes = []
    order = ["graphify", "ctags", "scan"] if backend == "auto" else [backend]
    for name in order:
        fn = BACKENDS.get(name)
        if fn is None:
            notes.append(f"unknown backend {name!r}")
            continue
        syms, err, considered = fn(repo)
        # The note is recorded BEFORE the early return. Returning as soon as a
        # backend produced symbols discarded it, so a scan truncated at the file
        # limit reported `partial: false` — a completeness claim that was simply
        # untrue.
        if err:
            notes.append(f"{name}: {err}")
        if syms:
            return syms, name, notes, considered
    return {}, "none", notes, []


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a symbol -> file:line map.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--backend", default="auto", choices=["auto", "graphify", "ctags", "scan"])
    ap.add_argument("--out", default="symbols.json")
    args = ap.parse_args()

    syms, used, notes, considered = build(os.path.abspath(args.repo), args.backend)
    for n in notes:
        print(f"  note: {n}", file=sys.stderr)
    if not syms:
        print("no symbols found by any backend", file=sys.stderr)
        return 1
    total = sum(len(v) for v in syms.values())
    ambiguous = sum(1 for v in syms.values() if len(v) > 1)
    repo_abs = os.path.abspath(args.repo)
    vcs, commit, dirty = repo_state(repo_abs)
    fp, counted, fp_warnings = fingerprint(repo_abs, files=considered)
    scope = ({"kind": "extensions",
              "extensions": sorted({e for e, _rx in _DEF_PATTERNS}),
              "exclude": sorted(_SKIP_DIRS)} if used == "scan"
             else {"kind": used})
    config = {"backend": used, "exclude": sorted(_SKIP_DIRS), "max_files": 20000}
    if used == "scan":
        # The regexes ARE the scan's configuration: change them and the same
        # tree yields a different symbol map. Folding them into the fingerprint
        # is what lets an index built with older patterns say "regenerate me"
        # instead of passing for equivalent.
        config["patterns"] = {ext: rx.pattern for ext, rx in _DEF_PATTERNS}
    partial = bool(fp_warnings) or any("max-files-reached" in n for n in notes)
    with open(args.out, "w") as fh:
        json.dump({
            "schema_version": 3,
            "tracelink_version": "0.4.2",
            # `root` is logical on purpose: an absolute path would make the index
            # unusable from another checkout and leak the author's filesystem.
            "repository": {"root": ".", "vcs": vcs, "commit": commit,
                           "dirty": dirty, "fingerprint": fp,
                           "files_fingerprinted": counted,
                           "scope": "symbol-index"},
            "indexing": {"backend": used, "backend_version": None,
                         "partial": partial,
                         # The scope descriptor is what makes the fingerprint
                         # reproducible by the linker. Without it the verifier
                         # hashed a different set than the indexer did, and a
                         # freshly written index came out stale immediately.
                         "scope": scope,
                         "files_considered": considered,
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
