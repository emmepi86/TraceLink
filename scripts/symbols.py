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
        if not label or label in out:
            continue
        src = n.get("source_file") or n.get("source") or n.get("file") or ""
        loc = n.get("source_location") or n.get("line")
        if not src:
            continue
        out[label] = f"{src}:{loc}" if loc else str(src)
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
                if name in out:
                    continue
                m = re.search(r"line:(\d+)", line)
                out[name] = f"{fname}:L{m.group(1)}" if m else fname
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
                    for i, line in enumerate(fh, 1):
                        m = rx.match(line)
                        if m and m.group(1) not in out:
                            out[m.group(1)] = f"{rel}:L{i}"
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
    with open(args.out, "w") as fh:
        json.dump({"backend": used, "repo": args.repo, "symbols": syms}, fh, indent=1)
    print(f"{len(syms)} symbols via {used} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
