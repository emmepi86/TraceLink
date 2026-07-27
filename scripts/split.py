#!/usr/bin/env python3
"""Split an append-only findings register into one note per finding.

A register written by appending is the natural way to record findings as they
are made, and the worst possible way to read them afterwards. A single finding
ends up as several sections scattered through the file in chronological order —
its correction sitting between two sections of an unrelated one — and the story
can only be reconstructed by reading the whole thing.

This produces one file per finding, with frontmatter and `[[wikilinks]]`, which
is both an Obsidian vault and a set of files an agent can read one at a time.
The original register is never modified.

Usage:
    python3 split.py --register FINDINGS.md --out vault/ --prefix RES
"""

from __future__ import annotations

import argparse
import collections
import os
import re
from typing import Dict, List

#: Status keywords, checked in order — the first match wins, so the more
#: specific phrases must come first.
_STATUS_RULES = (
    ("withdrawn", ("WITHDRAWN", "RETRACTED")),
    ("partial", ("PARTIALLY CLOSED", "PARTIAL")),
    ("downgraded", ("DOWNGRADED",)),
    ("closed", ("CLOSED", "RESOLVED", "FIXED")),
)

_SEVERITIES = ("critical", "high", "medium", "low")


def classify(headings: List[str]) -> str:
    """Status from the finding's OWN headings, never from its body.

    This is the one thing in this file worth reading twice. Keyword-matching the
    whole body misclassifies any finding that *mentions* another one's status —
    a note reading "this already caused a withdrawn finding (X-16)" becomes
    `withdrawn` itself. A wrong status in an index is worse than no status,
    because an index is trusted at a glance.
    """
    blob = " ".join(headings).upper()
    for status, keywords in _STATUS_RULES:
        if any(k in blob for k in keywords):
            return status
    return "open"


def severity(headings: List[str]) -> str:
    blob = " ".join(headings).upper()
    for s in _SEVERITIES:
        if f"[{s.upper()}]" in blob:
            return s
    return "unspecified"


def split(register: str, prefix: str) -> "collections.OrderedDict[str, List[str]]":
    text = open(register, errors="replace").read()
    head_re = rf"#{{2,6}} {re.escape(prefix)}-\d+"
    parts = re.split(rf"\n(?={head_re})", text)
    by: "collections.OrderedDict[str, List[str]]" = collections.OrderedDict()
    for p in parts:
        m = re.match(rf"#{{2,6}} ({re.escape(prefix)}-\d+)", p.strip())
        if m:
            by.setdefault(m.group(1), []).append(p.rstrip())
    return by


def note_body(fid: str, blocks: List[str], prefix: str) -> tuple:
    blob = "\n\n".join(blocks)
    heads = [l for l in blob.splitlines() if re.match(rf"#{{2,6}} {re.escape(prefix)}-\d+", l)]
    st, sv = classify(heads), severity(heads)
    refs = sorted({r for r in re.findall(rf"{re.escape(prefix)}-\d+", blob) if r != fid},
                  key=lambda r: int(r.split("-")[-1]))
    title = re.sub(rf"^#{{2,6}} {re.escape(prefix)}-\d+\s*[—:-]?\s*", "", heads[0]).strip() if heads else fid
    body = "\n".join([
        "---",
        f"id: {fid}",
        f"status: {st}",
        f"severity: {sv}",
        f"sections: {len(blocks)}",
        "---",
        "",
        f"# {fid} — {title}",
        "",
        ("Related: " + " ".join(f"[[{r}]]" for r in refs)) if refs else "Related: none",
        "",
        "---",
        "",
        blob,
        "",
    ])
    return body, st, sv, len(blocks), title


def main() -> int:
    ap = argparse.ArgumentParser(description="Split a findings register into a note vault.")
    ap.add_argument("--register", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", default="RES", help="finding id prefix, e.g. RES, BUG, ADR")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    by = split(args.register, args.prefix)
    if not by:
        print(f"no headings matched '## {args.prefix}-<n>' in {args.register}")
        return 1

    rows = []
    for fid, blocks in by.items():
        body, st, sv, n, title = note_body(fid, blocks, args.prefix)
        with open(os.path.join(args.out, f"{fid}.md"), "w") as fh:
            fh.write(body)
        rows.append((fid, st, sv, n, title))

    rows.sort(key=lambda r: int(r[0].split("-")[-1]))
    idx = [
        "# Findings — index",
        "",
        f"One note per finding, split from `{os.path.basename(args.register)}`,",
        "which is left untouched as the chronological record.",
        "",
        "Status is derived from each finding's own headings only — reading it from",
        "the body misclassifies any note that mentions another finding's status.",
        "",
        "| id | status | severity | sections | title |",
        "|---|---|---|---|---|",
    ]
    idx += [f"| [[{r[0]}]] | {r[1]} | {r[2]} | {r[3]} | {r[4][:70]} |" for r in rows]

    open_high = [r for r in rows if r[1] == "open" and r[2] in ("critical", "high")]
    if open_high:
        idx += ["", "## Open and high severity", ""]
        idx += [f"- [[{r[0]}]] — {r[4][:80]}" for r in open_high]

    with open(os.path.join(args.out, "INDEX.md"), "w") as fh:
        fh.write("\n".join(idx) + "\n")

    counts = collections.Counter(r[1] for r in rows)
    print(f"{len(rows)} notes -> {args.out}")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
