#!/usr/bin/env python3
"""Cross-link notes and code, in both directions.

Forward: each note gets a managed `## Linked code` block listing the symbols it
names, with file and line.

Backward: `CODE-INDEX.md` lists, per symbol, the notes that mention it. This is
the direction that is usually missing and usually wanted — *what has been said
about this function* is a question nobody can answer from the code alone.

The matching is LEXICAL. It finds identifiers a note actually spells out. It
will not know that "the enrichment channel" means `enrich_records()` unless the
note says so.

Notes anchor to FILES too, not only symbols. A file reference is a token with
an extension — in backticks always (backticks are a decision), bare in prose
only when it carries at least one `/` (a bare `config.yml` is too weak). It
resolves by path SUFFIX against the real tree under `--repo`, whole components
only, exactly the rule `_dotted_matches_path` applies to dotted names: a
unique match becomes an anchor (`- infra/docker/compose.yml` in the managed
block, a `## Files` table in CODE-INDEX.md), a multiple match is reported
ambiguous and never guessed, a zero match is silence. File anchors do NOT
count toward the per-file hotspot rollup, which counts (note, symbol) links:
a file named as a thing is not a symbol located in a file, and 0.8.0 keeps
the two semantics apart. The tool's own
artifacts — the register the vault manifest names, the vault itself,
`.tracelink/**`, INDEX.md and CODE-INDEX.md — are never anchor targets, even
when cited textually: the tool must not link to its own output.

Incremental relinking: a sidecar `{vault}/.tracelink-link-state.json` records,
per note, a hash of its authored text (plus its frontmatter overrides, which
`matchable()` strips but which change the outcome) and the links its managed
block carries — each symbol with its resolved location, and each file anchor
with a fingerprint of the full resolution of the note's file references. A
later run skips a note only when it can PROVE the outcome would be identical:
same authored text, same options, none of the symbols the note links or
mentions added, removed, or moved, the same file-reference resolution against
the tree as it is NOW, and a managed block that matches, byte for byte, the
one rendered from the cached links. Under that proof the skip does no candidate
scanning and no disambiguation at all. Anything the proof does not cover — a
corrupt or missing state, a different schema, different options, an ambiguous
reference, a block that does not match — falls back to a full relink of the
note, because a stale link is a wrong answer delivered quickly. `--check`
ignores the state entirely (it verifies, so it must look) and never writes
it; `--full` ignores it and rebuilds it. The state is written by every
non-check run whose linking completed — deliberately including one that then
exits 1 under `--require-linked`: that exit is a CI gate on the result, not
a failure of the linking, and the state describes note files that were
really written. A freshness refusal happens before any linking and writes
nothing.

The JSON report carries the additive key `linking.notes_skipped_unchanged`;
the text report prints the same figure. `notes_scanned` remains the total
number of notes in the vault.

Usage:
    python3 link.py --vault vault/ --symbols symbols.json
    python3 link.py --vault vault/ --symbols symbols.json --check
    python3 link.py --vault vault/ --symbols symbols.json --explain
    python3 link.py --vault vault/ --symbols symbols.json --full
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

#: The scan backend's directory excludes, imported rather than copied: a
#: private duplicate would drift, and file anchors must see exactly the tree
#: the symbol index sees.
try:
    from .symbol_index import _SKIP_DIRS as _REPO_SKIP_DIRS
except ImportError:  # running as a loose script
    from symbol_index import _SKIP_DIRS as _REPO_SKIP_DIRS  # type: ignore

#: Names too common to be evidence of anything. A note mentioning "data" should
#: not acquire a link to every table called `data`. Extend with --stopwords.
_DEFAULT_STOP = {
    "data", "value", "values", "result", "results", "config", "settings",
    "status", "content", "context", "request", "response", "record", "records",
    "session", "message", "options", "params", "output", "input", "index",
    "update", "create", "delete", "select", "insert", "filter", "process",
    "handler", "manager", "service", "client", "server", "public", "private",
    "string", "number", "object", "array", "table", "column", "schema",
    "severity", "sections", "related", "title", "notes",
}

#: The default --min-len threshold. Public on purpose: lint applies the same
#: bar to bare identifiers, and importing the number is what keeps the two
#: tools from drifting apart one default at a time.
DEFAULT_MIN_LEN = 7

_SNAKE = re.compile(r"\b(_?[a-z][a-z0-9_]*)\b")
_CAMEL = re.compile(r"\b([A-Z][A-Za-z0-9]+)\b")
_CODE_SPAN = re.compile(r"`([^`\n]{2,80})`")
#: `payments.validate` — identifiers joined by dots, nothing else. A path in
#: backticks must not be mistaken for a dotted name via its extension.
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
#: `self.validate` is syntax, not evidence of location. These prefixes are
#: stripped before any dotted reasoning, so the remainder follows the normal
#: rules for whatever it is — a bare name, or a still-dotted reference.
_SYNTAX_PREFIXES = ("self.", "cls.", "this.")


def _strip_syntax_prefixes(token: str) -> str:
    while token.startswith(_SYNTAX_PREFIXES):
        token = token.split(".", 1)[1]
    return token

_FORWARD_HEADING = "## Linked code"
_BLOCK_START = "<!-- tracelink:linked-code:start -->"
_BLOCK_END = "<!-- tracelink:linked-code:end -->"

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_MANAGED = re.compile(
    re.escape(_BLOCK_START) + r".*?" + re.escape(_BLOCK_END) + r"\n*", re.S)
#: blocks written before the markers existed, so old vaults heal on first run
_LEGACY_BLOCK = re.compile(r"^## Linked code\n\n(?:- .*\n)*\n?", re.M)
#: `Related:` is generated by split.py and must not be matched either
_GENERATED_LINE = re.compile(r"^Related: .*$", re.M)



#: The frontmatter block, group 1 its inner text. Public: status parses the
#: same frontmatter, and it should not have to reach for a private name.
FM_BLOCK = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_FM_BLOCK = FM_BLOCK  # private alias kept for internal compatibility
_OWNED = re.compile(r"^tracelink_schema:\s*1\s*$", re.M)


def is_owned_note(text: str) -> bool:
    """Ownership lives in the frontmatter, not anywhere in the file.

    A substring search made a hand-written note that merely MENTIONS
    `tracelink_schema:` look owned — and therefore rewritable. The guarantee is
    only worth stating if the marker is structural.
    """
    m = _FM_BLOCK.match(text)
    return bool(m and _OWNED.search(m.group(1)))


def matchable(text: str) -> str:
    """The part of a note a human actually wrote.

    tracelink must never read its own output, and this is the single most
    important line in the tool.

    Frontmatter carries `status` and `severity`. The managed block carries the
    symbols found on the previous run. Scanning either makes the tool match
    itself: the first published demo linked both example notes to `severity` —
    a function inside `split.py` — and reported "2/2 notes linked" as though it
    had found the symbols named in the findings. Every statistic downstream was
    then formally correct and substantively false.

    It also makes links immortal. A symbol removed from the prose survives
    forever, because the next run rediscovers it in the block written by the
    last one.
    """
    text = _FRONTMATTER.sub("", text)
    text = _MANAGED.sub("", text)
    text = _LEGACY_BLOCK.sub("", text)
    text = _GENERATED_LINE.sub("", text)
    return text


def strip_managed(text: str) -> str:
    """Remove the managed block, leaving everything a human wrote."""
    return _LEGACY_BLOCK.sub("", _MANAGED.sub("", text))


def candidates(text: str, symbols: Dict[str, str], min_len: int,
               stop: set) -> List[Tuple[str, str]]:
    """Symbols the note names, ranked by how deliberate the reference is.

    A name inside backticks is a decision; a bare word of the same spelling may
    be prose. Ranking before truncation matters — capping an alphabetically
    sorted list throws away the strongest evidence for the weakest.

    A dotted span (`payments.validate`) resolves to its tail: the index is
    keyed by simple name, but the author spelled the reference out, so it
    counts as inline code and bypasses --min-len like any other code span.
    The prefix is not discarded — disambiguate() reads it from the text.
    """
    ranked: Dict[str, str] = {}
    for span in _CODE_SPAN.findall(text):
        token = _strip_syntax_prefixes(span.strip().rstrip("()").lstrip("."))
        if token in symbols:
            ranked[token] = "inline-code"
        elif _DOTTED.fullmatch(token):
            tail = token.rsplit(".", 1)[1]
            if tail in symbols:
                ranked[tail] = "inline-code"
    for m in _SNAKE.findall(text):
        if m in ranked or m in stop or len(m) < min_len:
            continue
        if m in symbols:
            ranked[m] = "identifier"
    for m in _CAMEL.findall(text):
        if m in ranked or m.lower() in stop or len(m) < min_len:
            continue
        if m in symbols:
            ranked[m] = "identifier"
    order = {"inline-code": 0, "identifier": 1}
    return sorted(ranked.items(), key=lambda kv: (order[kv[1]], kv[0]))


# --------------------------------------------------------------------------- #
# file anchors — notes that name a file, not a symbol
# --------------------------------------------------------------------------- #

#: A path-shaped token whose LAST segment carries an extension: a word
#: character, a dot, then the extension. `infra/docker/compose.yml`,
#: `deploy-stage.sh`, `.env.example` — but not a bare dotfile like `.env`,
#: which has no extension to speak of.
_FILE_TOKEN = re.compile(r"(?:[\w.\-]+/)*[\w.\-]*\w\.[A-Za-z0-9][\w\-]*")
#: The same token bare in prose, requiring at least one `/`. The lookbehind
#: refuses a match that starts mid-token — which also keeps URLs out, since
#: `host.com/x.yml` always has a `.` or `/` right before any candidate start.
_FILE_BARE = re.compile(
    r"(?<![\w./\\-])((?:[\w.\-]+/)+[\w.\-]*\w\.[A-Za-z0-9][\w\-]*)")


def _norm_ref(ref: str) -> str:
    """`./compose.yml` is spelling, not location."""
    while ref.startswith("./"):
        ref = ref[2:]
    return ref


def file_refs(text: str) -> List[Tuple[str, str]]:
    """File references the author spelled out, with how deliberately.

    In backticks any extension-bearing token counts — backticks are a
    decision. Bare in prose the bar is higher: at least one `/`, because a
    bare `config.yml` names a kind of file, not a file. The real filter is
    neither: it is existence in the repository, applied by resolution.
    """
    refs: Dict[str, str] = {}
    for span in _CODE_SPAN.findall(text):
        token = span.strip()
        if _FILE_TOKEN.fullmatch(token):
            refs.setdefault(_norm_ref(token), "inline-code")
    for m in _FILE_BARE.findall(text):
        refs.setdefault(_norm_ref(m), "bare")
    return list(refs.items())


def _manifest_register(vault: str) -> Optional[str]:
    """The register basename the vault manifest records, or None."""
    try:
        with open(os.path.join(vault, ".tracelink-manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        reg = ((man.get("register") or {}).get("source")
               or man.get("generated_from"))
        return os.path.basename(reg) if isinstance(reg, str) and reg else None
    except Exception:  # noqa: BLE001 — no manifest shape may break linking
        return None


def repo_file_map(repo: str, vault: Optional[str] = None,
                  register: Optional[str] = None) -> Dict[str, List[str]]:
    """basename -> sorted relative paths of every file a reference may
    anchor to. One full walk per run — the same price the indexer pays.

    Exclusions, because the tool must never link to its own output:
    the scan backend's skip dirs and every hidden directory (which covers
    `.tracelink/**`), the vault subtree, INDEX.md and CODE-INDEX.md by name,
    the link-state sidecar, and the register by the basename the manifest
    records — a register elsewhere in the tree is still a register.
    """
    root = os.path.realpath(repo)
    vault_real = os.path.realpath(vault) if vault else None
    exclude_names = {"INDEX.md", "CODE-INDEX.md", STATE_FILE,
                     ".tracelink-manifest.json"}
    if register:
        exclude_names.add(register)
    out: Dict[str, List[str]] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in _REPO_SKIP_DIRS and not d.startswith(".")]
        if vault_real is not None:
            real_dir = os.path.realpath(dirpath)
            if real_dir == vault_real or real_dir.startswith(
                    vault_real + os.sep):
                dirs[:] = []
                continue
        for fn in files:
            if fn in exclude_names:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn),
                                  root).replace(os.sep, "/")
            out.setdefault(fn, []).append(rel)
    for paths in out.values():
        paths.sort()
    return out


def resolve_file_ref(ref: str,
                     files_by_name: Dict[str, List[str]]) -> List[str]:
    """Every repo path whose final components equal the reference's — whole
    segments only, so `compose.yml` never matches `compose.prod.yml`, the
    same rule `_dotted_matches_path` applies. Case-sensitive: near enough
    is a guess."""
    parts = ref.split("/")
    return [p for p in files_by_name.get(parts[-1], ())
            if p.split("/")[-len(parts):] == parts]



def normalise(symbols: dict) -> dict:
    """Accept v1 (name -> "path:line") and v2 (name -> [locations]).

    Everything downstream sees a list, so ambiguity is representable rather than
    silently collapsed.
    """
    out = {}
    for name, val in symbols.items():
        if isinstance(val, list):
            out[name] = val
        else:
            txt = str(val)
            path, _, line = txt.rpartition(":")
            out[name] = [{"path": path or txt,
                          "line": line.lstrip("L") if path else None,
                          "kind": "", "qualified_name": name}]
    return out


def fmt(loc: dict) -> str:
    return f"{loc['path']}:L{loc['line']}" if loc.get("line") else str(loc["path"])


def _dotted_refs(name: str, text: str) -> list:
    """Dotted spellings of `name` the note contains: `payments.validate` is a
    reference to the tail carrying its own disambiguating prefix.

    Sub-chains count: in `payments.validate.errors` the boundary after the
    name is satisfied by the following dot, so `payments.validate` is
    extracted as a reference to `validate` — an attribute of the thing is
    still a naming of the thing.

    `self.`, `cls.` and `this.` are syntax, not evidence of location. They
    are stripped before anything else, so `self.validate` carries exactly the
    information of a bare `validate`: no dotted reference at all, the normal
    min-len and disambiguation rules apply.
    """
    pat = re.compile(r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)+" + re.escape(name) + r")\b")
    refs = set()
    for ref in pat.findall(text):
        ref = _strip_syntax_prefixes(ref)
        if "." in ref:
            refs.add(ref)
    return sorted(refs)


def _dotted_matches_qualified(loc: dict, ref: str) -> bool:
    """The reference and the qualified name may record different depths of
    the same dotted path: the note may spell more than the index recorded
    (`app.payments.validate` against a registered `payments.validate`) or
    less (`payments.validate` against `app.payments.validate`). Containment
    on whole segments, in either direction, is a match. A qualified name
    with no prefix of its own says nothing about location and is never
    matched by containment.
    """
    q = loc.get("qualified_name")
    if not q:
        return False
    return q == ref or q.endswith("." + ref) or ("." in q and ref.endswith("." + q))


def _dotted_matches_path(path: str, ref: str) -> bool:
    """The dotted segments against the final path segments, extension dropped:
    `payments.validate` matches `.../payments.py`, `.../payments/__init__.py`,
    `.../payments/validate.py` or `.../payments/validate/__init__.py` — a
    trailing `__init__` names its package, so it is dropped before comparing.
    Case-sensitive — near enough is a guess.
    """
    segments = ref.split(".")
    parts = os.path.splitext(str(path).replace("\\", "/"))[0].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    prefix = segments[:-1]
    return parts[-len(prefix):] == prefix or parts[-len(segments):] == segments


def disambiguate(name: str, locations: list, text: str, overrides: dict):
    """Pick a location only when the evidence points at exactly one.

    Two candidates supported by the note are not a tie to break — they are the
    author naming both. Returning the first was the same defect as v1 resolving
    duplicates by filesystem order, moved one level up.

    An explicit override may win over textual evidence because it is a
    structured decision. A qualified name and a path are both authorial
    evidence of the same weight: when they disagree, that is a conflict to
    report, not a precedence to invent.

    A dotted spelling of the name (`payments.validate`) is the author naming
    the prefix on purpose. It is matched against qualified names first and,
    when no qualified name matches (the scan backend records none), against
    path suffixes. `self.`, `cls.` and `this.` are syntax, not a prefix:
    `self.validate` is treated exactly as a bare `validate`. Reason codes:

        dotted-name             the prefix names exactly one qualified name
        dotted-path             the prefix names exactly one path suffix
        dotted-ambiguous        the prefix still matches two or more locations
        dotted-unmatched        the prefix matches no location of the tail —
                                contradictory evidence, so not even the bare
                                tail is linked
        dotted-and-path-disagree  a path cited in the note points at a
                                location outside everything the prefix
                                matches
    """
    if name in overrides:
        want = overrides[name]
        for loc in locations:
            if want in (loc.get("qualified_name"), loc["path"], fmt(loc)):
                return loc, "frontmatter-override"
        return None, "override-unmatched"

    refs = _dotted_refs(name, text)
    by_dotted, dotted_how = [], None
    if refs:
        by_dotted = [l for l in locations
                     if any(_dotted_matches_qualified(l, r) for r in refs)]
        dotted_how = "dotted-name"
        if not by_dotted:
            by_dotted = [l for l in locations
                         if any(_dotted_matches_path(l["path"], r) for r in refs)]
            dotted_how = "dotted-path"
        if not by_dotted:
            return None, "dotted-unmatched"

    if len(locations) == 1:
        return locations[0], "unique"

    by_qualified = [l for l in locations
                    if l.get("qualified_name") and l["qualified_name"] != name
                    and l["qualified_name"] in text]
    by_path = [l for l in locations if l["path"] in text]

    if len(by_qualified) > 1:
        return None, "multiple-qualified-names"
    if len(by_path) > 1:
        return None, "multiple-paths-in-note"
    if by_qualified and by_path and by_qualified[0] is not by_path[0]:
        return None, "qualified-name-and-path-disagree"
    if by_dotted and len(by_path) == 1 and all(l is not by_path[0] for l in by_dotted):
        return None, "dotted-and-path-disagree"
    if len(by_qualified) == 1:
        return by_qualified[0], "qualified-name"
    if len(by_path) == 1:
        return by_path[0], "path-in-note"
    if refs:
        if len(by_dotted) == 1:
            return by_dotted[0], dotted_how
        return None, "dotted-ambiguous"
    return None, "ambiguous"


_OVERRIDE = re.compile(r"^\s*tracelink:\s*$\n((?:\s+\w[\w.]*:\s*\S+\s*\n)+)", re.M)


def read_overrides(text: str) -> dict:
    """`tracelink:` mapping in the frontmatter, when the author has decided."""
    m = _OVERRIDE.search(text[:2000])
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.strip().partition(":")
            if k and v.strip() and k != "symbols":
                out[k.strip()] = v.strip()
    return out


def render_block(links, symbols, files=()) -> str:
    """Symbols first, exactly as before; file anchors after, as pure paths —
    no backticks, no line, because the anchor is the file itself."""
    rows = [f"- `{name}` — {fmt(loc)}" for name, loc, _why in links]
    rows += [f"- {path}" for path in files]
    body = "\n".join(rows)
    return f"{_BLOCK_START}\n{_FORWARD_HEADING}\n\n{body}\n{_BLOCK_END}\n"


def apply_block(text: str, links: List[Tuple[str, str]],
                symbols: Dict[str, str], files=()) -> str:
    """Always rebuilt from the current match; removed when nothing matches.

    The previous implementation only ran when there were hits, so a note whose
    last symbol left the prose kept its stale block indefinitely.
    """
    stripped = strip_managed(text)
    if not links and not files:
        return stripped
    block = render_block(links, symbols, files)
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        cut = stripped.find("\n", end + 1) + 1 if end != -1 else 0
        return stripped[:cut] + "\n" + block + stripped[cut:]
    return block + "\n" + stripped


def write_atomic(path: str, content: str) -> None:
    tmp = path + ".tracelink.tmp"
    with open(tmp, "w") as fh:
        fh.write(content)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# incremental state — proof, not memory
# --------------------------------------------------------------------------- #
#
# The state is only ever an optimisation: every fact it asserts must be
# re-checkable from the inputs of the current run, and anything it cannot
# prove is relinked in full. Naming follows `.tracelink-manifest.json`.

#: Sidecar filename. Public: status locates the same file.
STATE_FILE = ".tracelink-link-state.json"
_STATE_FILE = STATE_FILE  # private alias kept for internal compatibility
#: v2 caches each link's resolved location alongside its name, so the skip
#: path renders the managed block from the state instead of disambiguating
#: again. v3 adds, per note, the `files` array of resolved file anchors and
#: a `files_fingerprint` over the full resolution of the note's file
#: references. A v1 or v2 state — or any other version — is discarded whole:
#: the schema-mismatch path IS the migration, one full relink.
_STATE_SCHEMA = 3


def sha256_text(data) -> str:
    """`sha256:<hex>` of text or bytes — the fingerprint spelling every
    tracelink state file uses. Public: status recomputes the symbols
    fingerprint with the same function that wrote it."""
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return "sha256:" + hashlib.sha256(data).hexdigest()


_sha = sha256_text  # private alias kept for internal compatibility


def note_fingerprint(body: str, overrides: dict) -> str:
    """Hash of everything about a note that can change its links.

    The overrides are included on purpose: they live in the frontmatter,
    which `matchable()` strips — a hash of the body alone would let an
    author repoint a `tracelink:` override and see nothing happen, which is
    precisely the stale link this state must never produce.
    """
    return _sha(body + "\0" + json.dumps(overrides, sort_keys=True))


def options_fingerprint(min_len: int, max_links: int, stop: set) -> str:
    return _sha(json.dumps([min_len, max_links, sorted(stop)]))


def location_fingerprint(locations: list) -> str:
    """Covers path, line, kind and qualified name — a definition that moves
    one line changes this hash, and the notes that link it get relinked so
    their `path:Lline` stays true."""
    return _sha(json.dumps(locations, sort_keys=True, default=str))


def files_fingerprint(outcomes: list) -> str:
    """Hash of one note's file-reference resolutions: every reference with
    the FULL list of paths it matches, not just the unique winners.

    The full list matters: a reference that matched nothing and now matches
    two files gains an ambiguity warning without gaining an anchor, and a
    fingerprint over anchors alone would let the skip hide that warning.

    This is recomputed on EVERY run for every note, cached or not — the
    repository tree is an input the content hash cannot see. The cost is one
    regex pass over the note body plus dict lookups against a basename map
    built once per run; correctness over a changing tree is worth exactly
    that much.
    """
    return _sha(json.dumps(outcomes))


def load_state(path: str) -> Optional[dict]:
    """The state on disk, or None for anything less than fully well-formed.

    Absent, unreadable, invalid JSON, wrong schema, wrong shape anywhere —
    all collapse to the same answer, because a partially trusted state is a
    partially wrong vault. None means: relink everything.
    """
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != _STATE_SCHEMA:
        return None
    if not isinstance(raw.get("symbols_fingerprint"), str):
        return None
    if not isinstance(raw.get("options_fingerprint"), str):
        return None
    locs = raw.get("symbol_locations")
    if not isinstance(locs, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in locs.items()):
        return None
    notes = raw.get("notes")
    if not isinstance(notes, dict):
        return None
    for name, entry in notes.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            return None
        if not isinstance(entry.get("content_hash"), str):
            return None
        linked = entry.get("linked")
        if not isinstance(linked, list) or not all(
                isinstance(s, str) for s in linked):
            return None
        locations = entry.get("locations")
        if not isinstance(locations, list) or len(locations) != len(linked):
            return None
        for loc in locations:
            if not isinstance(loc, dict) or not isinstance(loc.get("path"), str):
                return None
            if not isinstance(loc.get("line"), (str, int, type(None))):
                return None
        files = entry.get("files")
        if not isinstance(files, list) or not all(
                isinstance(p, str) for p in files):
            return None
        if not isinstance(entry.get("files_fingerprint"), str):
            return None
    return raw


def cached_links(entry: dict) -> list:
    """The links a skipped note carries, straight from the state.

    No disambiguation happens here — that is the point of caching the
    resolved location next to each name. The skip decision has already proven
    the inputs unchanged: same authored text and overrides (the content
    hash), same location list for every symbol the note links or mentions
    (`symbol_locations` through the changed-names check). Under that proof
    the cached resolution IS the current resolution. The caller still renders
    the managed block from these links and compares it byte for byte with
    the disk, so a state that lies — or a block edited by hand — falls back
    to a full recompute of the note rather than being believed.
    """
    return [(name, {"path": loc["path"], "line": loc.get("line")}, "cached")
            for name, loc in zip(entry["linked"], entry["locations"])]


# --------------------------------------------------------------------------- #
# freshness — is this index still about the repository in front of us?
# --------------------------------------------------------------------------- #

class Freshness:
    """Four states, because a boolean cannot express "I do not know".

    The distinctions that matter, each learned from a way of being wrong:

        same commit        != same files      (the working tree can be dirty)
        clean repository   != same commit
        provenance present != freshness verified
        fresh              != complete
        unknown            != fresh

    `stale` requires positive evidence of divergence. `fresh` requires positive
    evidence of correspondence. Everything else is `unknown`, and `unknown` is
    reported as such rather than rounded to the comfortable side.
    """

    def __init__(self, status, reasons=None, **fields):
        self.status = status
        self.reasons = reasons or []
        self.__dict__.update(fields)

    def as_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != "reasons"}
        d["reasons"] = self.reasons
        return d


def verify_freshness(payload, repo, index_path=None):
    """Compare an index against the repository it claims to describe."""
    try:
        from .symbol_index import (discover_scope as _ds, fingerprint as _fp,
                                   repo_state as _rs)
    except ImportError:  # running as a loose script
        from symbol_index import (discover_scope as _ds, fingerprint as _fp,  # type: ignore
                                  repo_state as _rs)
    except Exception:  # noqa: BLE001
        return Freshness("unknown", ["indexer-unavailable"])

    # A v1 index is a bare mapping with no envelope. It must stay loadable —
    # rejecting it as invalid would break every vault written before v2 and
    # punish the user for our schema history.
    if "symbols" not in payload:
        if payload and all(isinstance(v, (str, list)) for v in payload.values()):
            return Freshness("unknown", ["legacy-index-without-provenance"],
                             partial=False, indexed_commit=None, current_commit=None)
        return Freshness("invalid", ["not-a-symbol-index"])
    if not isinstance(payload.get("symbols"), dict):
        return Freshness("invalid", ["symbols-not-a-mapping"])

    indexing = payload.get("indexing") or {}
    partial = bool(indexing.get("partial"))

    repo_meta = payload.get("repository") or {}
    idx_commit = repo_meta.get("commit") or payload.get("repo_commit")
    idx_dirty = repo_meta.get("dirty")
    idx_fp = repo_meta.get("fingerprint")

    if idx_commit is None and idx_fp is None:
        return Freshness("unknown", ["legacy-index-without-provenance"],
                         partial=partial, indexed_commit=None, current_commit=None)

    _vcs, cur_commit, cur_dirty = _rs(os.path.realpath(repo))
    common = dict(partial=partial, indexed_commit=idx_commit,
                  current_commit=cur_commit, indexed_dirty=idx_dirty,
                  current_dirty=cur_dirty, indexed_fingerprint=idx_fp)

    # The fingerprint is the strongest evidence: it covers uncommitted work and
    # repositories with no VCS at all, so it is checked first and it decides.
    if idx_fp:
        scope = indexing.get("scope")
        if not scope:
            # A v3 index from 0.4.0/0.4.1 recorded a scoped fingerprint without
            # recording the scope, so it cannot be reproduced. Saying so is the
            # honest answer; hashing the whole tree instead would compare two
            # different sets and call every fresh index stale, which is what
            # 0.4.1 did.
            return Freshness("unknown", ["fingerprint-scope-not-recorded"], **common)
        files, confidence = _ds(os.path.realpath(repo), scope)
        if confidence != "exact":
            return Freshness("unknown", ["scope-cannot-be-rebuilt"], **common)
        cur_fp, _n, _w = _fp(os.path.realpath(repo), files=files)
        common["current_fingerprint"] = cur_fp
        if cur_fp == idx_fp:
            return Freshness("fresh", ["fingerprint-match"], **common)
        return Freshness("stale", ["fingerprint-mismatch"], **common)

    # v2 index: a commit and nothing else. It can prove divergence but not
    # correspondence, because the working tree is invisible to it.
    if cur_commit is None:
        return Freshness("unknown", ["git-unavailable-or-not-a-repository"], **common)
    if idx_commit != cur_commit:
        return Freshness("stale", ["commit-mismatch"], **common)
    if cur_dirty:
        return Freshness("stale", ["working-tree-modified"], **common)
    return Freshness("unknown", ["commit-match-without-fingerprint"], **common)


def render_freshness(f, fmt="text"):
    if fmt == "json":
        return None
    lines = [f"index_freshness:   {f.status}"]
    for r in f.reasons:
        lines.append(f"reason:            {r}")
    for label, attr in (("indexed_commit", "indexed_commit"),
                        ("current_commit", "current_commit")):
        v = getattr(f, attr, None)
        if v:
            lines.append(f"{label}:    {v[:12]}")
    if getattr(f, "partial", False):
        lines.append("index_completeness: partial — it does not cover the whole repository")
    if f.status == "stale":
        lines.append("")
        lines.append("Run the symbol indexer again before trusting generated links.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-link notes and code.")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--max-links", type=int, default=8,
                    help="cap per note, applied AFTER ranking (default 8)")
    ap.add_argument("--min-len", type=int, default=DEFAULT_MIN_LEN,
                    help="minimum identifier length outside backticks "
                         f"(default {DEFAULT_MIN_LEN})")
    ap.add_argument("--report-unlinked", action="store_true",
                    help="list every note that linked nothing, with the reason")
    ap.add_argument("--require-linked", action="store_true",
                    help="exit 1 if any note linked nothing (for CI)")
    ap.add_argument("--stopwords", default="", help="comma-separated extra names to ignore")
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing; exit 1 if stale")
    ap.add_argument("--full", action="store_true",
                    help="ignore the incremental state and reprocess every note")
    ap.add_argument("--explain", action="store_true",
                    help="print why each link was made")
    ap.add_argument("--repo", default=".",
                    help="repository the index claims to describe (default .)")
    ap.add_argument("--freshness", choices=["warn", "require", "ignore"], default="warn",
                    help="what to do when the index no longer matches the repo")
    ap.add_argument("--require-fresh-index", action="store_true",
                    help="alias for --freshness require")
    ap.add_argument("--allow-partial-index", action="store_true",
                    help="accept an index whose scan did not complete")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    with open(args.symbols, "rb") as fh:
        raw_symbols = fh.read()
    payload = json.loads(raw_symbols)
    mode = "require" if args.require_fresh_index else args.freshness
    fresh = None
    if mode != "ignore":
        fresh = verify_freshness(payload, args.repo, args.symbols)
        if args.format == "text":
            print(render_freshness(fresh))
            print()
        # The diagnostic is emitted even when we refuse to link. A CI consumer
        # asking for JSON and receiving prose on stderr has been given nothing
        # it can act on — the machine-readable promise has to hold on the
        # failure path especially, since that is the path it exists for.
        refusal = None
        if fresh.status == "invalid":
            refusal = ("invalid-index", 2)
        elif mode == "require":
            if fresh.status in ("stale", "unknown"):
                refusal = (f"freshness-{fresh.status}", 1)
            elif getattr(fresh, "partial", False) and not args.allow_partial_index:
                refusal = ("partial-index", 1)
        if refusal:
            reason, code = refusal
            if args.format == "json":
                print(json.dumps({"ok": False, "exit_reason": reason,
                                  "freshness": fresh.as_dict(), "linking": None}, indent=1))
            else:
                print(f"refusing to link: {reason}", file=sys.stderr)
                if fresh.status == "stale":
                    print("\nSuggested action:\n  python3 scripts/symbols.py "
                          f"--repo {args.repo} --out {args.symbols}", file=sys.stderr)
            return code

    symbols = normalise(payload.get("symbols") or payload)
    stop = set(_DEFAULT_STOP) | {s.strip() for s in args.stopwords.split(",") if s.strip()}

    # Incremental state. `--check` verifies, so it must look at everything:
    # the state is neither read nor written. `--full` distrusts it on request.
    # A state whose options differ described a different linker and is
    # discarded whole — half-trusting it would mix two rule sets in one vault.
    symbols_fp = _sha(raw_symbols)
    opts_fp = options_fingerprint(args.min_len, args.max_links, stop)
    current_locations = {n: location_fingerprint(l) for n, l in symbols.items()}
    state_path = os.path.join(args.vault, _STATE_FILE)
    state = None
    if not args.check and not args.full:
        state = load_state(state_path)
        if state and state["options_fingerprint"] != opts_fp:
            state = None
    symbols_changed = bool(state) and state["symbols_fingerprint"] != symbols_fp
    changed_names: set = set()
    mention_pat = None
    if state and symbols_changed:
        old_locations = state["symbol_locations"]
        # Added, removed, and moved names all count as changed: a removed name
        # can turn an ambiguity or an unlinked reason into something else, so
        # notes that merely MENTION it are relinked too — more conservative
        # than strictly necessary, and stale-proof.
        changed_names = ({n for n in old_locations
                          if current_locations.get(n) != old_locations[n]} |
                         {n for n in current_locations if n not in old_locations})
        if changed_names:
            mention_pat = re.compile(
                r"\b(?:" + "|".join(map(re.escape, sorted(changed_names))) + r")\b")
    state_notes = state.get("notes", {}) if state else {}

    # RES-OWNERSHIP: only notes tracelink generated. Pointing --vault at a
    # directory of hand-written markdown must never rewrite it.
    notes, skipped = [], 0
    for p in sorted(os.listdir(args.vault)):
        if not p.endswith(".md") or p in ("INDEX.md", "CODE-INDEX.md"):
            continue
        if is_owned_note(open(os.path.join(args.vault, p), errors="replace").read()):
            notes.append(p)
        else:
            skipped += 1
    if skipped:
        print(f"warning: skipped {skipped} markdown file(s) without tracelink_schema")
    if not notes:
        print(f"no notes in {args.vault}")
        return 1

    # File anchoring resolves against the real tree, so the map is built once
    # per run, with the tool's own artifacts excluded at the source.
    files_by_name = repo_file_map(args.repo, args.vault,
                                  _manifest_register(args.vault))

    backward: Dict[str, List[str]] = collections.defaultdict(list)
    # (note, symbol) pairs per file, from links actually written. The rollup
    # answers "which file do the notes keep pointing at" — counting ambiguous
    # candidates here would count links the linker refused to make.
    file_rollup: Dict[str, set] = collections.defaultdict(set)
    # File anchors actually written, per anchored file. Feeds the `## Files`
    # table of CODE-INDEX and NOTHING else: file anchors deliberately do not
    # count toward the per-file hotspot rollup above, which counts
    # (note, symbol) links — a file named as a thing is not a symbol located
    # in a file, and 0.8.0 does not mix the two semantics.
    file_notes: Dict[str, set] = collections.defaultdict(set)
    # File references that matched more than one path, with their candidates:
    # reported in Ambiguous references next to the symbols, never guessed.
    ambiguous_files: Dict[str, List[str]] = collections.defaultdict(list)
    ambiguous_file_candidates: Dict[str, List[str]] = {}
    # Symbols a note referenced but could not be linked to one place. Kept per
    # symbol so the inverse index can say "referenced, ambiguous" instead of
    # dropping the reference entirely — a note asking about `row_fingerprint`
    # got no answer precisely where the answer was two answers.
    ambiguous_refs: Dict[str, List[str]] = collections.defaultdict(list)
    # Why a note linked nothing. "No link" has several causes and they call for
    # different actions: rewrite the finding, widen the index, or disambiguate.
    unlinked: List[Dict[str, str]] = []
    # Rebuilt from scratch every run: entries for deleted notes fall away, and
    # only notes whose analysis was clean (no ambiguity) are recorded — an
    # ambiguous note is a warning, and warnings are re-earned, not cached.
    new_state_notes: Dict[str, dict] = {}
    scanned = with_matches = modified = total_links = ambiguous = 0
    files_linked = skipped_unchanged = 0

    for name in notes:
        path = os.path.join(args.vault, name)
        text = open(path, errors="replace").read()
        scanned += 1
        body = matchable(text)
        overrides = read_overrides(text)
        note_hash = note_fingerprint(body, overrides)

        # File references and their resolution, computed for EVERY note —
        # even one about to be skipped, because the repository tree is an
        # input the content hash cannot see. See files_fingerprint for the
        # cost accounting.
        refs = file_refs(body)
        outcomes = [(ref, resolve_file_ref(ref, files_by_name))
                    for ref, _why in refs]
        files_fp = files_fingerprint(outcomes)

        # The skip decision. Default is to relink; every clause below is a
        # positive proof that relinking would reproduce the file byte for
        # byte. When the proof fails at any step, we fall through to the
        # full pipeline rather than reason about why.
        cached = None
        entry = state_notes.get(name)
        if entry is not None and entry["content_hash"] == note_hash:
            affected = symbols_changed and (
                bool(changed_names.intersection(entry["linked"]))
                or (mention_pat is not None and mention_pat.search(body)))
            if not affected and entry["files_fingerprint"] == files_fp:
                cached = cached_links(entry)
                if apply_block(text, cached, symbols, entry["files"]) != text:
                    cached = None  # the block on disk is not what was promised

        note_ambiguous = []
        note_file_ambiguous = []
        if cached is not None:
            skipped_unchanged += 1
            links = cached
            note_files = list(entry["files"])
        else:
            links = []
            for sym, why in candidates(body, symbols, args.min_len, stop):
                loc, how = disambiguate(sym, symbols[sym], body, overrides)
                if loc is None:
                    note_ambiguous.append((sym, how))
                    continue
                links.append((sym, loc, f"{why}/{how}"))
            links = links[: args.max_links]
            note_files = []
            for ref, matches in outcomes:
                if len(matches) == 1:
                    if matches[0] not in note_files:
                        note_files.append(matches[0])
                elif len(matches) > 1:
                    note_file_ambiguous.append((ref, matches))
            # --max-links caps the TOTAL, symbols first — the block's own
            # order is the budget's order.
            note_files = note_files[: max(0, args.max_links - len(links))]
        ambiguous += len(note_ambiguous) + len(note_file_ambiguous)
        if not note_ambiguous and not note_file_ambiguous:
            new_state_notes[name] = {
                "content_hash": note_hash,
                "linked": [s for s, _l, _w in links],
                "locations": [{"path": l["path"], "line": l.get("line")}
                              for _s, l, _w in links],
                "files": list(note_files),
                "files_fingerprint": files_fp}
        if not links and not note_files:
            # Distinguish the causes rather than reporting a single count.
            known = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body)
                     if w in symbols]
            if note_ambiguous:
                reason = "only-ambiguous"
            elif known:
                reason = "all-candidates-filtered"
            else:
                reason = "no-identifiers"
            unlinked.append({"id": os.path.splitext(name)[0], "reason": reason})
        for sym, how in note_ambiguous:
            ambiguous_refs[sym].append(os.path.splitext(name)[0])
            print(f"AMBIGUOUS {sym} in {name} ({how})")
            for loc in symbols[sym]:
                print(f"  - {fmt(loc)}")
        for ref, matches in note_file_ambiguous:
            ambiguous_files[ref].append(os.path.splitext(name)[0])
            ambiguous_file_candidates[ref] = matches
            print(f"AMBIGUOUS {ref} in {name} (file-suffix)")
            for p in matches:
                print(f"  - {p}")
        if links or note_files:
            with_matches += 1
            total_links += len(links)
            files_linked += len(note_files)
        if cached is None:  # a skipped note was proven identical already
            new = apply_block(text, links, symbols, note_files)
            if new != text:
                modified += 1
                if not args.check:
                    write_atomic(path, new)
        stem = os.path.splitext(name)[0]
        for sym, loc, why in links:
            backward[sym].append((stem, fmt(loc)))
            file_rollup[loc["path"]].add((stem, sym))
            if args.explain:
                print(f"{stem} -> {sym}\n    reason: {why}\n    destination: {fmt(loc)}")
        for anchored in note_files:
            file_notes[anchored].add(stem)
            if args.explain:
                print(f"{stem} -> {anchored}\n    reason: file-anchor\n"
                      f"    destination: {anchored}")

    # v3 keeps the backend under `indexing`; reading the v2 field on a v3 index
    # printed "unknown" while the backend was perfectly well known.
    _backend_name = ((payload.get("indexing") or {}).get("backend")
                     or payload.get("backend") or "unknown")
    lines = [
        "# Code index — which notes mention which symbol",
        "",
        f"Generated from `{os.path.basename(args.symbols)}` "
        f"(backend: {_backend_name}).",
        "",
        "Matching is lexical: it finds symbols the notes actually spell out.",
        "Frontmatter and generated blocks are excluded, so tracelink never",
        "matches its own output.",
        "",
        "| symbol | location | notes |",
        "|---|---|---|",
    ]
    for sym in sorted(backward, key=lambda s: (-len({n for n, _ in backward[s]}), s)):
        refs = " ".join(f"[[{n}]]" for n in sorted({n for n, _ in backward[sym]}))
        loc = sorted({l for _, l in backward[sym]})[0].replace("|", r"\|")
        lines.append(f"| `{sym}` | {loc} | {refs} |")

    # File anchors get their own table, after the symbols and before the
    # hotspots. Rendered only when something anchored — the same rule the
    # hotspot section follows. They do NOT feed the per-file hotspot rollup:
    # hotspots count (note, symbol) links, and a file named as a thing is
    # not a symbol located in a file.
    if file_notes:
        lines += [
            "",
            "## Files",
            "",
            "Notes anchored to a file rather than a symbol — configs,",
            "scripts and infrastructure the code index has no name for.",
            "",
            "| file | notes |",
            "|---|---|",
        ]
        for fpath in sorted(file_notes,
                            key=lambda p: (-len(file_notes[p]), p)):
            refs = " ".join(f"[[{n}]]" for n in sorted(file_notes[fpath]))
            esc = fpath.replace("|", r"\|")
            lines.append(f"| {esc} | {refs} |")

    # Three notes about one function is a signal worth seeing — and it should
    # not require counting wikilinks in the table above by hand. Rendered only
    # when there is something to show: a permanent header over an empty table
    # trains the reader to skip the section on the day it matters.
    hot_syms = [s for s in backward if len({n for n, _ in backward[s]}) >= 2]
    hot_files = [p for p in file_rollup if len(file_rollup[p]) >= 2]
    if hot_syms or hot_files:
        lines += [
            "",
            "## Hotspots",
            "",
            "Where the notes converge — several findings naming the same code",
            "is a signal worth seeing on its own.",
            "",
        ]
        if hot_syms:
            lines += ["| symbol | location | notes | note count |",
                      "|---|---|---|---|"]
            for sym in sorted(hot_syms,
                              key=lambda s: (-len({n for n, _ in backward[s]}), s)):
                stems = sorted({n for n, _ in backward[sym]})
                refs = " ".join(f"[[{n}]]" for n in stems)
                loc = sorted({l for _, l in backward[sym]})[0].replace("|", r"\|")
                lines.append(f"| `{sym}` | {loc} | {refs} | {len(stems)} |")
        if hot_files:
            if hot_syms:
                lines.append("")
            lines += ["### Per file",
                      "",
                      "| file | distinct symbols | note links |",
                      "|---|---|---|"]
            for path in sorted(hot_files,
                               key=lambda p: (-len(file_rollup[p]), p)):
                distinct = len({s for _, s in file_rollup[path]})
                esc = path.replace("|", r"\|")
                lines.append(f"| {esc} | {distinct} | {len(file_rollup[path])} |")

    if ambiguous_refs or ambiguous_files:
        lines += [
            "",
            "## Ambiguous references",
            "",
            "Named by a note, defined in more than one place. The link is",
            "withheld because guessing would be worse than abstaining — but the",
            "reference itself is evidence and is recorded here.",
            "",
        ]
        for sym in sorted(ambiguous_refs):
            notes_ref = " ".join(f"[[{n}]]" for n in sorted(set(ambiguous_refs[sym])))
            lines += [f"### `{sym}`", "", f"Referenced by: {notes_ref}", "",
                      "Candidates:", ""]
            lines += [f"- {fmt(loc)}" for loc in symbols.get(sym, [])]
            lines.append("")
        # File references after the symbols, same shape: the candidates are
        # the paths the suffix matched, and the linker refused to pick one.
        for ref in sorted(ambiguous_files):
            notes_ref = " ".join(f"[[{n}]]"
                                 for n in sorted(set(ambiguous_files[ref])))
            lines += [f"### `{ref}`", "", f"Referenced by: {notes_ref}", "",
                      "Candidates:", ""]
            lines += [f"- {p}" for p in ambiguous_file_candidates[ref]]
            lines.append("")

    index_text = "\n".join(lines) + "\n"

    index_path = os.path.join(args.vault, "CODE-INDEX.md")
    index_stale = (not os.path.exists(index_path)
                   or open(index_path, errors="replace").read() != index_text)
    if index_stale and not args.check:
        write_atomic(index_path, index_text)

    # Written on every non-check run that got this far — even one that
    # relinked nothing, because the fingerprints must describe the inputs the
    # vault was last verified against, not the last time something changed.
    if not args.check:
        write_atomic(state_path, json.dumps({
            "schema_version": _STATE_SCHEMA,
            "symbols_fingerprint": symbols_fp,
            "options_fingerprint": opts_fp,
            "symbol_locations": current_locations,
            "notes": new_state_notes,
        }, indent=1) + "\n")

    if args.format == "json":
        print(json.dumps({
            "ok": not (args.check and (modified or index_stale)),
            "exit_reason": None,
            "freshness": fresh.as_dict() if fresh else None,
            "linking": {"notes_scanned": scanned, "notes_with_matches": with_matches,
                        "notes_modified": modified,
                        "notes_skipped_unchanged": skipped_unchanged,
                        "symbols_linked": total_links,
                        "files_linked": files_linked,
                        "distinct_symbols": len(backward), "ambiguous_matches": ambiguous},
            "unlinked_notes": unlinked,
        }, indent=1))
        failed = (args.check and (modified or index_stale)) or (
            args.require_linked and unlinked)
        return 1 if failed else 0
    print(f"notes_scanned:      {scanned}")
    print(f"notes_with_matches: {with_matches}")
    print(f"notes_modified:     {modified}")
    print(f"notes_skipped_unchanged: {skipped_unchanged}")
    print(f"symbols_linked:     {total_links}")
    if files_linked:
        # Printed only when something anchored: the thirty-second demo's
        # console output is a documented artifact, and a vault without file
        # anchors must read exactly as it always did.
        print(f"files_linked:       {files_linked}")
    print(f"distinct_symbols:   {len(backward)}")
    print(f"ambiguous_matches:  {ambiguous}")
    print(f"unlinked_notes:     {len(unlinked)}")
    if unlinked and (args.report_unlinked or args.require_linked):
        print("\nUNLINKED NOTES\n")
        explain = {
            "no-identifiers": "no symbol identifiers found",
            "all-candidates-filtered": "candidate symbols filtered as common or too short",
            "only-ambiguous": "only ambiguous symbols found",
        }
        for item in unlinked:
            print(f"{item['id']}\n  reason: {explain[item['reason']]}\n")
    # The freshness block above already prints the commit for every schema.
    # Reading `repo_commit` here reproduced the v2 field on a v3 index and
    # printed nothing — duplicated output that was also wrong.
    if args.require_linked and unlinked:
        print(f"require-linked: {len(unlinked)} note(s) linked nothing")
        return 1
    if args.check:
        stale = modified + (1 if index_stale else 0)
        print(f"check: {stale} file(s) would change")
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
