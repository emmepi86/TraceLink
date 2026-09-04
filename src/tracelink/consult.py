"""What the vault already knows about a file — as objects, for any caller.

This is the answer to "am I about to edit something the register has already
written about?", and it is the one part of tracelink that runs inside somebody
else's turn: the Claude Code PostToolUse hook calls it after every edit. That
sets two hard constraints, and they explain every choice in this module.

**It must stay cheap.** The lookup reads the linker's sidecar link-state and
nothing else — no vault walk, no index build, no symbol resolution — and opens
a note file only when that state says the note mentions this exact path, and
then only its head (frontmatter + first heading), never its body. A file no
note links costs one json load.

**It must stay a leaf.** This module imports the standard library and nothing
from tracelink — not `linker`, not `symbol_index`. Importing `linker` costs
~30ms; on a path that runs after every single edit that is the difference
between free and noticeable, and `tests/test_consult_api.py` fails if an
import creeps in. For the same reason the results are plain classes with
`__slots__` rather than dataclasses: `import dataclasses` alone costs ~26ms,
more than everything else this module does.

The state file's name and schema live here, and `linker` imports them from
here, because a private copy in either module is a copy that drifts.

Silence is a result, not an error. Every "nothing to say" carries a reason —
`no-link-state`, `state-schema`, `not-linked` — so a caller (`status`, a
future `doctor`, a human asking why nothing appeared) can tell "the vault
knows nothing about this file" from "the vault could not be read at all".
"""

from __future__ import annotations

import json
import os
import sys

#: The linker's sidecar, written next to the vault. Shared with `linker`.
STATE_FILE = ".tracelink-link-state.json"

#: Schema of that sidecar (0.8.0). An older or newer state is silence, never
#: a guess: the next refresh rewrites it.
STATE_SCHEMA = 3

#: How many notes a consult shows before deferring to CODE-INDEX.md.
MAX_NOTES = 5

#: Severity order; anything unknown sinks below `low`.
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: How far into a note to look for its heading before giving up.
_HEAD_LINES = 200

#: The two things a target can be. A caller may say which; otherwise
#: `_kind_of` decides, deterministically and by one documented rule.
FILE = "file"
SYMBOL = "symbol"

#: Exit codes of `tracelink consult`. Published, therefore fixed: a caller
#: branching on them is entitled to keep working.
EXIT_OK = 0          # answered — including "nothing is written about this"
EXIT_USAGE = 2       # unusable arguments (argparse's own code)
EXIT_NOT_FOUND = 3   # a file target that does not exist in the repository
EXIT_AMBIGUOUS = 4   # the name means two or more things; never guessed
EXIT_NO_STATE = 5    # no link-state, or one this version cannot read

#: Version of the `--json` document. Independent of the sidecar's internal
#: `schema_version`: the protocol we publish and the algorithm that produces
#: it evolve on different clocks, and only one of them is a promise.
JSON_SCHEMA_VERSION = 1


class SymbolHit:
    """One symbol a note links, where it was found."""

    __slots__ = ("name", "line", "path")

    def __init__(self, name, line=None, path=None):
        self.name = name
        self.line = line if isinstance(line, int) else None
        self.path = path

    def __eq__(self, other):
        return (isinstance(other, SymbolHit) and self.name == other.name
                and self.line == other.line and self.path == other.path)

    def __repr__(self):
        return f"SymbolHit({self.name!r}, {self.line!r}, {self.path!r})"


class NoteHit:
    """A note that has something to say about the consulted path.

    `symbols` are the symbols it links *in this file*; `file_anchor` says the
    note names the file itself. A note can hit on either or both.
    """

    __slots__ = ("note_id", "note_file", "status", "severity", "title",
                 "symbols", "file_anchor")

    def __init__(self, note_id, note_file, status, severity, title,
                 symbols=(), file_anchor=False):
        self.note_id = note_id
        self.note_file = note_file
        self.status = status
        self.severity = severity
        self.title = title
        self.symbols = tuple(symbols)
        self.file_anchor = bool(file_anchor)

    @property
    def matches(self):
        """How many distinct reasons this note came up."""
        return len(self.symbols) + (1 if self.file_anchor else 0)

    @property
    def is_open(self):
        return self.status == "open"

    def __repr__(self):
        return (f"NoteHit({self.note_id!r}, status={self.status!r}, "
                f"severity={self.severity!r}, matches={self.matches})")


class ConsultResult:
    """What the vault knows about one target.

    `input` is what the caller asked for, `kind` how it was read (`file` or
    `symbol`) and `resolved` what it became — a repo-relative path, or the
    linked symbol name. `notes` is the ranked, capped list and `hidden` how
    many more matched.

    `silence` is None when notes were found and a reason code otherwise, so
    "nothing is written about this target" stays distinguishable from "the
    state could not be read" and from "the name means two different things".
    `candidates` carries those two things when it does.
    """

    __slots__ = ("input", "kind", "resolved", "notes", "hidden", "silence",
                 "candidates")

    def __init__(self, resolved, notes=(), hidden=0, silence=None,
                 kind=FILE, input=None, candidates=()):
        self.resolved = resolved
        self.kind = kind
        self.input = resolved if input is None else input
        self.notes = tuple(notes)
        self.hidden = hidden
        self.silence = silence
        self.candidates = tuple(candidates)

    #: The historical name for `resolved`, kept because the rendered text
    #: quotes it and the hook has printed it since 0.7.0.
    @property
    def path(self):
        return self.resolved

    def __bool__(self):
        return bool(self.notes)

    @property
    def total(self):
        return len(self.notes) + self.hidden

    def __repr__(self):
        return (f"ConsultResult({self.resolved!r}, kind={self.kind!r}, "
                f"notes={len(self.notes)}, hidden={self.hidden}, "
                f"silence={self.silence!r})")


def _relative(project, target):
    """`target` as a repo-relative, forward-slash path, or None."""
    if not isinstance(target, str) or not target:
        return None
    path = target if os.path.isabs(target) else os.path.join(project, target)
    return os.path.relpath(os.path.realpath(path),
                           os.path.realpath(project)).replace(os.sep, "/")


def _read_state(vault):
    """The link-state dict, or a reason code it could not be used."""
    try:
        with open(os.path.join(vault, STATE_FILE), encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:  # noqa: BLE001 — absent or corrupt state is silence
        return None, "no-link-state"
    if not isinstance(state, dict):
        return None, "no-link-state"
    if state.get("schema_version") != STATE_SCHEMA:
        return None, "state-schema"
    notes = state.get("notes")
    if not isinstance(notes, dict):
        return None, "no-link-state"
    return notes, None


def note_head(vault, note_file):
    """(id, status, severity, title) from the top of one note.

    Only the top: the frontmatter and the first `# ` heading after it. Notes
    can be long, and consult lives inside somebody else's turn.
    """
    stem = note_file[:-3] if note_file.endswith(".md") else note_file
    note_id, status, severity, title = stem, "", "", ""
    try:
        # errors="replace": a note whose bytes are not UTF-8 is damaged, not
        # a reason to raise inside somebody else's edit. Its id and status
        # still read, and the reader is told to open the note anyway.
        with open(os.path.join(vault, note_file), encoding="utf-8",
                  errors="replace") as fh:
            in_front = False
            for lineno, raw in enumerate(fh):
                if lineno > _HEAD_LINES:  # no heading by now — keep the id
                    break
                line = raw.strip()
                if lineno == 0 and line == "---":
                    in_front = True
                    continue
                if in_front:
                    if line == "---":
                        in_front = False
                        continue
                    key, _, value = line.partition(":")
                    key, value = key.strip(), value.strip()
                    if key == "tracelink_id" and value:
                        note_id = value
                    elif key == "status":
                        status = value
                    elif key == "severity":
                        severity = value
                    continue
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
    except OSError:
        pass
    return note_id, status, severity, _tidy_title(title, note_id, severity)


def _tidy_title(title, note_id, severity):
    """`RES-01 — totals ignore tax [HIGH]` → `totals ignore tax`: whoever
    renders this already prints the id and the severity beside it."""
    for sep in (" — ", " – ", " - "):
        if title.startswith(note_id + sep):
            title = title[len(note_id) + len(sep):]
            break
    if severity:
        suffix = "[" + severity + "]"
        if title.lower().endswith(suffix.lower()):
            title = title[:-len(suffix)]
    return title.strip()


def _rank(hit):
    """Open before closed, then severity, then most reasons, then id."""
    return (0 if hit.is_open else 1,
            SEVERITY_RANK.get(hit.severity, len(SEVERITY_RANK)),
            -hit.matches,
            hit.note_id)


def _kind_of(project, target):
    """Read a bare target as a file or as a symbol, by one rule.

    A target that names something on disk is a file; so is anything holding
    a path separator, because no symbol does. Everything else is a symbol.
    The rule asks the filesystem rather than guessing from the spelling, and
    `--file` / `--symbol` settle the case where the caller knows better.
    """
    if "/" in target or os.sep in target:
        return FILE
    path = target if os.path.isabs(target) else os.path.join(project, target)
    return FILE if os.path.exists(path) else SYMBOL


def _linked_names(notes):
    """Every symbol name the state links, mapped to the notes linking it."""
    names = {}
    for note_file, entry in notes.items():
        if not isinstance(entry, dict):
            continue
        linked = entry.get("linked")
        if not isinstance(linked, list):
            continue
        for name in linked:
            if isinstance(name, str) and name:
                names.setdefault(name, []).append(str(note_file))
    return names


def resolve_symbol(notes, target):
    """(name, candidates) for a symbol target, resolved deterministically.

    An exact name wins outright. Otherwise the target is tried as the tail
    of a dotted name — `validate` finds `payments.validate` — and that only
    counts when exactly one name matches. Two matches are two candidates and
    no answer: the caller is told what the name could mean, and nothing is
    chosen for them.
    """
    names = _linked_names(notes)
    if target in names:
        return target, ()
    matches = sorted(n for n in names if n.endswith("." + target))
    if len(matches) == 1:
        return matches[0], ()
    return None, tuple(matches)


def _note_hits(notes, vault, keep):
    """Notes whose linked symbols and file anchors survive `keep`.

    `keep(name, location)` returns the SymbolHit to record or None, and
    `keep.file_anchor(entry)` says whether the note anchors the target file.
    Kept in one place because both target kinds walk the same state and the
    difference between them is only which links count.
    """
    hits = []
    for note_file, entry in notes.items():
        if not isinstance(entry, dict):
            continue
        linked, locations = entry.get("linked"), entry.get("locations")
        if not isinstance(linked, list) or not isinstance(locations, list):
            continue
        symbols = []
        for name, loc in zip(linked, locations):
            if not isinstance(name, str) or not isinstance(loc, dict):
                continue
            hit = keep(name, loc)
            if hit is not None:
                symbols.append(hit)
        file_anchor = keep.file_anchor(entry)
        if not symbols and not file_anchor:
            continue
        note_file = str(note_file)
        note_id, status, severity, title = note_head(vault, note_file)
        hits.append(NoteHit(note_id, note_file, status, severity, title,
                            symbols, file_anchor))
    return hits


def _keep_in_file(rel):
    def keep(name, loc):
        if loc.get("path") == rel:
            return SymbolHit(name, loc.get("line"), rel)
        return None

    def file_anchor(entry):
        anchors = entry.get("files")
        return isinstance(anchors, list) and rel in anchors

    keep.file_anchor = file_anchor
    return keep


def _keep_symbol(wanted):
    def keep(name, loc):
        if name == wanted:
            return SymbolHit(name, loc.get("line"), loc.get("path"))
        return None

    keep.file_anchor = lambda entry: False
    return keep


def consult(project, target, kind=None, vault=None, limit=MAX_NOTES):
    """What the vault knows about `target` — a path or a symbol name.

    `kind` may be `FILE` or `SYMBOL` to say which; left None, `_kind_of`
    decides. Reads only the link-state, then the heads of the notes that
    matched: no index build, no vault walk, no resolution against the
    repository. `limit=None` returns every hit.

    Never raises for a missing, corrupt or wrong-schema state, and never
    picks between two meanings of a name: both are a `ConsultResult` with a
    `silence` reason and no notes.
    """
    if not isinstance(target, str) or not target:
        return ConsultResult("", silence="no-target",
                             input=target if isinstance(target, str) else "")
    if vault is None:
        vault = os.path.join(project, ".tracelink", "vault")
    if kind is None:
        kind = _kind_of(project, target)

    resolved = _relative(project, target) if kind == FILE else target
    notes, reason = _read_state(vault)
    if notes is None:
        return ConsultResult(resolved, silence=reason, kind=kind,
                             input=target)

    if kind == FILE:
        hits = _note_hits(notes, vault, _keep_in_file(resolved))
    else:
        name, candidates = resolve_symbol(notes, target)
        if candidates:
            return ConsultResult(target, silence="ambiguous-symbol",
                                 kind=kind, input=target,
                                 candidates=candidates)
        if name is None:
            return ConsultResult(target, silence="not-linked", kind=kind,
                                 input=target)
        resolved = name
        hits = _note_hits(notes, vault, _keep_symbol(name))

    if not hits:
        return ConsultResult(resolved, silence="not-linked", kind=kind,
                             input=target)
    hits.sort(key=_rank)
    shown = hits if limit is None else hits[:limit]
    return ConsultResult(resolved, shown, len(hits) - len(shown), kind=kind,
                         input=target)


#: Internal silence reason -> the code the JSON document publishes. The two
#: vocabularies are deliberately separate: `_read_state` may learn to say
#: something new tomorrow without that becoming a promise today. A test
#: asserts this mapping is total, so a new reason cannot leak unnamed.
PUBLIC_ERROR = {
    "no-link-state": "no_state",
    "state-schema": "state_schema_unsupported",
    "ambiguous-symbol": "ambiguous_target",
    "no-target": "invalid_target",
    "not-found": "target_not_found",
    "not-linked": None,  # not an error: the vault simply says nothing
}

#: Public error code -> exit code.
EXIT_FOR_ERROR = {
    "no_state": EXIT_NO_STATE,
    "state_schema_unsupported": EXIT_NO_STATE,
    "ambiguous_target": EXIT_AMBIGUOUS,
    "invalid_target": EXIT_USAGE,
    "target_not_found": EXIT_NOT_FOUND,
}


def error_code(result):
    """The published error code for a result, or None when it is an answer.

    A result with no notes is not automatically an error: "nothing is
    written about this file" is a true answer and exits 0.
    """
    if result.silence is None:
        return None
    return PUBLIC_ERROR.get(result.silence, "no_state")


def exit_code(result):
    """The process exit code for a result."""
    code = error_code(result)
    return EXIT_OK if code is None else EXIT_FOR_ERROR.get(code, EXIT_NO_STATE)


def as_json(result):
    """The `--json` document: schema 1, and only what schema 1 promises.

    Deliberately small. Provenance, verification commits and finding
    relations are later ministeps, and an optional field added to schema 1
    breaks nobody — a field published too early and withdrawn does.
    """
    doc = {
        "schema_version": JSON_SCHEMA_VERSION,
        "target": {"input": result.input, "kind": result.kind,
                   "resolved": result.resolved},
        "hits": [{
            "finding_id": note.note_id,
            "status": note.status,
            "severity": note.severity,
            "title": note.title,
            "anchors": _anchors(note, result),
        } for note in result.notes],
    }
    code = error_code(result)
    if code is not None:
        error = {"code": code}
        if result.candidates:
            error["candidates"] = list(result.candidates)
        doc["error"] = error
    return doc


def _anchors(note, result):
    """Why this note came up, as data: the symbols, and the file itself."""
    anchors = [{"kind": SYMBOL, "name": hit.name, "path": hit.path,
                "line": hit.line} for hit in note.symbols]
    if note.file_anchor:
        anchors.append({"kind": FILE, "path": result.resolved})
    return anchors


def render_text(result):
    """The plain-text briefing for a `ConsultResult`, or "" when silent.

    One line per note: id, status/severity, title, and the citation that
    made it match — the symbols with their lines, or the file itself.
    """
    if not result.notes:
        return ""
    if result.kind == SYMBOL:
        return _render_symbol_text(result)
    lines = [f"TraceLink — known findings about this file ({result.path}):"]
    for note in result.notes:
        tag = "/".join(p for p in (note.status, note.severity) if p) or "?"
        head = f"- {note.note_id} [{tag}]" + (f" {note.title}"
                                              if note.title else "")
        if note.symbols:
            syms = ", ".join(f"{s.name} (L{s.line})" if s.line is not None
                             else s.name for s in note.symbols)
            lines.append(f"{head} — symbols: {syms}")
        else:  # a pure file anchor: the file itself is the citation
            lines.append(f"{head} — file: {result.path}")
    lines.append("(full notes: .tracelink/vault/<id>.md — read before "
                 "assuming this area is clean)")
    if result.hidden:
        lines.append(f"…and {result.hidden} more in CODE-INDEX.md")
    return "\n".join(lines)


def _render_symbol_text(result):
    """The same briefing for a symbol target, cited by location."""
    lines = [f"TraceLink — known findings about this symbol "
             f"({result.resolved}):"]
    for note in result.notes:
        tag = "/".join(p for p in (note.status, note.severity) if p) or "?"
        head = f"- {note.note_id} [{tag}]" + (f" {note.title}"
                                              if note.title else "")
        where = ", ".join(_where(hit) for hit in note.symbols)
        lines.append(f"{head} — at {where}" if where else head)
    lines.append("(full notes: .tracelink/vault/<id>.md — read before "
                 "assuming this area is clean)")
    if result.hidden:
        lines.append(f"…and {result.hidden} more in CODE-INDEX.md")
    return "\n".join(lines)


def _where(hit):
    if hit.path and hit.line is not None:
        return f"{hit.path}:L{hit.line}"
    return hit.path or hit.name


def main(argv=None, prog=None) -> int:
    """`tracelink consult <target>` — the vault, asked about one thing.

    argparse is imported here rather than at the top of the module on
    purpose: this module is also the hot path of the edit hook, where
    `import argparse` would cost more than the whole lookup. The CLI pays
    for it; the hook never does.

    With `--json`, stdout carries the JSON document and nothing else — every
    human word goes to stderr — because the first consumers of this command
    are other programs.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog,
        description="What the findings vault already knows about a file or "
                    "a symbol. Reads the link state written by `link`; it "
                    "never rebuilds the index.")
    ap.add_argument("target", help="a path, or a symbol name "
                                   "(`payments.validate`, or `validate` when "
                                   "that is unambiguous)")
    ap.add_argument("--repo", default=".", help="repository root (default .)")
    ap.add_argument("--vault", default=None,
                    help="vault holding the link state "
                         "(default <repo>/.tracelink/vault)")
    ap.add_argument("--file", dest="kind", action="store_const", const=FILE,
                    help="read the target as a path, whatever it looks like")
    ap.add_argument("--symbol", dest="kind", action="store_const",
                    const=SYMBOL, help="read the target as a symbol name")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable document instead")
    ap.set_defaults(kind=None)
    args = ap.parse_args(argv)

    kind = args.kind or _kind_of(args.repo, args.target)
    if kind == FILE and not os.path.exists(
            args.target if os.path.isabs(args.target)
            else os.path.join(args.repo, args.target)):
        result = ConsultResult(_relative(args.repo, args.target) or
                               args.target, silence="not-found", kind=FILE,
                               input=args.target)
    else:
        result = consult(args.repo, args.target, kind=kind, vault=args.vault,
                         limit=None)

    if args.json:
        print(json.dumps(as_json(result), indent=2, ensure_ascii=False))
    elif result.notes:
        print(render_text(result))

    code = error_code(result)
    if code == "ambiguous_target":
        print(f"{args.target!r} could mean: "
              + ", ".join(result.candidates)
              + " — say which with --symbol", file=sys.stderr)
    elif code == "target_not_found":
        print(f"no such file: {args.target}", file=sys.stderr)
    elif code == "no_state":
        print(f"no link state under {args.vault or 'the vault'} — run "
              "`tracelink link` first", file=sys.stderr)
    elif code == "state_schema_unsupported":
        print("the link state was written by another version of tracelink — "
              "run `tracelink link` to rewrite it", file=sys.stderr)
    elif not args.json and not result.notes:
        print(f"nothing recorded about {result.resolved}", file=sys.stderr)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
