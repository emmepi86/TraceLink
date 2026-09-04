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


class SymbolHit:
    """One symbol a note links, at the line it was found on."""

    __slots__ = ("name", "line")

    def __init__(self, name, line=None):
        self.name = name
        self.line = line if isinstance(line, int) else None

    def __eq__(self, other):
        return (isinstance(other, SymbolHit) and self.name == other.name
                and self.line == other.line)

    def __repr__(self):
        return f"SymbolHit({self.name!r}, {self.line!r})"


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
    """What the vault knows about one path.

    `notes` is the ranked, capped list; `hidden` is how many more matched.
    `silence` is None when notes were found and a reason code otherwise, so
    "nothing is written about this file" is distinguishable from "the state
    could not be read".
    """

    __slots__ = ("path", "notes", "hidden", "silence")

    def __init__(self, path, notes=(), hidden=0, silence=None):
        self.path = path
        self.notes = tuple(notes)
        self.hidden = hidden
        self.silence = silence

    def __bool__(self):
        return bool(self.notes)

    @property
    def total(self):
        return len(self.notes) + self.hidden

    def __repr__(self):
        return (f"ConsultResult({self.path!r}, notes={len(self.notes)}, "
                f"hidden={self.hidden}, silence={self.silence!r})")


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


def consult(project, target, vault=None, limit=MAX_NOTES):
    """What the vault knows about `target` (a path, absolute or relative).

    Reads only the link-state, then the heads of the notes that matched.
    Never raises for a missing, corrupt or wrong-schema state: that is a
    `ConsultResult` with a `silence` reason and no notes.
    """
    rel = _relative(project, target)
    if rel is None:
        return ConsultResult("", silence="no-target")
    if vault is None:
        vault = os.path.join(project, ".tracelink", "vault")

    notes, reason = _read_state(vault)
    if notes is None:
        return ConsultResult(rel, silence=reason)

    hits = []
    for note_file, entry in notes.items():
        if not isinstance(entry, dict):
            continue
        linked, locations = entry.get("linked"), entry.get("locations")
        if not isinstance(linked, list) or not isinstance(locations, list):
            continue
        symbols = [SymbolHit(name, loc.get("line"))
                   for name, loc in zip(linked, locations)
                   if isinstance(name, str) and isinstance(loc, dict)
                   and loc.get("path") == rel]
        anchors = entry.get("files")
        file_anchor = isinstance(anchors, list) and rel in anchors
        if not symbols and not file_anchor:
            continue
        note_file = str(note_file)
        note_id, status, severity, title = note_head(vault, note_file)
        hits.append(NoteHit(note_id, note_file, status, severity, title,
                            symbols, file_anchor))

    if not hits:
        return ConsultResult(rel, silence="not-linked")
    hits.sort(key=_rank)
    return ConsultResult(rel, hits[:limit], max(0, len(hits) - limit))


def render_text(result):
    """The plain-text briefing for a `ConsultResult`, or "" when silent.

    One line per note: id, status/severity, title, and the citation that
    made it match — the symbols with their lines, or the file itself.
    """
    if not result.notes:
        return ""
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
