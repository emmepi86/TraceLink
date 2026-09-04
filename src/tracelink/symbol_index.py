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

def _git(repo, *args, raw=False):
    """Ask git, without letting the question change the repository.

    `--no-optional-locks` is not decoration: `git status` refreshes the
    index by default, which writes inside `.git`. A tool that promises to
    only read the repository it observes must not leave a trace in it, and
    a benchmark that measures the read must not be measuring a write.
    """
    try:
        import subprocess
        r = subprocess.run(["git", "--no-optional-locks", "-C", repo, *args],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return r.stdout if raw else r.stdout.strip()
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



def _sha_text(text):
    import hashlib
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def in_scope(name, scope):
    """Would the indexer consider this path? Same rule as `discover_scope`,
    applied to a name instead of to a walk."""
    if (scope or {}).get("kind") != "extensions":
        return False
    if os.path.splitext(name)[1] not in set(scope.get("extensions") or []):
        return False
    skip = set(scope.get("exclude") or []) | set(_SKIP_DIRS)
    parts = name.split("/")[:-1]
    return not any(part in skip or part.startswith(".") for part in parts)


def tracked_count(repo):
    """How many files git tracks, or None. One cheap call, used to decide
    whether asking git about everything is cheaper than hashing the scope."""
    listing = _git(repo, "ls-files", raw=True)
    return None if listing is None else len(listing.splitlines())


def git_evidence(repo, scope):
    """What git can honestly witness about the scope right now, or None.

    None means "ask the filesystem instead". It is returned whenever git
    is absent, or present but a weaker observer than it looks:

      * `assume-unchanged` / `skip-worktree` — flags whose entire purpose is
        to make git stop noticing changes to a file. A fast path over those
        would report `fresh` about a file nobody is watching.
      * sparse checkout — the working tree does not contain what the index
        says it does.
      * an unborn or detached-without-commit HEAD — nothing to compare with.

    The candidate set is built from git's own inventory — tracked, untracked
    AND ignored — then filtered by the indexer's scope rule. Ignored matters:
    a generated file git hides is a file TraceLink may well index, and a
    check that never looked at it would miss it appearing.
    """
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    if not tree:
        return None
    if (_git(repo, "config", "--get", "core.sparseCheckout") or "").lower() \
            == "true":
        return None

    listing = _git(repo, "ls-files", "-v", raw=True)
    if listing is None:
        return None
    tracked, unobservable = [], []
    for line in listing.splitlines():
        if len(line) < 3 or line[1] != " ":
            continue
        flag, name = line[0], line[2:]
        # lowercase = assume-unchanged, S = skip-worktree
        if flag.islower() or flag == "S":
            unobservable.append(name)
        tracked.append(name)
    if any(in_scope(name, scope) for name in unobservable):
        return None

    others = _git(repo, "ls-files", "--others", "--exclude-standard",
                  raw=True) or ""
    # Not `--directory`: collapsing a wholly-ignored tree to one entry makes
    # the enumeration cheap and the guarantee false — the collapsed entry has
    # no extension, so an ignored FILE the indexer reads vanishes from the
    # candidate set and its appearance stops being detectable. Measured and
    # reverted; the differential test caught it.
    ignored = _git(repo, "ls-files", "--others", "--ignored",
                   "--exclude-standard", raw=True) or ""
    candidates = sorted({name for name in
                         tracked + others.splitlines() + ignored.splitlines()
                         if in_scope(name, scope)})

    # `status`, not `diff-index`: the latter trusts stat information, so a
    # file whose mtime moved but whose bytes did not comes back as changed —
    # measured, and it would send the common case (a checkout, a touch) down
    # the slow path for nothing. `status` compares the content. Untracked
    # files are excluded from the walk because their names are already known
    # from `ls-files --others` above.
    changed = {line[3:].strip().strip('"')
               for line in (_git(repo, "status", "--porcelain=v1",
                                 "--untracked-files=no", raw=True)
                            or "").splitlines() if line.strip()}
    scope_dirty = sorted(name for name in changed if in_scope(name, scope))
    tracked_set = set(tracked)
    # Anything in scope that git does not track cannot be vouched for by the
    # tree identity, however clean the repository looks — so it is read.
    # This is the whole cost of the fast path: not every indexed file, only
    # the ones git cannot speak for. On a repository whose sources are all
    # committed, that is nothing at all.
    outside_git = [name for name in candidates if name not in tracked_set]
    import hashlib
    digest = hashlib.sha256()
    for name in outside_git:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        try:
            with open(os.path.join(repo, name), "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"\0unreadable\0")
        digest.update(b"\n")
    return {"tree_identity": tree,
            "names_fingerprint": _sha_text("\n".join(candidates)),
            "outside_git_fingerprint": "sha256:" + digest.hexdigest(),
            "candidates": candidates,
            "dirty_in_scope": scope_dirty,
            "untracked_in_scope": outside_git}


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
        syms, err, considered, _prov = from_ctags(root)
        return (considered, "exact") if not err else ([], "unknown")
    if kind == "graphify":
        syms, err, considered, _prov = from_graphify(root)
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


#: Node types a backend uses for prose rather than for code. Graphify marks
#: module and function docstrings `rationale`, and 3531 of 11911 nodes in
#: one real graph were exactly that: sentences ingested as symbol names.
#: The backend's own type is the authority here — inferring "this looks like
#: a docstring" from the string would be a heuristic, and this is a fact the
#: producer already recorded.
_PROSE_NODE_TYPES = frozenset({"rationale", "doc", "docstring", "comment",
                               "text", "prose"})

#: The lexical backstop, for backends that type nothing. An identifier does
#: not contain whitespace and is not a paragraph. Deliberately loose about
#: everything else: `operator<<`, `foo?`, `$scope` and `Class::method` are
#: identifiers in some language, and a filter that only admits `\w+` would
#: quietly drop them.
_MAX_IDENTIFIER = 128


def looks_like_identifier(name):
    """Could this be the name of something, in any language?

    Two rules, both structural. Whitespace: no language writes a definition
    whose name contains a space or a newline outside quotes, and no backend
    here emits quoted names. Length: 128 characters is far past the longest
    identifier anyone has written on purpose and far short of a sentence.
    """
    if not name or len(name) > _MAX_IDENTIFIER:
        return False
    return not any(ch.isspace() for ch in name)


def _add(out, name, path, line, kind, qualified):
    """Record EVERY definition of a name, not just the first.

    v1 kept one location per symbol and silently discarded the rest, so a
    finding naming `validate` where two modules define it was linked to
    whichever the backend happened to return first — an answer that depended on
    filesystem order and came with no warning. Ambiguity is now data, and the
    linker refuses to guess.
    """
    if not looks_like_identifier(name):
        # Whatever this is, it is not the name of something a finding can
        # name. The caller counts the refusals and says how many.
        return False
    loc = {"path": path, "line": _line_number(line),
           "kind": kind or "", "qualified_name": qualified}
    bucket = out.setdefault(name, [])
    if not any(b["path"] == loc["path"] and b["line"] == loc["line"] for b in bucket):
        bucket.append(loc)
    return True


#: Keys a backend artefact may use to say which repository state it was
#: generated from. A commit or a tree hash can be checked against the
#: repository; a timestamp cannot, and is kept as diagnosis only.
_PROVENANCE_COMMIT_KEYS = ("commit", "source_commit", "repo_commit",
                           "revision", "sha")
_PROVENANCE_FINGERPRINT_KEYS = ("tree_hash", "source_fingerprint",
                                "fingerprint")
_PROVENANCE_TIME_KEYS = ("generated_at", "created_at", "timestamp")

#: The scan backend reads the repository itself: there is no artefact in
#: between that could be older than the code it describes.
_WORKING_TREE = {"kind": "working-tree", "artifact": None,
                 "source_commit": None, "source_fingerprint": None,
                 "generated_at": None, "artifact_mtime": None}


def _artifact_provenance(path, metadata=None):
    """What an artefact says about the repository state it describes.

    `state` is decided later, by comparing with the repository; this only
    reports what the artefact carries. An artefact that carries nothing is
    not evidence of currency, and saying so is the point.
    """
    out = {"kind": "artifact",
           "artifact": path,
           "source_commit": None,
           "source_fingerprint": None,
           "generated_at": None}
    try:
        out["artifact_mtime"] = int(os.path.getmtime(path))
    except OSError:
        out["artifact_mtime"] = None
    for key, value in (metadata or {}).items():
        if not isinstance(value, (str, int)):
            continue
        lowered = key.lower()
        if lowered in _PROVENANCE_COMMIT_KEYS and not out["source_commit"]:
            out["source_commit"] = str(value)
        elif (lowered in _PROVENANCE_FINGERPRINT_KEYS
                and not out["source_fingerprint"]):
            out["source_fingerprint"] = str(value)
        elif lowered in _PROVENANCE_TIME_KEYS and not out["generated_at"]:
            out["generated_at"] = str(value)
    return out


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
        return {}, f"no graph at {path}", [], None
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unreadable graph.json: {type(exc).__name__}", [], None

    nodes = data.get("nodes")
    if nodes is None and isinstance(data.get("graph"), dict):
        nodes = data["graph"].get("nodes")
    if not isinstance(nodes, list):
        return {}, "graph.json has no node list where expected", [], None

    out: Dict[str, str] = {}
    considered = set()
    skipped_prose = skipped_labels = 0
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
        if str(n.get("file_type") or "").lower() in _PROSE_NODE_TYPES:
            # A docstring is not a symbol. The producer said so; believe it.
            skipped_prose += 1
            continue
        # `norm_label` is the only qualifying hint graphify exposes; when it
        # adds nothing, record None rather than repeating the bare name and
        # calling it qualified.
        norm = (n.get("norm_label") or "").strip()
        qualified = norm if norm and norm != label and "." in norm else None
        if not _add(out, label, str(src), loc, n.get("file_type") or "",
                    qualified):
            skipped_labels += 1
            continue
        considered.add(str(src))
    graph_meta = data.get("graph") if isinstance(data.get("graph"), dict) else {}
    note = None
    if skipped_prose or skipped_labels:
        parts = []
        if skipped_prose:
            parts.append(f"{skipped_prose} prose node(s) the graph itself "
                         f"types as documentation")
        if skipped_labels:
            parts.append(f"{skipped_labels} label(s) that are not identifiers")
        note = "ignored " + " and ".join(parts)
    return out, note, sorted(considered), _artifact_provenance(path, graph_meta)


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
        return {}, f"no tags file at {path} (ctags -R --fields=+n -f tags .)", [], None
    out: Dict[str, str] = {}
    considered = set()
    pseudo: Dict[str, str] = {}
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("!_TAG_"):
                    # Read the header rather than skipping it: if a generator
                    # ever records which repository state it described, this
                    # is where it would say so.
                    header = line.rstrip("\n").split("\t")
                    if len(header) >= 2:
                        pseudo[header[0][6:].strip().lower()] = header[1].strip()
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
        return {}, f"unreadable tags: {type(exc).__name__}", [], None
    # universal-ctags records its own version in the pseudo-tags and
    # nothing about the repository, so `pseudo` will not usually carry a
    # commit. That is the answer, not a gap to fill with a guess.
    return out, None, sorted(considered), _artifact_provenance(path, pseudo)


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
                return (out, f"max-files-reached at {max_files}",
                        sorted(considered), _WORKING_TREE)
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
    return out, None, sorted(considered), _WORKING_TREE


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
        syms, err, considered, provenance = fn(repo)
        # The note is recorded BEFORE the early return. Returning as soon as a
        # backend produced symbols discarded it, so a scan truncated at the file
        # limit reported `partial: false` — a completeness claim that was simply
        # untrue.
        if err:
            notes.append(f"{name}: {err}")
        if syms:
            return syms, name, notes, considered, provenance
    return {}, "none", notes, [], None


def verify_upstream(provenance, repo):
    """Is the evidence this index was built FROM current with the repository?

    A different question from "is the index current", and the reason both
    exist. `scan` reads the tree, so its evidence cannot be older than the
    tree. A backend artefact can be any age, and only a commit or a tree
    fingerprint it records can settle it: a timestamp cannot be compared
    with anything reliable, so it is carried as diagnosis and decides
    nothing.

    No provenance means `unknown`, never `verified`. An index may be
    perfectly usable and still not be evidence that it is current — those
    are separate claims, and rounding the second one up is exactly the
    failure this function exists to prevent.
    """
    if not isinstance(provenance, dict):
        return {"state": "unknown", "reason": "backend-reported-no-provenance"}
    out = dict(provenance)
    if provenance.get("kind") == "working-tree":
        out.update({"state": "verified",
                    "reason": "backend-reads-the-working-tree"})
        return out

    commit = provenance.get("source_commit")
    fingerprint = provenance.get("source_fingerprint")
    if commit:
        _vcs, current, dirty = repo_state(repo)
        out["repository_commit"] = current
        if current is None:
            out.update({"state": "unknown", "reason": "repository-has-no-vcs"})
        elif commit != current:
            out.update({"state": "stale", "reason": "source-commit-differs"})
        elif dirty:
            # The artefact names the commit we are on, but the tree has
            # uncommitted edits it could not have seen. That is not evidence
            # of divergence, only absence of evidence of correspondence —
            # and `stale` is a claim this cannot support.
            out.update({"state": "unknown",
                        "reason": "working-tree-modified-since-that-commit"})
        else:
            out.update({"state": "verified", "reason": "source-commit-matches"})
        return out
    if fingerprint:
        out.update({"state": "unknown",
                    "reason": "source-fingerprint-not-comparable"})
        return out
    out.update({"state": "unknown",
                "reason": "artifact-records-no-repository-provenance"})
    return out


def validate_locations(symbols, repo, considered=()):
    """Drop every location that does not name a file inside `repo`.

    The ingestion boundary, and the only place coordinates are checked. A
    backend can record paths in whatever base it likes; what TraceLink may
    not do is assert an anchor against a path it cannot find. Benchmark 01
    (F4) linked 104 symbols to locations that resolved to nothing, because
    the artefact's paths were relative to the repository root while the
    artefact's own position forced `--repo` one level below it.

    **Nothing is guessed.** A path that would resolve against the parent
    directory, against the artefact's directory, or by matching a suffix is
    still invalid here: a coordinate error must not be repaired into a
    coordinate heuristic, or the next mismatch resolves silently to the
    wrong file. The fix for a mismatch is to point `--repo` at the base the
    artefact actually uses.

    Returns (kept symbols, kept considered, report).
    """
    root = os.path.realpath(repo)
    verdicts = {}          # path -> bool, so a file shared by 40 symbols is
                           # stat'd once

    def usable(path):
        if path in verdicts:
            return verdicts[path]
        full = path if os.path.isabs(path) else os.path.join(root, path)
        resolved = os.path.realpath(full)
        inside = (resolved == root
                  or resolved.startswith(root + os.sep))
        verdicts[path] = inside and os.path.isfile(resolved)
        return verdicts[path]

    kept, checked, rejected = {}, 0, 0
    examples = []
    for name, locations in symbols.items():
        surviving = []
        for location in locations:
            checked += 1
            path = location.get("path")
            if isinstance(path, str) and usable(path):
                surviving.append(location)
                continue
            rejected += 1
            if len(examples) < 5:
                examples.append(path)
        if surviving:
            kept[name] = surviving

    report = {"state": "ok" if not rejected else "invalid",
              "checked_locations": checked,
              "invalid_locations": rejected,
              "examples": examples}
    if rejected:
        report["reason"] = ("paths do not name a file inside --repo; the "
                            "backend may be recording them against another "
                            "base")
    kept_considered = [path for path in considered if usable(path)]
    return kept, kept_considered, report


def main(argv=None, prog=None) -> int:
    ap = argparse.ArgumentParser(
        prog=prog, description="Build a symbol -> file:line map.")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--backend", default="auto", choices=["auto", "graphify", "ctags", "scan"])
    ap.add_argument("--out", default="symbols.json")
    args = ap.parse_args(argv)

    syms, used, notes, considered, provenance = build(os.path.abspath(args.repo),
                                                      args.backend)
    for n in notes:
        print(f"  note: {n}", file=sys.stderr)
    if not syms:
        print("no symbols found by any backend", file=sys.stderr)
        return 1

    repo_root = os.path.abspath(args.repo)
    syms, considered, integrity = validate_locations(syms, repo_root,
                                                     considered)
    if integrity["invalid_locations"]:
        print(f"  note: {integrity['invalid_locations']} of "
              f"{integrity['checked_locations']} locations do not name a file "
              f"inside {args.repo} and were dropped", file=sys.stderr)
        for example in integrity["examples"]:
            print(f"    e.g. {example}", file=sys.stderr)
    if not syms:
        # Every coordinate the backend produced was unusable. That is a
        # configuration answer, not an empty repository: say so, and do not
        # write an index whose every anchor would point at nothing.
        print(f"every location the {used} backend produced falls outside "
              f"{args.repo} — check that --repo names the base those paths "
              f"are relative to", file=sys.stderr)
        return 1
    total = sum(len(v) for v in syms.values())
    ambiguous = sum(1 for v in syms.values() if len(v) > 1)
    repo_abs = os.path.abspath(args.repo)
    evidence = None
    vcs, commit, dirty = repo_state(repo_abs)
    fp, counted, fp_warnings = fingerprint(repo_abs, files=considered)
    scope = ({"kind": "extensions",
              "extensions": sorted({e for e, _rx in _DEF_PATTERNS}),
              "exclude": sorted(_SKIP_DIRS)} if used == "scan"
             else {"kind": used})
    # What git can witness about this scope, recorded so the verifier can
    # decide whether it may skip re-reading the files. None when git is
    # absent or is a weaker observer than it looks; the verifier then has
    # only the content fingerprint, which is what it has always had.
    evidence = git_evidence(repo_abs, scope)
    config = {"backend": used, "exclude": sorted(_SKIP_DIRS), "max_files": 20000}
    if used == "scan":
        # The regexes ARE the scan's configuration: change them and the same
        # tree yields a different symbol map. Folding them into the fingerprint
        # is what lets an index built with older patterns say "regenerate me"
        # instead of passing for equivalent.
        config["patterns"] = {ext: rx.pattern for ext, rx in _DEF_PATTERNS}
    partial = bool(fp_warnings) or any("max-files-reached" in n for n in notes)
    # `--out .tracelink/symbols.json` before anything else created the
    # directory raised FileNotFoundError; the out path is ours to write, so
    # its parent is ours to create.
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump({
            "schema_version": 3,
            "tracelink_version": "0.4.2",
            # `root` is logical on purpose: an absolute path would make the index
            # unusable from another checkout and leak the author's filesystem.
            "repository": {"root": ".", "vcs": vcs, "commit": commit,
                           "dirty": dirty, "fingerprint": fp,
                           "files_fingerprinted": counted,
                           # What the cheap check compares against. Both are
                           # about the SET and the COMMIT, never about
                           # content: the content evidence is `fingerprint`,
                           # and these only say when it can be trusted
                           # without re-reading every byte.
                           "tree_identity": (evidence or {}).get(
                               "tree_identity"),
                           "scope_names_fingerprint": (evidence or {}).get(
                               "names_fingerprint"),
                           # Content of what git cannot vouch for — untracked
                           # and ignored files the indexer nonetheless reads.
                           "outside_git_fingerprint": (evidence or {}).get(
                               "outside_git_fingerprint"),
                           "scope": "symbol-index"},
            "indexing": {"backend": used, "backend_version": None,
                         "partial": partial,
                         # Where this index's EVIDENCE came from, and whether
                         # that evidence is current. Separate from the index's
                         # own freshness: an index built a minute ago from a
                         # month-old artefact is new and out of date at once.
                         "upstream": verify_upstream(provenance,
                                                     os.path.realpath(repo_abs)),
                         # Separate from `partial`, which is about how much
                         # of the repository was covered. This is about
                         # whether the coordinates point at it at all.
                         "path_integrity": integrity,
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
