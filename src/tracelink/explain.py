"""`tracelink explain <ID>` — why a finding is linked where it is.

Four questions, and deliberately no fifth:

    1. What do we know?          the finding, as the vault holds it
    2. Where does it apply?      the links it carries
    3. Why was it linked there?  the method, and the evidence behind it
    4. What is still uncertain?  the names that resolved to nothing

Everything comes from the link state `link` wrote. Nothing is recomputed:
if this command had to run the resolver to answer, its answer would be about
today's repository rather than about the link that actually exists, and the
two can differ. Reading is also the only thing it does — no vault is
rewritten, no index refreshed.

There are no scores here. A link is asserted or it is not, and when it is
not, the honest output says so in those words: *no link was asserted*.
"""

from __future__ import annotations

import json
import os
import sys

from . import consult as _consult
from .consult import (EXIT_NOT_FOUND, EXIT_NO_STATE, EXIT_OK, EXIT_USAGE,
                      JSON_SCHEMA_VERSION, STATE_FILE, note_head)


class Explanation:
    """One finding, its links, and what stayed unresolved."""

    __slots__ = ("finding_id", "note_file", "status", "severity", "title",
                 "links", "files", "unresolved")

    def __init__(self, finding_id, note_file, status, severity, title,
                 links=(), files=(), unresolved=()):
        self.finding_id = finding_id
        self.note_file = note_file
        self.status = status
        self.severity = severity
        self.title = title
        self.links = tuple(links)
        self.files = tuple(files)
        self.unresolved = tuple(unresolved)


class Link:
    """One resolved link: a name, where it landed, and why."""

    __slots__ = ("name", "path", "line", "provenance")

    def __init__(self, name, path, line, provenance):
        self.name = name
        self.path = path
        self.line = line
        self.provenance = provenance


class Unresolved:
    """One name the resolver refused to link, and what it could have meant."""

    __slots__ = ("name", "state", "reason", "candidates", "basis")

    def __init__(self, name, reason, candidates=(), basis=()):
        self.name = name
        self.reason = reason
        self.state = _consult.RESOLUTION.get(reason, ("ambiguous", None))[0]
        self.candidates = tuple(candidates)
        self.basis = tuple((b[0], b[1]) for b in basis
                           if isinstance(b, (list, tuple)) and len(b) == 2)


def find_note(notes, finding_id):
    """(note_file, candidates) for an id, matched without guessing.

    `RES-17.md` exactly, else a single case-insensitive stem. Two matches
    are two candidates and no answer.
    """
    exact = finding_id + ".md"
    if exact in notes:
        return exact, ()
    wanted = finding_id.lower()
    matches = sorted(n for n in notes
                     if os.path.splitext(str(n))[0].lower() == wanted)
    if len(matches) == 1:
        return matches[0], ()
    return None, tuple(matches)


def explain(project, finding_id, vault=None):
    """(Explanation, error) — error is a `consult` silence reason or None."""
    if vault is None:
        vault = os.path.join(project, ".tracelink", "vault")
    notes, reason = _consult._read_state(vault)
    if notes is None:
        return None, reason

    note_file, candidates = find_note(notes, finding_id)
    if note_file is None:
        return None, "ambiguous-symbol" if candidates else "not-found"

    entry = notes[note_file]
    entry = entry if isinstance(entry, dict) else {}
    note_id, status, severity, title = note_head(vault, note_file)

    linked = entry.get("linked") if isinstance(entry.get("linked"), list) else []
    locations = entry.get("locations") if isinstance(
        entry.get("locations"), list) else []
    records = entry.get("provenance")
    records = records if isinstance(records, list) else []

    links = []
    try:
        if len(records) != len(linked):
            raise _consult.InvalidState("provenance does not cover the links")
        for name, loc, record in zip(linked, locations, records):
            loc = loc if isinstance(loc, dict) else {}
            links.append(Link(name, loc.get("path"), loc.get("line"),
                              _consult._provenance(record)))
    except _consult.InvalidState:
        # Schema 4 has no shape for a match that cannot explain itself.
        return None, "state-invalid"

    unresolved = []
    for item in entry.get("ambiguous") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            unresolved.append(Unresolved(item["name"], item.get("reason"),
                                         item.get("candidates") or (),
                                         item.get("basis") or ()))

    files = [f for f in (entry.get("files") or []) if isinstance(f, str)]
    return Explanation(note_id, note_file, status, severity, title,
                       links, files, unresolved), None


def as_json(explanation, debug=False):
    """The `--json` document. Same version namespace as the other documents:
    it tracks incompatibility of this shape, not the release it shipped in."""
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "finding": {"id": explanation.finding_id,
                    "status": explanation.status,
                    "severity": explanation.severity,
                    "title": explanation.title,
                    "note": explanation.note_file},
        "links": [_link_json(link, debug) for link in explanation.links],
        "files": [{"kind": _consult.FILE, "path": path, "state": "match",
                   "method": "file_anchor",
                   "basis": [{"kind": "file_named_in_note", "value": path}]}
                  for path in explanation.files],
        "unresolved": [{"name": item.name, "state": item.state,
                        "method": None,
                        "candidates": list(item.candidates),
                        "basis": [{"kind": kind, "value": value}
                                  for kind, value in item.basis]}
                       for item in explanation.unresolved],
    }


def _link_json(link, debug):
    """Every link has provenance: schema 4 admits no other kind."""
    doc = {"kind": _consult.SYMBOL, "name": link.name, "path": link.path,
           "line": link.line}
    doc.update(_consult.as_provenance(link.provenance, debug))
    return doc


def render_text(explanation):
    lines = [explanation.finding_id, ""]
    tag = "/".join(p for p in (explanation.status, explanation.severity)
                   if p)
    if explanation.title:
        lines += [explanation.title]
    if tag:
        lines += [f"[{tag}]"]
    lines.append("")

    if not explanation.links and not explanation.files:
        lines += ["NO ANCHOR", "",
                  "Nothing in this finding resolved to code."]
    for link in explanation.links:
        provenance = link.provenance
        lines.append(provenance.state.upper())
        lines.append(f"  {link.name}")
        where = link.path or "?"
        if link.line is not None:
            where += f":{link.line}"
        lines.append(f"  {where}")
        lines.append("")
        lines.append(f"  Method: {provenance.method}")
        lines.append("  Basis:")
        for kind, value in provenance.basis:
            lines.append(f"    {_phrase(kind, value)}")
        lines.append("")
    for path in explanation.files:
        lines += ["MATCH", f"  {path}", "", "  Method: file_anchor",
                  "  Basis:", f"    the finding names the file `{path}`", ""]

    for item in explanation.unresolved:
        lines.append(item.state.upper())
        lines.append(f"  the finding names `{item.name}`")
        for kind, value in item.basis:
            lines.append(f"    {_phrase(kind, value)}")
        if item.candidates:
            lines.append("")
            lines.append("  Candidates:")
            for candidate in item.candidates:
                lines.append(f"    {candidate}")
        lines.append("")
        lines.append("  No link was asserted.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _phrase(kind, value):
    """One line of evidence, in words, without inventing certainty."""
    if kind == "sole_candidate":
        return f"`{value}` names exactly one location in the index"
    if kind == "qualified_name_in_note":
        return f"the finding names the qualified symbol `{value}`"
    if kind == "path_in_note":
        return f"the finding cites the path `{value}`"
    if kind == "dotted_reference":
        return f"the finding writes the dotted name `{value}`"
    if kind == "path_suffix_match":
        return f"that prefix matched exactly one path, `{value}`"
    if kind == "frontmatter_override":
        return f"the note's frontmatter pins it to `{value}`"
    if kind == "file_named_in_note":
        return f"the finding names the file `{value}`"
    return f"{kind}: {value}"


def main(argv=None, prog=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog,
        description="Why a finding is linked where it is: the links it "
                    "carries, the method that produced each one, and the "
                    "evidence behind it. Reads the link state; recomputes "
                    "nothing.")
    ap.add_argument("finding", help="a finding id, e.g. RES-17")
    ap.add_argument("--repo", default=".", help="repository root (default .)")
    ap.add_argument("--vault", default=None,
                    help="vault holding the link state "
                         "(default <repo>/.tracelink/vault)")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable document instead")
    ap.add_argument("--debug", action="store_true",
                    help="include the resolver's internal reason codes, "
                         "which carry no stability promise")
    args = ap.parse_args(argv)

    explanation, error = explain(args.repo, args.finding, vault=args.vault)
    if explanation is None:
        code = _consult.PUBLIC_ERROR.get(error, "no_state")
        if args.json:
            print(json.dumps({"schema_version": JSON_SCHEMA_VERSION,
                              "finding": {"id": args.finding},
                              "error": {"code": code}}, indent=2,
                             ensure_ascii=False))
        if code == "target_not_found":
            print(f"no finding {args.finding} in the vault", file=sys.stderr)
            return EXIT_NOT_FOUND
        if code == "ambiguous_target":
            print(f"{args.finding} matches more than one note",
                  file=sys.stderr)
            return _consult.EXIT_AMBIGUOUS
        print("no link state — run `tracelink link` first", file=sys.stderr)
        return EXIT_NO_STATE

    if args.json:
        print(json.dumps(as_json(explanation, args.debug), indent=2,
                         ensure_ascii=False))
    else:
        sys.stdout.write(render_text(explanation))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
