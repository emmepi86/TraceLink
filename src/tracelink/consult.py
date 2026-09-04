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

#: Schema of that sidecar. An older or newer state is silence, never a
#: guess: the next `link` run rewrites it in full.
#:
#: v4 (0.9) records, next to each link, the reason it was made and the
#: evidence behind it, and gives each ambiguous name its candidate list.
#:
#: v6 (0.9) records an ambiguous FILE reference the way an ambiguous symbol
#: has always been recorded. Before it, a file reference matching two files
#: was printed and forgotten: `explain` could not show it, so a refusal to
#: anchor left no trace anywhere a reader would look.
#:
#: v5 (0.9) moved the linker's own cache of the symbol index OUT of this
#: file. It was 94–98% of it, this module never read a byte of it, and the
#: per-edit cost was almost entirely parsing it: 9.8ms at 14k symbols,
#: 548ms at 400k, for a vault that never changed. What is left here is the
#: knowledge `link` compiled — which is all `consult` and `explain` need,
#: and all they are allowed to know about.
STATE_SCHEMA = 6

#: How many notes a consult shows before deferring to CODE-INDEX.md.
MAX_NOTES = 5

#: Severity order; anything unknown sinks below `low`.
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: The managed block a note carries: everything between these two markers
#: belongs to `link`, and nothing else in the note does. They live in the
#: leaf module because two writers need them — `link`, which renders the
#: block, and `split`, which regenerates the note around it — and a second
#: spelling of a marker would silently orphan every block already on disk.
BLOCK_START = "<!-- tracelink:linked-code:start -->"
BLOCK_END = "<!-- tracelink:linked-code:end -->"


def managed_block(text):
    """The block markers and everything between them, or "" if absent."""
    start = text.find(BLOCK_START)
    if start < 0:
        return ""
    end = text.find(BLOCK_END, start)
    if end < 0:
        return ""
    return text[start:end + len(BLOCK_END)]


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

#: Internal resolver reason -> (published state, published method).
#:
#: Two vocabularies on purpose. The resolver's reasons name *how its own
#: branches ran* and are free to change when it is refactored; `state` and
#: `method` name *what kind of conclusion was published*, and are not. A
#: caller may branch on `qualified_symbol`; nobody may branch on
#: `dotted-name`. Tests keep the mapping total in both directions: every
#: reason the resolver can return is mapped, and every method declared here
#: is reached by a real run.
#:
#: `method` is None wherever no link was asserted — an ambiguity has no
#: method, because no conclusion was reached.
RESOLUTION = {
    # a location was chosen
    "frontmatter-override": ("match", "explicit_override"),
    "unique": ("match", "sole_candidate"),
    "qualified-name": ("match", "qualified_symbol"),
    "dotted-name": ("match", "qualified_symbol"),
    "path-in-note": ("match", "path_in_note"),
    "dotted-path": ("match", "path_in_note"),
    # the evidence pointed at more than one place
    "ambiguous": ("ambiguous", None),
    "dotted-ambiguous": ("ambiguous", None),
    "multiple-qualified-names": ("ambiguous", None),
    "multiple-paths-in-note": ("ambiguous", None),
    # a file reference that names more than one file in the tree
    "file-suffix-ambiguous": ("ambiguous", None),
    # the note's own evidence disagrees with itself, or matches nothing
    "qualified-name-and-path-disagree": ("conflict", None),
    "dotted-and-path-disagree": ("conflict", None),
    "dotted-unmatched": ("conflict", None),
    "override-unmatched": ("conflict", None),
}

#: Reasons the resolver can return that describe a NON-event: no link was
#: made and nothing is recorded, so they never reach a published document
#: and deliberately have no public name. `link --explain` shows them.
INTERNAL_ONLY_REASONS = ("insufficient-explicit-evidence",)

#: The three published states. `match` asserted a location; `ambiguous` and
#: `conflict` assert nothing, and say why.
PUBLIC_STATES = ("match", "ambiguous", "conflict")

#: The published methods — what kind of evidence decided.
PUBLIC_METHODS = ("explicit_override", "sole_candidate", "qualified_symbol",
                  "path_in_note")

#: The published kinds of evidence a basis entry can carry.
BASIS_KINDS = ("frontmatter_override", "sole_candidate",
               "qualified_name_in_note", "path_in_note", "dotted_reference",
               "path_suffix_match", "file_reference_in_note")

#: Version of the `--json` document. Independent of the sidecar's internal
#: `schema_version`: the protocol we publish and the algorithm that produces
#: it evolve on different clocks, and only one of them is a promise.
JSON_SCHEMA_VERSION = 1


class InvalidState(Exception):
    """A link state that schema 4 forbids.

    Raised, not swallowed, so the caller fails closed. The alternative —
    reporting `match` with no method and no basis — would publish an
    assertion that cannot explain itself, which is the one thing this
    format exists to prevent.
    """


class Provenance:
    """Why a link exists: the conclusion, and the evidence behind it.

    `state` and `method` are published vocabulary; `reason` is the
    resolver's own word for the branch it took, kept for `--debug` and
    carrying no promise of stability.
    """

    __slots__ = ("state", "method", "basis", "reason")

    def __init__(self, reason, basis=()):
        if reason not in RESOLUTION:
            raise InvalidState(f"unknown resolution reason {reason!r}")
        self.reason = reason
        self.state, self.method = RESOLUTION[reason]
        self.basis = tuple((str(kind), value) for kind, value in basis)
        if self.state == "match" and not self.basis:
            raise InvalidState(f"{reason!r} asserted a match with no basis")

    def __eq__(self, other):
        return (isinstance(other, Provenance) and self.reason == other.reason
                and self.basis == other.basis)

    def __repr__(self):
        return f"Provenance({self.state!r}, {self.method!r}, {self.basis!r})"


class SymbolHit:
    """One symbol a note links, where it was found and why."""

    __slots__ = ("name", "line", "path", "provenance")

    def __init__(self, name, line=None, path=None, provenance=None):
        self.name = name
        self.line = line if isinstance(line, int) else None
        self.path = path
        self.provenance = provenance

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
                 "candidates", "coverage")

    def __init__(self, resolved, notes=(), hidden=0, silence=None,
                 kind=FILE, input=None, candidates=(), coverage=None):
        self.resolved = resolved
        self.kind = kind
        self.input = resolved if input is None else input
        self.notes = tuple(notes)
        self.hidden = hidden
        self.silence = silence
        self.candidates = tuple(candidates)
        #: Where the vault has anchors at all — populated only when this
        #: result has nothing to say, so silence can be read correctly.
        self.coverage = coverage

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
    """`RES-01 — totals ignore tax [HIGH]` → `totals ignore tax`.

    Two decorations come off: the id, which whoever renders this prints
    beside the title anyway, and a trailing severity bracket — *any* of
    them, not only the current one.

    That last part matters. A finding downgraded from HIGH to LOW keeps its
    original `[HIGH]` heading while `severity` says `low`; stripping only
    the matching bracket left the stale one inside the title, and since 0.9
    that title is a field of a published document. A consumer reading
    `severity: "low"` next to `title: "... [HIGH]"` would be right to ask
    which one to believe. The structured field is the answer, so the title
    stops carrying a second, older copy of it.

    Only the four words `SEVERITY:` itself accepts are stripped, and only
    at the end: this is not a title parser, and a title that genuinely ends
    in brackets keeps them.
    """
    for sep in (" — ", " – ", " - "):
        if title.startswith(note_id + sep):
            title = title[len(note_id) + len(sep):]
            break
    stripped = title.rstrip()
    if stripped.endswith("]"):
        head, _, bracket = stripped[:-1].rpartition("[")
        if head and bracket.strip().lower() in SEVERITY_RANK:
            title = head
    return title.strip()


def coverage(notes, target_directory=None):
    """Where the vault has anchors at all, by directory.

    Answers the question a silent consult leaves open. `nothing recorded`
    means one of two very different things —

        the area was looked at and carries no constraints
        nobody has ever written anything about this area

    — and TraceLink cannot tell them apart, so it must not let a reader
    assume the first. Reporting where memory exists at all makes the
    difference visible: a directory with no anchors anywhere in the vault is
    unexamined, not clean.

    Computed only when there is nothing to say, over a state already in
    memory, so the answering path pays nothing for it.
    """
    counts = {}
    for entry in notes.values():
        if not isinstance(entry, dict):
            continue
        paths = [loc.get("path") for loc in entry.get("locations") or []
                 if isinstance(loc, dict)]
        paths += [p for p in entry.get("files") or [] if isinstance(p, str)]
        for path in {p for p in paths if p}:
            directory = path.rsplit("/", 1)[0] if "/" in path else "."
            counts[directory] = counts.get(directory, 0) + 1
    rolled = {}
    for directory, n in counts.items():
        parts = directory.split("/")
        key = "/".join(parts[:2]) if len(parts) > 1 else parts[0]
        rolled[key] = rolled.get(key, 0) + n
    here = None
    if target_directory is not None:
        here = sum(n for d, n in counts.items()
                   if d == target_directory
                   or d.startswith(target_directory + "/"))
    return {"by_directory": dict(sorted(rolled.items(),
                                        key=lambda kv: (-kv[1], kv[0]))),
            "target_directory": target_directory,
            "anchors_here": here}


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
        records = entry.get("provenance")
        if not isinstance(records, list) or len(records) != len(linked):
            raise InvalidState("provenance does not cover every link")
        symbols = []
        for name, loc, record in zip(linked, locations, records):
            if not isinstance(name, str) or not isinstance(loc, dict):
                continue
            hit = keep(name, loc, _provenance(record))
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


def _provenance(record):
    """The `Provenance` a schema 4 record must carry.

    Schema 4 has one invariant and this is where it is enforced: a link
    exists only with the reason and the evidence that produced it. A record
    that is missing, malformed or unknown does not degrade into an
    unexplained match — it invalidates the state, which sends the caller
    back to `tracelink link` rather than forward with an assertion nobody
    can check.
    """
    if not isinstance(record, dict):
        raise InvalidState("a link without a provenance record")
    reason = record.get("reason")
    if not isinstance(reason, str):
        raise InvalidState("a provenance record without a reason")
    basis = record.get("basis")
    if not isinstance(basis, list):
        raise InvalidState(f"{reason!r} has no basis list")
    pairs = [(b[0], b[1]) for b in basis
             if isinstance(b, (list, tuple)) and len(b) == 2
             and isinstance(b[0], str)]
    return Provenance(reason, pairs)


def _keep_in_file(rel):
    def keep(name, loc, provenance):
        if loc.get("path") == rel:
            return SymbolHit(name, loc.get("line"), rel, provenance)
        return None

    def file_anchor(entry):
        anchors = entry.get("files")
        return isinstance(anchors, list) and rel in anchors

    keep.file_anchor = file_anchor
    return keep


def _keep_symbol(wanted):
    def keep(name, loc, provenance):
        if name == wanted:
            return SymbolHit(name, loc.get("line"), loc.get("path"),
                             provenance)
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

    try:
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
    except InvalidState:
        return ConsultResult(resolved, silence="state-invalid", kind=kind,
                             input=target)

    if not hits:
        directory = (resolved.rsplit("/", 1)[0] if kind == FILE
                     and "/" in resolved else None)
        return ConsultResult(resolved, silence="not-linked", kind=kind,
                             input=target,
                             coverage=coverage(notes, directory))
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
    "state-invalid": "state_unusable",
    "not-linked": None,  # not an error: the vault simply says nothing
}

#: Public error code -> exit code.
EXIT_FOR_ERROR = {
    "no_state": EXIT_NO_STATE,
    "state_schema_unsupported": EXIT_NO_STATE,
    "state_unusable": EXIT_NO_STATE,
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
    if result.coverage and not result.notes:
        doc["coverage"] = result.coverage
    code = error_code(result)
    if code is not None:
        error = {"code": code}
        if result.candidates:
            error["candidates"] = list(result.candidates)
        doc["error"] = error
    return doc


def _anchors(note, result, debug=False):
    """Why this note came up, as data: the symbols, and the file itself.

    Each symbol anchor carries the provenance of its link — the published
    `state` and `method`, and the `basis` that produced them. A file anchor
    has none to carry: a note names a path or it does not, and there is no
    disambiguation to explain.
    """
    anchors = []
    for hit in note.symbols:
        anchor = {"kind": SYMBOL, "name": hit.name, "path": hit.path,
                  "line": hit.line}
        if hit.provenance is not None:
            anchor.update(as_provenance(hit.provenance, debug))
        anchors.append(anchor)
    if note.file_anchor:
        anchors.append({"kind": FILE, "path": result.resolved,
                        "state": "match", "method": "file_anchor",
                        "basis": [["file_named_in_note", result.resolved]]})
    return anchors


def as_provenance(provenance, debug=False):
    """The published shape of one link's provenance."""
    doc = {"state": provenance.state,
           "method": provenance.method,
           "basis": [{"kind": kind, "value": value}
                     for kind, value in provenance.basis]}
    if debug:
        # The resolver's own word for the branch it took. Present only when
        # asked for, and promised to nobody.
        doc["internal_reason"] = provenance.reason
    return doc


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


def render_coverage(result, limit=6):
    """What a silent answer is entitled to say — and what it must not.

    Deliberately not a percentage. TraceLink does not know how much memory
    a repository *should* have, so a coverage score would be a number about
    nothing. These are counts of what exists.

    Rendered by the CLI only. The edit hook stays silent when it has nothing
    to say: volunteering "I know nothing about this file" after every edit
    would be noise, and the hook's whole contract is not to speak uninvited.
    """
    if result.notes or not result.coverage:
        return ""
    lines = ["No linked findings.", ""]
    here = result.coverage.get("anchors_here")
    directory = result.coverage.get("target_directory")
    if directory is not None:
        lines.append(f"  {directory + '/':<24} "
                     + (f"{here} anchor(s)" if here else "no recorded anchors"))
    for name, n in list(result.coverage["by_directory"].items())[:limit]:
        if name != directory:
            lines.append(f"  {name + '/':<24} {n} anchor(s)")
    lines += ["",
              "TraceLink cannot conclude that no constraints exist here:",
              "an area nobody has written about and an area known to be "
              "clean look the same."]
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
        detail = render_coverage(result)
        if detail:
            print("", file=sys.stderr)
            print(detail, file=sys.stderr)
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
