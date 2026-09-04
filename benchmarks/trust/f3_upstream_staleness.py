#!/usr/bin/env python3
"""F3 — an index can be `fresh` and still describe code that has moved.

Benchmark 01 found this on a private repository: a symbol graph generated
36 days before the code it described produced an index TraceLink reported
as `fresh`, with 42% of its line numbers no longer holding the symbol they
named. This is that finding as a repository anyone can rebuild in a second.

The mechanism is not a bug in the freshness check — it is the *scope* of
the check. `link` asks "does this index still describe the repository in
front of me?", and answers by comparing the repository fingerprint recorded
when the index was built with the one now. Build the index this minute from
a month-old artefact and the answer is honestly `fresh`: the index IS new.
What nobody checks is whether the artefact it was derived from still
describes anything.

    the derived index is unchanged   ≠   the evidence behind it is current

which is the distinction TraceLink already makes between an anchor that
still resolves and a finding that is still true, one level further up.

This script measures; it does not assert. Run it, read the table.

    python3 benchmarks/trust/f3_upstream_staleness.py
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

FILES = 40
FUNCS = 10


def write_repo(root, drift=0):
    """`drift` blank lines prepended to every file: same symbols, new lines."""
    for index in range(FILES):
        path = os.path.join(root, f"mod{index:03d}.py")
        with open(path, "w") as fh:
            fh.write("\n" * drift)
            for number in range(FUNCS):
                fh.write(f"def sym_{index:03d}_{number:02d}(value):\n"
                         f"    return value\n\n\n")


def read_symbol_lines(root):
    """{symbol: line} as the code actually is, right now."""
    found = {}
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(root, name)) as fh:
            for number, line in enumerate(fh, start=1):
                if line.startswith("def "):
                    found[line[4:].split("(")[0]] = (name, number)
    return found


def write_graph(root, truth):
    """A graphify-shaped graph.json describing `truth`."""
    out = os.path.join(root, "graphify-out")
    os.makedirs(out, exist_ok=True)
    nodes = [{"label": symbol, "source_file": path, "source_location": line,
              "file_type": "py"} for symbol, (path, line) in truth.items()]
    with open(os.path.join(out, "graph.json"), "w") as fh:
        json.dump({"nodes": nodes}, fh)


def probe(root, symbols_path):
    """How many recorded lines still hold the symbol they name."""
    with open(symbols_path) as fh:
        symbols = json.load(fh).get("symbols") or {}
    correct = wrong = 0
    for name, locations in symbols.items():
        location = locations[0] if isinstance(locations, list) else {}
        path, line = location.get("path"), location.get("line")
        if not path or not line:
            continue
        try:
            with open(os.path.join(root, path)) as fh:
                lines = fh.readlines()
        except OSError:
            wrong += 1
            continue
        window = "".join(lines[max(0, line - 2):line + 1])
        correct += name in window
        wrong += name not in window
    return correct, wrong


def freshness_of(vault, symbols_path, repo):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        linker.main(["--vault", vault, "--symbols", symbols_path,
                     "--repo", repo, "--format", "json"])
    return json.loads(buffer.getvalue()).get("freshness") or {}


def main():
    scratch = tempfile.mkdtemp(prefix="tracelink-f3-")
    try:
        repo = os.path.join(scratch, "repo")
        os.makedirs(repo)

        # v1: the code the upstream artefact was generated from
        write_repo(repo, drift=0)
        truth_v1 = read_symbol_lines(repo)
        write_graph(repo, truth_v1)

        # v2: the code as it is today — every symbol has moved down
        write_repo(repo, drift=25)
        truth_v2 = read_symbol_lines(repo)
        moved = sum(1 for name in truth_v1
                    if truth_v1[name][1] != truth_v2.get(name, (None, None))[1])

        # A vault with one note: `link` needs something to link before it
        # will report freshness at all (it exits early on an empty vault,
        # printing prose even under --format json — recorded as O2).
        register = os.path.join(scratch, "FINDINGS.md")
        with open(register, "w") as fh:
            fh.write("# Findings\n\n## F3-01 — a finding [MEDIUM]\n"
                     "### STATUS: OPEN\n\n`sym_000_00` is named here.\n")
        vault = os.path.join(scratch, "vault")
        with contextlib.redirect_stdout(io.StringIO()):
            splitter.main(["--register", register, "--out", vault,
                           "--prefix", "F3"])
        rows = []
        for backend in ("scan", "graphify"):
            symbols_path = os.path.join(scratch, f"symbols-{backend}.json")
            with contextlib.redirect_stdout(io.StringIO()):
                symbol_index.main(["--repo", repo, "--backend", backend,
                                   "--out", symbols_path])
            correct, wrong = probe(repo, symbols_path)
            fresh = freshness_of(vault, symbols_path, repo)
            upstream = fresh.get("upstream") or {}
            rows.append((backend, correct, wrong, fresh.get("status"),
                         upstream.get("state"), fresh.get("effective"),
                         upstream.get("reason")))

        print(__doc__.split("\n\n")[0])
        print()
        print(f"  repository: {FILES} files, {FILES * FUNCS} symbols")
        print(f"  upstream artefact describes the code BEFORE {moved} symbols "
              f"moved\n")
        print(f"  {'backend':<10} {'lines ok':>9} {'wrong':>6} "
              f"{'index':>8} {'upstream':>10} {'effective':>10}  why")
        for backend, correct, wrong, status, up, effective, why in rows:
            print(f"  {backend:<10} {correct:>9} {wrong:>6} {str(status):>8} "
                  f"{str(up):>10} {str(effective):>10}  {why}")
        print("\n  scan reads the tree, so its evidence cannot be older "
              "than the tree.")
        print("  graphify reads an artefact that records nothing about which "
              "repository state")
        print("  it describes — so the index may well be usable, but it is "
              "not evidence of")
        print("  currency, and `effective` says so instead of rounding up to "
              "`fresh`.")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
