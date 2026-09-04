#!/usr/bin/env python3
"""How much does a single file reference cost on a large repository?

ms-11d deferred the repository walk: it happens when a note names a file and
not before. That removes the *unconditional* floor benchmark 02 measured —
7 seconds at 50 000 files, paid by vaults that anchor nothing to a path.

It does not remove the walk. A vault with one file reference still pays for
one, because resolving a reference means knowing every file that could match
it: a reference is ambiguous when two files share its tail, and finding that
out is the whole job. This measures what remains, on the same synthetic
repositories, across three vaults:

    0 refs      no note names a path
    1 ref       exactly one note does
    many refs   a tenth of the notes do

If `1 ref` costs what `many refs` costs, then the remaining term is the walk
itself and not the references — which is worth knowing before anyone builds
an incremental file index to make it cheaper.

Freshness verification is switched off here, because it hashes every
indexed file and would drown the signal: at 50 000 files a default `link`
takes 6.1 s of which 5.8 s is that hash. It is measured on its own instead.

    python3 benchmarks/f2_file_refs.py --scratch /tmp/scale --out results/
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from scaling import make_repo  # noqa: E402  — the same generator as b02

NOTES = 100


def register(path, symbols, refs, files):
    """`refs` of the notes name a file; the rest name a symbol."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Findings — SYNTHETIC FIXTURE\n\n")
        for number in range(NOTES):
            fh.write(f"## FR-{number:04d} — fixture [MEDIUM]\n"
                     f"### STATUS: OPEN\n\n")
            if number < refs:
                # a real path in the generated tree, deep enough to be
                # resolved by suffix like a real reference
                index = number % max(1, files)
                fh.write(f"`pkg{index // 100:04d}/mod{index:06d}.py` "
                         f"behaves oddly.\n\n")
            else:
                fh.write(f"`{symbols[number % len(symbols)]}` is named "
                         f"here.\n\n")


def measure(scratch, files, refs, repeats=2):
    from tracelink import linker, splitter, symbol_index

    repo = make_repo(os.path.join(scratch, f"repo-{files}x1"), files, 1)
    work = os.path.join(scratch, f"fr-{files}-{refs}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    symbols_path = os.path.join(work, "symbols.json")
    vault = os.path.join(work, "vault")
    findings = os.path.join(work, "FINDINGS.md")

    with contextlib.redirect_stdout(io.StringIO()):
        symbol_index.main(["--repo", repo, "--backend", "scan",
                           "--out", symbols_path])
    with open(symbols_path) as fh:
        names = sorted(json.load(fh).get("symbols") or {})
    register(findings, names, refs, files)
    with contextlib.redirect_stdout(io.StringIO()):
        splitter.main(["--register", findings, "--out", vault,
                       "--prefix", "FR"])

    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            # `--freshness ignore` on purpose: verifying freshness hashes
            # every indexed file and costs 5.8s of the 6.1s a default run
            # takes here, which would drown the term this is measuring.
            # That cost is real and is measured separately (F2b); this
            # column is about what a file reference makes the linker do.
            linker.main(["--vault", vault, "--symbols", symbols_path,
                         "--repo", repo, "--full",
                         "--freshness", "ignore"])
        samples.append(time.perf_counter() - start)
    shutil.rmtree(work, ignore_errors=True)
    return round(min(samples), 4)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default="/tmp/tracelink-f2-refs")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    os.makedirs(args.scratch, exist_ok=True)

    rows = []
    print(f"  {'files':>7} {'0 refs':>9} {'1 ref':>9} {'many refs':>10}")
    for files in (2700, 10000, 25000, 50000):
        row = {"files": files}
        for label, refs in (("0 refs", 0), ("1 ref", 1),
                            ("many refs", NOTES // 10)):
            row[label] = measure(args.scratch, files, refs)
        rows.append(row)
        print(f"  {files:>7} {row['0 refs']:>9.3f} {row['1 ref']:>9.3f} "
              f"{row['many refs']:>10.3f}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "f2-file-refs.json"), "w") as fh:
            json.dump(rows, fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
