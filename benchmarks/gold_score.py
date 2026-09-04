#!/usr/bin/env python3
"""Score TraceLink against a gold set somebody wrote by hand.

A benchmark of speed says how fast the tool is wrong. This one asks the
question the tool exists for: given real engineering knowledge about a real
codebase, does it connect it to the right code — and, more importantly, does
it ever connect it confidently to the wrong code?

The gold set is authored by reading the code and its history. It must not be
generated from TraceLink's own output, or the benchmark measures agreement
with itself. This script only reads it.

Outcomes, and why they are not just precision and recall:

    correct match           expected an anchor, got that anchor
    wrong match             expected an anchor, got a DIFFERENT one
    missed match            expected an anchor, got none
    correct ambiguity       expected two meanings, refused to choose
    false ambiguity         expected an answer, got a refusal to choose
    correct no-assertion    expected nothing, got nothing
    unsupported assertion   expected nothing (or a refusal), got an anchor

Two of those are graded differently from the rest, and the report says so at
the top. A missed match costs a user a piece of memory. A **wrong match** or
an **unsupported assertion** hands them a false one — and a memory layer
that is confidently wrong is worse than no memory layer, because it is
believed. The target for those two is zero, and 85% recall with none of them
is a better result than 99% recall with a few.

    python3 benchmarks/gold_score.py --repo /path/to/code \\
        --register gold/FINDINGS.md --gold gold/gold.json --out results/
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from tracelink import consult as consult_mod  # noqa: E402
from tracelink import linker, splitter, symbol_index  # noqa: E402

GRAVE = ("wrong match", "unsupported assertion")


def build_vault(repo, register, work, backend, prefix):
    os.makedirs(work, exist_ok=True)
    symbols = os.path.join(work, "symbols.json")
    vault = os.path.join(work, "vault")
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        if symbol_index.main(["--repo", repo, "--backend", backend,
                              "--out", symbols]) != 0:
            raise SystemExit("the index could not be built")
        if splitter.main(["--register", register, "--out", vault,
                          "--prefix", prefix]) != 0:
            raise SystemExit("the register could not be split")
        linker.main(["--vault", vault, "--symbols", symbols, "--repo", repo])
    with open(os.path.join(vault, consult_mod.STATE_FILE)) as fh:
        return vault, json.load(fh)


def actual_for(state, finding_id):
    """What TraceLink concluded about one finding, in the gold's vocabulary."""
    entry = state["notes"].get(finding_id + ".md")
    if entry is None:
        return {"state": "absent", "symbols": [], "files": [],
                "ambiguous": []}
    symbols = list(entry.get("linked") or [])
    files = list(entry.get("files") or [])
    ambiguous = [item.get("name") for item in entry.get("ambiguous") or []]
    if symbols or files:
        state_name = "match"
    elif ambiguous:
        state_name = "ambiguous"
    else:
        state_name = "no-assertion"
    return {"state": state_name, "symbols": symbols, "files": files,
            "ambiguous": ambiguous}


def classify(expected, actual):
    """One outcome per finding. The grave ones are named, not averaged."""
    want = expected["state"]

    if want == "match":
        wanted_symbols = [a["name"] for a in expected.get("anchors", [])
                          if a["kind"] == "symbol"]
        wanted_files = [a["path"] for a in expected.get("anchors", [])
                        if a["kind"] == "file"]
        got_symbols, got_files = actual["symbols"], actual["files"]
        if not got_symbols and not got_files:
            return ("false ambiguity" if actual["ambiguous"]
                    else "missed match")
        symbols_ok = all(name in got_symbols for name in wanted_symbols)
        files_ok = all(path in got_files for path in wanted_files)
        if symbols_ok and files_ok:
            # Extra anchors are not a wrong match: a finding may legitimately
            # name more than the gold recorded. Wrongness is the expected
            # anchor being REPLACED by another, not accompanied by one.
            return "correct match"
        return "wrong match"

    if want == "ambiguous":
        if actual["state"] == "match":
            return "unsupported assertion"
        if actual["ambiguous"]:
            return "correct ambiguity"
        return "missed ambiguity"

    # want == "no-assertion"
    if actual["state"] == "match":
        return "unsupported assertion"
    if actual["ambiguous"]:
        return "false ambiguity"
    return "correct no-assertion"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--register", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--work", default="/tmp/tracelink-gold")
    ap.add_argument("--out", default=None)
    ap.add_argument("--backend", default="scan")
    ap.add_argument("--prefix", default="CRM")
    args = ap.parse_args(argv)

    with open(args.gold) as fh:
        gold = json.load(fh)
    _vault, state = build_vault(os.path.abspath(args.repo),
                                os.path.abspath(args.register),
                                os.path.abspath(args.work), args.backend,
                                args.prefix)

    rows, counts = [], collections.Counter()
    by_category = collections.defaultdict(collections.Counter)
    for item in gold["findings"]:
        actual = actual_for(state, item["finding_id"])
        outcome = classify(item["expected"], actual)
        counts[outcome] += 1
        by_category[item["review"]["category"]][outcome] += 1
        rows.append({"finding_id": item["finding_id"], "outcome": outcome,
                     "expected": item["expected"], "actual": actual,
                     "adversarial": item["review"]["adversarial"],
                     "category": item["review"]["category"]})

    grave = sum(counts[name] for name in GRAVE)
    resolvable = sum(1 for i in gold["findings"]
                     if i["expected"]["state"] == "match")
    recovered = counts["correct match"]

    print(f"\n  FALSE AUTHORITATIVE ASSERTIONS   {grave}   (target 0)")
    print(f"  SUPPORTED KNOWLEDGE RECOVERED    {recovered}/{resolvable}"
          f"   ({100 * recovered / max(1, resolvable):.0f}%)\n")
    for outcome, n in counts.most_common():
        mark = "  <-- grave" if outcome in GRAVE and n else ""
        print(f"  {outcome:24} {n:>3}{mark}")

    misses = [r for r in rows if not r["outcome"].startswith("correct")]
    if misses:
        print("\n  not as expected:")
        for row in misses:
            print(f"    {row['finding_id']}  {row['outcome']:22} "
                  f"expected {row['expected']['state']}, got "
                  f"{row['actual']['state']}"
                  + ("  [adversarial]" if row["adversarial"] else ""))

    adversarial = [r for r in rows if r["adversarial"]]
    ok = sum(1 for r in adversarial if r["outcome"].startswith("correct"))
    print(f"\n  adversarial findings handled as expected: {ok}/"
          f"{len(adversarial)}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "gold-results.json"), "w") as fh:
            json.dump({"counts": dict(counts), "rows": rows,
                       "false_authoritative_assertions": grave,
                       "recovered": recovered, "resolvable": resolvable,
                       "by_category": {k: dict(v)
                                       for k, v in by_category.items()}},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 1 if grave else 0


if __name__ == "__main__":
    raise SystemExit(main())
