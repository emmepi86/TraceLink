#!/usr/bin/env python3
"""F4 — which coordinate systems does TraceLink actually accept?

Benchmark 01 found a symbol graph recording paths relative to the
repository root, while the artefact's own location forced `--repo` one level
down. Every path therefore resolved to nothing, 104 links were written
anyway, and the only signal was `index_completeness: partial`, which is
about something else.

"104 broken paths" is an anecdote. The question worth answering is which
combinations of *artefact location* and *path convention* TraceLink
understands, which it rejects, and which it accepts while producing anchors
that point at no code. This walks the matrix and reports, for each cell:

    emitted        how many locations the index recorded
    resolve        how many of those paths exist under --repo
    line ok        how many of those lines hold the symbol they name
    linked         how many links `link` wrote from them
    signal         what TraceLink said about it, if anything

It measures; it does not assert, and it fixes nothing.

    python3 benchmarks/trust/f4_path_bases.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from tracelink import linker, splitter, symbol_index  # noqa: E402

FILES = 20
FUNCS = 5


def build_tree(root):
    """A repository with its code under `app/`, as many projects have."""
    truth = {}
    for index in range(FILES):
        relative = os.path.join("app", "pkg", f"mod{index:03d}.py")
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            line = 1
            for number in range(FUNCS):
                name = f"sym_{index:03d}_{number:02d}"
                fh.write(f"def {name}(value):\n    return value\n\n\n")
                truth[name] = (relative, line)
                line += 4
    return truth


def write_graph(directory, truth, convention, root):
    """graph.json in `directory`, with paths written `convention`-style."""
    os.makedirs(os.path.join(directory, "graphify-out"), exist_ok=True)
    nodes = []
    for name, (relative, line) in truth.items():
        if convention == "root-relative":
            path = relative
        elif convention == "artefact-relative":
            path = os.path.relpath(os.path.join(root, relative), directory)
        elif convention == "absolute":
            path = os.path.join(root, relative)
        else:
            raise ValueError(convention)
        nodes.append({"label": name, "source_file": path,
                      "source_location": line, "file_type": "py"})
    with open(os.path.join(directory, "graphify-out", "graph.json"),
              "w") as fh:
        json.dump({"nodes": nodes}, fh)


def evaluate(repo_arg, symbols_path, truth):
    with open(symbols_path) as fh:
        index = json.load(fh)
    symbols = index.get("symbols") or {}
    emitted = resolve = line_ok = 0
    for name, locations in symbols.items():
        location = locations[0] if isinstance(locations, list) else {}
        path, line = location.get("path"), location.get("line")
        if not path:
            continue
        emitted += 1
        full = path if os.path.isabs(path) else os.path.join(repo_arg, path)
        if not os.path.exists(full):
            continue
        resolve += 1
        try:
            with open(full) as fh:
                lines = fh.readlines()
        except OSError:
            continue
        if line and name in "".join(lines[max(0, line - 2):line + 1]):
            line_ok += 1
    return emitted, resolve, line_ok, (index.get("indexing") or {})


def run_cell(scratch, label, artefact_at, convention):
    root = os.path.join(scratch, label.replace(" ", "_"))
    os.makedirs(root)
    truth = build_tree(root)
    directory = {"root": root,
                 "app": os.path.join(root, "app"),
                 "outside": os.path.join(scratch, label + "-outside")}[
                     artefact_at]
    os.makedirs(directory, exist_ok=True)
    write_graph(directory, truth, convention, root)
    # the backend looks for graphify-out/ under --repo, so --repo is
    # wherever the artefact lives — which is exactly how the mismatch arises
    repo_arg = directory if artefact_at != "outside" else root

    symbols_path = os.path.join(root, "symbols.json")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = symbol_index.main(["--repo", repo_arg, "--backend", "graphify",
                                  "--out", symbols_path])
    if code != 0 or not os.path.exists(symbols_path):
        return {"label": label, "emitted": 0, "resolve": 0, "line_ok": 0,
                "linked": 0, "signal": "index refused"}

    emitted, resolve, line_ok, indexing = evaluate(repo_arg, symbols_path,
                                                   truth)
    register = os.path.join(root, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write("# Findings\n\n")
        for number, name in enumerate(sorted(truth)[:20], start=1):
            fh.write(f"## F4-{number:03d} — a finding [MEDIUM]\n"
                     f"### STATUS: OPEN\n\n`{name}` is named here.\n\n")
    vault = os.path.join(root, "vault")
    with contextlib.redirect_stdout(io.StringIO()):
        splitter.main(["--register", register, "--out", vault,
                       "--prefix", "F4"])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        linker.main(["--vault", vault, "--symbols", symbols_path,
                     "--repo", repo_arg, "--format", "json"])
    try:
        report = json.loads(buffer.getvalue())
        linked = (report.get("linking") or {}).get("symbols_linked", 0)
    except ValueError:
        linked = 0
    signal = indexing.get("partial") and "completeness: partial" or "—"
    return {"label": label, "emitted": emitted, "resolve": resolve,
            "line_ok": line_ok, "linked": linked, "signal": signal}


def main():
    scratch = tempfile.mkdtemp(prefix="tracelink-f4-")
    cells = [
        ("artefact at root, paths root-relative", "root", "root-relative"),
        ("artefact at root, paths absolute", "root", "absolute"),
        ("artefact in app/, paths root-relative", "app", "root-relative"),
        ("artefact in app/, paths app-relative", "app", "artefact-relative"),
        ("artefact in app/, paths absolute", "app", "absolute"),
        ("artefact outside the repository", "outside", "root-relative"),
    ]
    try:
        print(__doc__.split("\n\n")[0])
        print()
        print(f"  {'configuration':<40} {'emitted':>8} {'resolve':>8} "
              f"{'line ok':>8} {'linked':>7}  signal")
        for label, where, convention in cells:
            row = run_cell(scratch, label, where, convention)
            print(f"  {row['label']:<40} {row['emitted']:>8} "
                  f"{row['resolve']:>8} {row['line_ok']:>8} "
                  f"{row['linked']:>7}  {row['signal']}")
        print("\n  A row with links but no resolving paths is the shape of "
              "the defect:")
        print("  anchors were asserted against code that is not where they "
              "say it is,")
        print("  and nothing failed closed.")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
