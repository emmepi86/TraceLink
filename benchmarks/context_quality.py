#!/usr/bin/env python3
"""What does an agent actually receive when it consults real code?

ms-12 asked whether findings land on the right code. This asks the next
question, which is the one an agent lives with: when it touches a file or a
symbol, how much of the context it gets back is useful, how much is noise,
and how much is *asserted without evidence*.

Those three are not the same failure and must not be averaged into one
number, because only one of them is dangerous:

    A. wrong evidence      an anchor produced by something the author never
                           wrote — the finding surfaces as if it belonged
    B. irrelevant context  a real anchor, but nothing this reader needed
    C. bad ordering        the right context, badly presented

A is not a ranking problem. Ranking A lower still leaves it there, believed
by whoever reads far enough. B and C are legibility problems and can be
tuned; A has to not exist.

Each target carries three sets, written by hand:

    must_include    the reader is entitled to these
    acceptable      related, defensible, not required
    everything else noise — and, if it arrived through prose, false authority

Evidence is checked mechanically: a finding is *supported* at a target when
the anchor that brought it there is written in the finding itself as code —
in backticks, or as a path. A finding that surfaced through a bare word in
prose is counted as false authority no matter how relevant it looks.

    python3 benchmarks/context_quality.py --repo /path/to/code \\
        --vault vault/ --targets targets.json --register gold/FINDINGS.md
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from tracelink import consult as consult_mod  # noqa: E402


def backticked(text):
    """Everything the finding's AUTHOR wrote as code.

    The managed block is cut out first. `link` writes the anchors it chose
    into the note in backticks, so reading the note as it sits on disk would
    let the tool certify its own evidence: every anchor would look authored.
    The block is exactly the part that is not the author's.
    """
    start = text.find(consult_mod.BLOCK_START)
    if start >= 0:
        end = text.find(consult_mod.BLOCK_END, start)
        if end >= 0:
            text = text[:start] + text[end + len(consult_mod.BLOCK_END):]
    return {m.group(1).strip() for m in re.finditer(r"`([^`\n]+)`", text)}


def note_bodies(vault):
    bodies = {}
    for name in sorted(os.listdir(vault)):
        if not name.endswith(".md") or name in ("INDEX.md", "CODE-INDEX.md"):
            continue
        with open(os.path.join(vault, name), encoding="utf-8",
                  errors="replace") as fh:
            bodies[name[:-3]] = fh.read()
    return bodies


def supported(note_text, anchor_names):
    """Was any anchor that brought this note here written as code in it?

    Three spellings count as the author having written it, because all three
    are evidence TraceLink documents as authored: the bare name, a qualified
    name whose tail is the anchor (`tenant_schema.with_tenant` for
    `with_tenant`), and a path quoted with more or fewer segments. What does
    NOT count is the anchor appearing only as an ordinary word in the prose
    — that is the distinction this benchmark exists to measure.
    """
    written = backticked(note_text)
    for name in anchor_names:
        if name in written:
            return True
        for quoted in written:
            if "/" in quoted or "/" in name:
                if quoted.endswith(name) or name.endswith(quoted):
                    return True
            elif quoted.endswith("." + name) or name.endswith("." + quoted):
                return True          # a qualified name the author wrote
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    repo, vault = os.path.abspath(args.repo), os.path.abspath(args.vault)
    with open(args.targets) as fh:
        spec = json.load(fh)
    bodies = note_bodies(vault)

    rows = []
    counts = collections.Counter()
    sizes, per_target, latencies, first_useful = [], [], [], []

    for target in spec["targets"]:
        kind = (consult_mod.FILE if target["kind"] == "file"
                else consult_mod.SYMBOL)
        start = time.perf_counter()
        result = consult_mod.consult(repo, target["target"], kind=kind,
                                     vault=vault, limit=None)
        latencies.append(time.perf_counter() - start)
        text = consult_mod.render_text(result)
        sizes.append(len(text.encode("utf-8")))
        returned = [n.note_id for n in result.notes]
        per_target.append(len(returned))

        must = set(target["must_include"])
        acceptable = set(target["acceptable"])
        verdicts = []
        for position, note in enumerate(result.notes, start=1):
            names = [hit.name for hit in note.symbols]
            if note.file_anchor:
                names.append(result.resolved)
            body = bodies.get(note.note_id, "")
            if not supported(body, names):
                verdict = "false authority"
            elif note.note_id in must:
                verdict = "useful"
            elif note.note_id in acceptable:
                verdict = "acceptable"
            else:
                verdict = "benign noise"
            counts[verdict] += 1
            verdicts.append({"finding": note.note_id, "verdict": verdict,
                             "position": position, "anchors": names})
        useful_positions = [v["position"] for v in verdicts
                            if v["verdict"] == "useful"]
        if useful_positions:
            first_useful.append(min(useful_positions))

        rows.append({"target": target["target"], "kind": target["kind"],
                     "adversarial": target.get("adversarial", False),
                     "returned": returned,
                     "missing": sorted(must - set(returned)),
                     "duplicates": len(returned) - len(set(returned)),
                     "bytes": len(text.encode("utf-8")),
                     "verdicts": verdicts})

    total_must = sum(len(t["must_include"]) for t in spec["targets"])
    found_must = sum(len(set(t["must_include"]) & set(r["returned"]))
                     for t, r in zip(spec["targets"], rows))
    shown = sum(counts.values())
    precision = (counts["useful"] + counts["acceptable"]) / max(1, shown)

    print(f"\n  CONTEXT RECALL          {found_must}/{total_must}"
          f"   ({100 * found_must / max(1, total_must):.0f}%)")
    print(f"  CONTEXT PRECISION       {100 * precision:.0f}%"
          f"   (useful or acceptable, of everything shown)")
    print(f"  FALSE-AUTHORITY EXPOSURE {counts['false authority']:>3}"
          f"   (target 0)\n")
    for verdict, n in counts.most_common():
        print(f"  {verdict:20} {n:>4}")
    print(f"\n  findings per target      "
          f"median {statistics.median(per_target):.0f}, "
          f"max {max(per_target)}")
    print(f"  briefing size            median "
          f"{statistics.median(sizes):.0f} B, max {max(sizes)} B")
    print(f"  first useful finding at  position "
          f"{statistics.median(first_useful):.0f} (median)"
          if first_useful else "  no useful finding anywhere")
    print(f"  duplicates               {sum(r['duplicates'] for r in rows)}")
    print(f"  consult latency          median "
          f"{1000 * statistics.median(latencies):.2f} ms")

    adversarial = [r for r in rows if r["adversarial"]]
    noisy = [r for r in adversarial if r["returned"]]
    print(f"\n  adversarial targets returning anything: {len(noisy)}/"
          f"{len(adversarial)}")
    for row in noisy:
        kinds = collections.Counter(v["verdict"] for v in row["verdicts"])
        print(f"    {row['target']:52} {dict(kinds)}")

    empty = [r for r in rows if not r["adversarial"] and not r["returned"]]
    if empty:
        print(f"\n  targets with NO context at all: {len(empty)}")
        for row in empty[:10]:
            print(f"    {row['target']}  (expected {row['missing']})")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "context-results.json"), "w") as fh:
            json.dump({"counts": dict(counts), "rows": rows,
                       "recall": [found_must, total_must],
                       "precision": precision,
                       "median_bytes": statistics.median(sizes),
                       "median_per_target": statistics.median(per_target)},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 1 if counts["false authority"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
