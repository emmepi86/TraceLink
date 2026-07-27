#!/usr/bin/env python3
"""Cross-link notes and code, in both directions.

Forward: each note gets a `## Linked code` block listing the symbols it names,
with file and line.

Backward: `CODE-INDEX.md` lists, per symbol, the notes that mention it. This is
the direction that is usually missing and usually wanted — *what has been said
about this function* is a question nobody can answer from the code alone.

The matching is LEXICAL. It finds identifiers a note actually spells out. It
will not know that "the enrichment channel" means `enrich_records()` unless the
note says so. That is a real limit, stated here rather than left for the user to
discover: notes that name their symbols get linked, prose that talks around them
does not.

Usage:
    python3 link.py --vault vault/ --symbols symbols.json
    python3 link.py --vault vault/ --symbols symbols.json --max-links 12 --min-len 6
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
from typing import Dict, List

#: Names too common to be evidence of anything. A note mentioning "data" should
#: not acquire a link to every table called `data`. Extend with --stopwords.
_DEFAULT_STOP = {
    "data", "value", "values", "result", "results", "config", "settings",
    "status", "content", "context", "request", "response", "record", "records",
    "session", "message", "options", "params", "output", "input", "index",
    "update", "create", "delete", "select", "insert", "filter", "process",
    "handler", "manager", "service", "client", "server", "public", "private",
    "string", "number", "object", "array", "table", "column", "schema",
}

_SNAKE = re.compile(r"\b(_?[a-z][a-z0-9_]*)\b")
_CAMEL = re.compile(r"\b([A-Z][A-Za-z0-9]+)\b")
_CODE_SPAN = re.compile(r"`([^`\n]{2,80})`")

_FORWARD_HEADING = "## Linked code"


def candidates(text: str, symbols: Dict[str, str], min_len: int, stop: set) -> List[str]:
    """Identifiers the note names that exist as symbols.

    Names inside backticks are trusted at any length — writing `id` in a code
    span is a deliberate reference, while the bare word is not.
    """
    hits = set()
    for span in _CODE_SPAN.findall(text):
        token = span.strip().rstrip("()").lstrip(".")
        if token in symbols:
            hits.add(token)
    for m in _SNAKE.findall(text):
        if len(m) >= min_len and m not in stop and m in symbols:
            hits.add(m)
    for m in _CAMEL.findall(text):
        if len(m) >= min_len and m.lower() not in stop and m in symbols:
            hits.add(m)
    return sorted(hits)


def write_forward(path: str, links: List[str], symbols: Dict[str, str]) -> bool:
    text = open(path, errors="replace").read()
    block = _FORWARD_HEADING + "\n\n" + "\n".join(
        f"- `{name}` — {symbols[name]}" for name in links
    ) + "\n"

    if _FORWARD_HEADING in text:
        # Replace the previous block so re-runs stay idempotent.
        new = re.sub(
            rf"{re.escape(_FORWARD_HEADING)}\n\n(?:- .*\n)*",
            block,
            text,
            count=1,
        )
    elif text.startswith("---"):
        # After the frontmatter, before the body.
        end = text.find("\n---", 3)
        cut = text.find("\n", end + 1) + 1 if end != -1 else 0
        new = text[:cut] + "\n" + block + "\n" + text[cut:]
    else:
        new = block + "\n" + text

    if new == text:
        return False
    open(path, "w").write(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-link notes and code.")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--max-links", type=int, default=8,
                    help="cap per note; generic names produce noise (default 8)")
    ap.add_argument("--min-len", type=int, default=7,
                    help="minimum identifier length outside backticks (default 7)")
    ap.add_argument("--stopwords", default="",
                    help="comma-separated extra names to ignore")
    args = ap.parse_args()

    with open(args.symbols) as fh:
        payload = json.load(fh)
    symbols = payload.get("symbols") or payload
    stop = set(_DEFAULT_STOP) | {s.strip() for s in args.stopwords.split(",") if s.strip()}

    notes = sorted(
        p for p in os.listdir(args.vault)
        if p.endswith(".md") and p not in ("INDEX.md", "CODE-INDEX.md")
    )
    if not notes:
        print(f"no notes in {args.vault}")
        return 1

    backward: Dict[str, List[str]] = collections.defaultdict(list)
    linked = 0
    for name in notes:
        path = os.path.join(args.vault, name)
        text = open(path, errors="replace").read()
        hits = candidates(text, symbols, args.min_len, stop)[: args.max_links]
        if not hits:
            continue
        if write_forward(path, hits, symbols):
            linked += 1
        stem = os.path.splitext(name)[0]
        for h in hits:
            backward[h].append(stem)

    lines = [
        "# Code index — which notes mention which symbol",
        "",
        f"Generated from `{os.path.basename(args.symbols)}` "
        f"(backend: {payload.get('backend', 'unknown')}).",
        "",
        "Matching is lexical: it finds symbols the notes actually spell out.",
        "Prose that talks around a symbol without naming it is not linked.",
        "",
        "| symbol | location | notes |",
        "|---|---|---|",
    ]
    for sym in sorted(backward, key=lambda s: (-len(backward[s]), s)):
        refs = " ".join(f"[[{n}]]" for n in sorted(set(backward[sym])))
        lines.append(f"| `{sym}` | {symbols[sym]} | {refs} |")

    with open(os.path.join(args.vault, "CODE-INDEX.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"forward: {linked}/{len(notes)} notes linked")
    print(f"backward: {len(backward)} symbols -> {os.path.join(args.vault, 'CODE-INDEX.md')}")
    hot = sorted(backward.items(), key=lambda kv: -len(kv[1]))[:5]
    if hot:
        print("most-referenced symbols:")
        for sym, refs in hot:
            print(f"   {sym:<34} {len(set(refs))} notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
