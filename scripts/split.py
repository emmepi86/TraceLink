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

#: Explicit grammar. `STATUS: CLOSED` in a heading is unambiguous; free-form
#: keywords are a legacy fallback and warn.
_STATUS_RE = re.compile(
    r"\bSTATUS:\s*(OPEN|CLOSED|REOPENED|PARTIAL|WITHDRAWN|DOWNGRADED)\b", re.I)
_SEVERITY_RE = re.compile(r"\bSEVERITY:\s*(CRITICAL|HIGH|MEDIUM|LOW)\b", re.I)

#: Legacy keywords, matched on WORD BOUNDARIES. Substring matching classified
#: "UNRESOLVED" as closed, because it contains "RESOLVED" — a wrong status in an
#: index is worse than no status, and this was exactly that.
_LEGACY = (
    ("withdrawn", (r"\bWITHDRAWN\b", r"\bRETRACTED\b")),
    ("open",      (r"\bREOPENED\b", r"\bUNRESOLVED\b")),
    ("partial",   (r"\bPARTIALLY\s+CLOSED\b", r"\bPARTIAL\b")),
    ("downgraded",(r"\bDOWNGRADED\b",)),
    ("closed",    (r"\bCLOSED\b", r"\bRESOLVED\b", r"\bFIXED\b")),
)

_SEVERITIES = ("critical", "high", "medium", "low")


def classify(headings: List[str]) -> str:
    """Status from the finding's OWN headings, LAST explicit value wins.

    Two rules, both learned the hard way:

    Never read the body. A note saying "this caused a withdrawn finding (X-16)"
    is not itself withdrawn.

    Never match substrings, and never let an early heading beat a later one.
    "UNRESOLVED" contains "RESOLVED"; a finding CLOSED in one session and
    REOPENED in the next is open. Headings are read in order and the last
    explicit status is the answer.
    """
    status = "open"
    for h in headings:
        m = _STATUS_RE.search(h)
        if m:
            v = m.group(1).lower()
            status = "open" if v == "reopened" else v
            continue
        for name, patterns in _LEGACY:
            if any(re.search(p, h, re.I) for p in patterns):
                status = name
                break
    return status


def severity(headings: List[str]) -> str:
    """Last explicit severity wins, for the same reason status does — a finding
    downgraded from HIGH to LOW must not keep reading HIGH."""
    sev = "unspecified"
    for h in headings:
        m = _SEVERITY_RE.search(h)
        if m:
            sev = m.group(1).lower()
            continue
        for s in _SEVERITIES:
            if f"[{s.upper()}]" in h.upper():
                sev = s
    return sev


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
