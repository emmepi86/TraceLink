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


def finding_pattern(prefix: str) -> str:
    """`RES-12` and `RES12` are the same identifier written by two people.

    The hyphen is optional in the pattern and PRESERVED in the id: a register
    that says `F1` gets notes called `F1`, not `F-1`. Rewriting a human
    identifier to suit the tool is a cost paid by every reader of the register,
    forever, to save one regex.
    """
    return rf"{re.escape(prefix)}-?\d+"


def finding_number(fid: str) -> int:
    """The trailing number, whatever separator precedes it.

    `fid.split("-")[-1]` broke on `F1` and on any prefix containing a hyphen,
    such as `P1-CQR-4`.
    """
    m = re.search(r"(\d+)\s*$", fid)
    return int(m.group(1)) if m else 0


def headings_in(text: str) -> List[str]:
    """Every markdown heading, for diagnostics when nothing matched."""
    return [ln.rstrip() for ln in text.splitlines() if ln.lstrip().startswith("#")]


def detect_identifier_styles(text: str) -> "collections.Counter":
    """Candidate finding identifiers actually present, for `--inspect`.

    Answers the question the failure raises — "then what IS in this file?" —
    instead of leaving it to be answered by reading the source.
    """
    styles: "collections.Counter" = collections.Counter()
    for line in headings_in(text):
        # Every stem segment must START with a letter, or the greedy character
        # class eats the leading digit of the number: `RES-37` was reported as
        # style `RES-3<n>`.
        m = re.match(
            r"#{2,6}\s+([A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z][A-Za-z0-9_]*)*)-?(\d+)\b",
            line)
        if m:
            stem = m.group(1)
            sep = "-" if f"{stem}-{m.group(2)}" in line else ""
            styles[f"{stem}{sep}<n>"] += 1
    return styles


def split(register: str, prefix: str) -> "collections.OrderedDict[str, List[str]]":
    text = open(register, errors="replace").read()
    head_re = rf"#{{2,6}} {finding_pattern(prefix)}"
    parts = re.split(rf"\n(?={head_re})", text)
    by: "collections.OrderedDict[str, List[str]]" = collections.OrderedDict()
    for p in parts:
        m = re.match(rf"#{{2,6}} ({finding_pattern(prefix)})", p.strip())
        if m:
            by.setdefault(m.group(1), []).append(p.rstrip())
    return by


def note_body(fid: str, blocks: List[str], prefix: str) -> tuple:
    """Build the note.

    Two heading sets, deliberately distinct. `finding_headings` are the
    `## RES-01` lines and carry the title. `state_headings` add the explicit
    `### STATUS:` / `### SEVERITY:` lines.

    Collapsing them was a real bug: `note_body` filtered to finding headings
    only, so the documented explicit grammar never reached `classify()` and a
    note marked `### STATUS: CLOSED` came out `open`. The unit tests missed it
    because they called `classify()` directly, bypassing the very filter that
    broke it — a test that skips the caller cannot see the caller's mistake.
    """
    blob = "\n\n".join(blocks)
    finding_re = re.compile(rf"#{{2,6}} {finding_pattern(prefix)}")
    finding_headings, state_headings = [], []
    for line in blob.splitlines():
        if not line.lstrip().startswith("#"):
            continue
        if finding_re.match(line):
            finding_headings.append(line)
            state_headings.append(line)
        elif _STATUS_RE.search(line) or _SEVERITY_RE.search(line):
            state_headings.append(line)

    st, sv = classify(state_headings), severity(state_headings)
    refs = sorted({r for r in re.findall(finding_pattern(prefix), blob) if r != fid},
                  key=finding_number)
    title = (re.sub(rf"^#{{2,6}} {finding_pattern(prefix)}\s*[—:-]?\s*", "",
                    finding_headings[0]).strip() if finding_headings else fid)
    body = "\n".join([
        "---",
        # RES-OWNERSHIP: the marker that makes a note recognisably ours. link.py
        # refuses to touch anything without it, so pointing --vault at a folder
        # of hand-written markdown cannot rewrite it.
        "tracelink_schema: 1",
        f"tracelink_id: {fid}",
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
    ap.add_argument("--inspect", action="store_true",
                    help="report the identifier styles present and write nothing")
    ap.add_argument("--adopt-vault", action="store_true",
                    help="take over a vault built from a different register")
    args = ap.parse_args()

    if args.inspect:
        text = open(args.register, errors="replace").read()
        styles = detect_identifier_styles(text)
        if not styles:
            print(f"no candidate finding identifiers in {args.register}")
            for line in headings_in(text)[:8]:
                print(f"  {line}")
            return 1
        print(f"Detected candidate identifier styles in {args.register}:\n")
        for style, count in styles.most_common():
            print(f"  {style:<20} {count} occurrence(s)")
        return 0

    os.makedirs(args.out, exist_ok=True)
    by = split(args.register, args.prefix)
    if not by:
        # "It found nothing" is not a diagnosis. Show what IS there, so the
        # answer does not require reading this source file — which is what it
        # required the first time it happened.
        text = open(args.register, errors="replace").read()
        print(f"No findings matched in {args.register}.\n")
        print("Expected:")
        print("  heading level: 2-6")
        print(f"  identifier:    {args.prefix}-<number> or {args.prefix}<number>\n")
        found = headings_in(text)
        if found:
            print("First headings found:")
            for line in found[:6]:
                print(f"  {line}")
        styles = detect_identifier_styles(text)
        if styles:
            print("\nCandidate identifier styles present:")
            for style, count in styles.most_common(5):
                print(f"  {style:<20} {count}")
            print("\nRe-run with --inspect for the full list.")
        return 1

    # --- vault identity ------------------------------------------------
    # A vault holds one register. Splitting a second one into it rewrites
    # INDEX.md to describe only the newcomer and orphans the notes already
    # there: a vault that is formally valid and semantically false. Merging is
    # a deliberate operation, not a side effect of running split twice.
    man_path = os.path.join(args.out, ".tracelink-manifest.json")
    import json as _json
    existing = {}
    if os.path.exists(man_path):
        try:
            existing = _json.load(open(man_path))
        except Exception:
            existing = {}
    prior = existing.get("register") or {}
    prior_prefix = prior.get("prefix") or existing.get("prefix")
    prior_source = prior.get("source") or existing.get("generated_from")
    register_name = os.path.basename(args.register)
    if not args.adopt_vault and existing:
        if prior_prefix and prior_prefix != args.prefix:
            print(f"ERROR: vault already belongs to prefix {prior_prefix}")
            print(f"cannot split prefix {args.prefix} into the same vault")
            print("use a separate --out directory")
            return 2
        if prior_source and prior_source != register_name:
            print(f"ERROR: vault was built from {prior_source}")
            print(f"cannot split {register_name} into the same vault")
            print("use a separate --out directory, or --adopt-vault to take it over")
            return 2

    rows = []
    for fid, blocks in by.items():
        body, st, sv, n, title = note_body(fid, blocks, args.prefix)
        with open(os.path.join(args.out, f"{fid}.md"), "w") as fh:
            fh.write(body)
        rows.append((fid, st, sv, n, title))

    # RES-PRUNE: remove notes for findings that left the register — but only
    # ones this tool generated and previously recorded. A file absent from the
    # old manifest is never deleted, whatever it looks like.
    previous = existing.get("generated_notes", []) if existing else []
    current = [f"{r[0]}.md" for r in rows]
    pruned = 0
    root = os.path.realpath(args.out)
    safe = re.compile(rf"\A{finding_pattern(args.prefix)}\.md\Z")
    for stale in sorted(set(previous) - set(current)):
        # A manifest is persisted data, and deletion is irreversible. Validate
        # the name, keep the path inside the vault, and require the ownership
        # marker in the frontmatter — three independent conditions, because any
        # one of them alone has a way to be wrong.
        if not safe.match(stale):
            print(f"  warning: refusing unsafe manifest entry {stale!r}")
            continue
        path = os.path.realpath(os.path.join(root, stale))
        if os.path.commonpath([root, path]) != root:
            print(f"  warning: manifest entry escapes the vault: {stale!r}")
            continue
        if not os.path.exists(path):
            continue
        head = open(path, errors="replace").read(400)
        if head.startswith("---") and "tracelink_schema: 1" in head.split("\n---", 1)[0]:
            os.remove(path)
            pruned += 1
    with open(man_path, "w") as fh:
        _json.dump({"schema_version": 2,
                    "register": {"prefix": args.prefix, "source": register_name},
                    # kept for readers of v1 manifests
                    "generated_from": register_name,
                    "generated_notes": current}, fh, indent=1)

    rows.sort(key=lambda r: finding_number(r[0]))
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
    print(f"{len(rows)} notes -> {args.out}" + (f"   pruned {pruned}" if pruned else ""))
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
