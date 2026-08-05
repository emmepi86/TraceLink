#!/usr/bin/env python3
"""Lint a findings register — a read-only quality gate.

Written for the capture loop (the Stop-hook prompt ends with "then run:
tracelink lint ...") and useful standalone: it answers, before split and link
ever run, whether the findings just appended will survive as memory. Four
rules, each one a way a finding fails its future reader:

  prose-only        no linkable identifier at all. Detection is the LINKER'S,
                    imported rather than copied — the same code-span, snake,
                    camel and dotted patterns `candidates()` uses — because a
                    private reimplementation would drift and lint would start
                    promising links the linker refuses. A finding whose only
                    "identifiers" are stopwords or ubiquitous symbols is
                    prose-only too: framework vocabulary in backticks is
                    still prose.
  unknown-symbols   with --symbols: deliberately-spelled identifiers the
                    index has never heard of. A WARNING only when the finding
                    cites NO known symbol at all — then the unknowns are the
                    whole citation record, and they are typos or prose in
                    backticks. Beside at least one known symbol they demote
                    to INFO (`INFO …` lines, the JSON's `infos` array, exit
                    code untouched): on the real-registry benchmark 9 of 10
                    notes that linked perfectly still warned here, because
                    backticked properties, env vars and config keys are
                    physiological in real notes and no index defines them. A
                    gate that cries almost every time gets ignored — and
                    capture leans on lint.
                    "Known symbol" means a RELIABLE anchor: linker stopwords
                    (`_DEFAULT_STOP`) and UBIQUITOUS symbols — defined in
                    more than `_UBIQUITY_LIMIT` distinct files — do not
                    count, even though the index lists them. Ubiquitous
                    symbols are still in the index, so they are never
                    reported as unknown (neither warning nor info): they
                    simply anchor nothing. Same for prose-only below.
  duplicate         with --vault: a title that normalises to an existing
                    note's title. The same discovery recorded twice reads as
                    two findings and links as none of them.
  missing-status /  the explicit `### STATUS:` / severity grammar the
  missing-severity  splitter documents is absent — the note would fall back
                    to defaults and legacy keyword guessing.

`--new-only` restricts the check to findings the vault manifest has not seen:
exactly the ones a capture session just appended. Exit 0 with zero warnings,
1 otherwise — lint IS the gate, so there is no --strict. Nothing here ever
writes: not the register, not the vault, not a state file.

Usage:
    tracelink lint --register FINDINGS.md
    tracelink lint --register FINDINGS.md --vault .tracelink/vault \\
        --symbols .tracelink/symbols.json --new-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional

from .linker import (_CAMEL, _CODE_SPAN, _DEFAULT_STOP, _DOTTED, _SNAKE,
                     DEFAULT_MIN_LEN, _strip_syntax_prefixes, candidates,
                     normalise)
from .splitter import (_SEVERITY_RE, _STATUS_RE, finding_pattern, severity,
                       split)

#: A symbol defined in more than this many distinct files is UBIQUITOUS and
#: never counts as an anchor. Next.js made the need concrete: `dynamic`,
#: `revalidate` and `runtime` are `export const` route-config in dozens of
#: routes, so citing them is framework vocabulary, not evidence the finding
#: is anchored to real code. The rule is data-driven on purpose — no
#: hardcoded framework names, so `up`/`down` across a migrations tree trip
#: the same wire without anyone listing them.
_UBIQUITY_LIMIT = 4

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A dotted token whose tail is a file extension is a filename, not a
#: qualified name — `app.py` must not become a citation of a symbol `py`.
_FILE_EXTS = {
    "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "md", "json", "yml",
    "yaml", "toml", "ini", "cfg", "txt", "sh", "bash", "go", "rs", "java",
    "rb", "c", "h", "cc", "cpp", "hpp", "cs", "php", "html", "css", "scss",
    "sql", "xml", "lock", "csv",
}


def _is_filename(token: str) -> bool:
    return token.rsplit(".", 1)[-1].lower() in _FILE_EXTS


def _is_ubiquitous(name: str, symbols: dict) -> bool:
    """Defined in more than _UBIQUITY_LIMIT distinct files of the index."""
    locations = symbols.get(name)
    if not isinstance(locations, list):
        return False
    paths = {loc.get("path") for loc in locations if isinstance(loc, dict)}
    return len(paths) > _UBIQUITY_LIMIT


def _is_anchor(token: str, symbols: dict) -> bool:
    """Whether a cited token is reliable evidence of anchoring.

    Known to the index AND not a linker stopword AND not ubiquitous. A
    backticked `config` does link when some file defines one, but half a
    codebase defines a `config`; and a symbol living in dozens of files
    (`dynamic` in a Next repo) names a convention, not a place. Neither is
    the kind of citation that should vouch for a finding's unknowns.
    """
    tail = token.rsplit(".", 1)[-1]
    return (tail in symbols and tail not in _DEFAULT_STOP
            and not _is_ubiquitous(tail, symbols))


def deliberate_identifiers(text: str) -> List[str]:
    """Tokens that can only be identifiers, without consulting any index.

    Inside backticks anything identifier- or dotted-shaped counts: backticks
    are a decision. Outside them the bar is higher, because prose qualifies
    for the linker's patterns too — `Negations` at the start of a sentence
    matches _CAMEL, and any seven-letter word matches _SNAKE. So a bare
    snake token needs an underscore, and a camel token needs lowercase AND a
    second capital or a digit; an all-caps word is an acronym, not a symbol.
    """
    found: Dict[str, None] = {}
    for span in _CODE_SPAN.findall(text):
        token = _strip_syntax_prefixes(span.strip().rstrip("()").lstrip("."))
        if _NAME.fullmatch(token):
            found.setdefault(token)
        elif _DOTTED.fullmatch(token) and not _is_filename(token):
            found.setdefault(token)
    for m in _SNAKE.findall(text):
        if "_" in m and m not in _DEFAULT_STOP:
            found.setdefault(m)
    for m in _CAMEL.findall(text):
        if (not m.isupper()
                and (sum(c.isupper() for c in m) >= 2
                     or any(c.isdigit() for c in m))
                and m.lower() not in _DEFAULT_STOP):
            found.setdefault(m)
    for m in _DOTTED.findall(text):
        token = _strip_syntax_prefixes(m)
        if ("." in token and not _is_filename(token)
                and all(len(seg) >= 2 for seg in token.split("."))):
            found.setdefault(token)
    return list(found)


def _norm_title(title: str) -> str:
    """`Totals ignore TAX!` and `totals ignore tax` are one discovery."""
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def finding_title(fid: str, blob: str, prefix: str) -> str:
    """The words of the first finding heading, id and severity tag stripped —
    the same reading the splitter gives a note its title with."""
    head_re = re.compile(rf"#{{2,6}} {finding_pattern(prefix)}")
    for line in blob.splitlines():
        if head_re.match(line):
            title = re.sub(
                rf"^#{{2,6}} {finding_pattern(prefix)}\s*[—:–-]?\s*", "", line)
            return re.sub(r"\[[A-Za-z]+\]\s*$", "", title).strip()
    return ""


_FM_ID = re.compile(r"^tracelink_id:\s*(\S+)\s*$", re.M)


def vault_titles(vault: str) -> Dict[str, str]:
    """{note id: normalised title} for every note in the vault. Read-only,
    and tolerant: a note without a heading contributes nothing."""
    titles: Dict[str, str] = {}
    if not os.path.isdir(vault):
        return titles
    for name in sorted(os.listdir(vault)):
        if not name.endswith(".md") or name in ("INDEX.md", "CODE-INDEX.md"):
            continue
        try:
            text = open(os.path.join(vault, name), errors="replace").read()
        except OSError:
            continue
        note_id = name[:-3]
        m = _FM_ID.search(text[:2000])
        if m:
            note_id = m.group(1)
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                for sep in (" — ", " – ", " - "):
                    if title.startswith(note_id + sep):
                        title = title[len(note_id) + len(sep):]
                        break
                title = re.sub(r"\[[A-Za-z]+\]\s*$", "", title).strip()
                titles[note_id] = _norm_title(title)
                break
    return titles


def check_finding(fid: str, blocks: List[str], prefix: str,
                  symbols: Optional[dict],
                  known_titles: Optional[Dict[str, str]]
                  ) -> "tuple[List[dict], List[dict]]":
    """(warnings, infos) one finding earns. Order is the rules' order in
    the docstring, so the output reads the same as the documentation."""
    warnings: List[dict] = []
    infos: List[dict] = []
    blob = "\n\n".join(blocks)
    idents = deliberate_identifiers(blob)

    # (a) prose-only: nothing the linker could connect. With an index,
    # stopwords and ubiquitous symbols do not rescue a finding — if they
    # are its only "identifiers", it is prose wearing backticks — and the
    # linker's own candidate scan gets the final word (a bare word the
    # index knows is linkable even without an underscore), filtered the
    # same way so `dynamic` as an English word cannot rescue it either.
    if symbols is None:
        linkable = bool(idents)
    else:
        linkable = any(
            (tail := token.rsplit(".", 1)[-1]) not in _DEFAULT_STOP
            and not _is_ubiquitous(tail, symbols)
            for token in idents)
        if not linkable:
            linkable = any(
                name.lower() not in _DEFAULT_STOP
                and not _is_ubiquitous(name, symbols)
                for name, _ in candidates(blob, symbols, DEFAULT_MIN_LEN,
                                          _DEFAULT_STOP))
    if not linkable:
        # The advice must fit the failure. A finding that already has
        # identifiers — necessarily all stopwords or ubiquitous, or it would
        # be linkable — has followed "use backticks" to the letter; telling
        # it to again is advice already taken.
        if idents:
            detail = ("the only backticked names are stopwords or "
                      "ubiquitous — name the specific symbols involved")
        else:
            detail = ("no linkable identifiers — name the exact symbols "
                      "in backticks")
        warnings.append({"id": fid, "code": "prose-only", "detail": detail})

    # (b) unknown symbols: deliberate spellings the index cannot resolve.
    # A warning only when NOTHING in the finding is a reliable ANCHOR —
    # then the unknowns are all it cites, and fixing them is the whole
    # job. Next to an anchor they are almost always properties, env vars
    # or config keys (the benchmark's 9/10), so they inform without
    # gating. Stopwords and ubiquitous symbols are in the index, so they
    # are never unknown themselves — they just cannot vouch for others.
    if symbols is not None:
        unknown = sorted(
            token for token in idents
            if (tail := token.rsplit(".", 1)[-1]) not in symbols
            and tail not in _DEFAULT_STOP)
        if unknown:
            cites_known = any(_is_anchor(token, symbols)
                              for token in idents)
            if cites_known:
                infos.extend(
                    {"id": fid, "code": "unknown-symbols",
                     "detail": f"not in the index: {token} — informational, "
                               "the finding already cites known symbols"}
                    for token in unknown)
            else:
                warnings.append({"id": fid, "code": "unknown-symbols",
                                 "detail": "names unknown symbols: "
                                           + ", ".join(unknown)})

    # (c) duplicate: same normalised title as a DIFFERENT existing note.
    if known_titles:
        norm = _norm_title(finding_title(fid, blob, prefix))
        if norm:
            for note_id, title in known_titles.items():
                if note_id != fid and title == norm:
                    warnings.append(
                        {"id": fid, "code": "duplicate",
                         "detail": f"possible duplicate of {note_id}"})
                    break

    # (d) the explicit metadata grammar. Severity accepts the documented
    # `[HIGH]`-style heading tag too — the splitter reads both.
    heads = [ln for ln in blob.splitlines() if ln.lstrip().startswith("#")]
    if not any(_STATUS_RE.search(h) for h in heads):
        warnings.append({"id": fid, "code": "missing-status",
                         "detail": "no ### STATUS: heading"})
    if severity(heads) == "unspecified" and not any(
            _SEVERITY_RE.search(h) for h in heads):
        warnings.append({"id": fid, "code": "missing-severity",
                         "detail": "no SEVERITY (### SEVERITY: line or "
                                   "[HIGH]-style tag)"})
    return warnings, infos


def _load_manifest(vault: str) -> dict:
    try:
        with open(os.path.join(vault, ".tracelink-manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError):
        return {}
    return man if isinstance(man, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Lint a findings register — read-only quality gate.")
    ap.add_argument("--register", required=True)
    ap.add_argument("--vault",
                    help="existing note vault, for duplicate titles and "
                         "--new-only")
    ap.add_argument("--symbols",
                    help="symbol index, to verify that cited symbols exist")
    ap.add_argument("--prefix", default=None,
                    help="finding id prefix (default: the vault manifest's, "
                         "else RES)")
    ap.add_argument("--new-only", action="store_true",
                    help="check only findings the vault manifest has not "
                         "seen (requires --vault)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if not os.path.isfile(args.register):
        print(f"register not found: {args.register}", file=sys.stderr)
        return 2
    if args.new_only and not args.vault:
        print("--new-only needs --vault: the manifest is what says which "
              "findings are already split", file=sys.stderr)
        return 2

    manifest = _load_manifest(args.vault) if args.vault else {}
    prefix = (args.prefix
              or (manifest.get("register") or {}).get("prefix")
              or manifest.get("prefix") or "RES")

    symbols = None
    if args.symbols:
        try:
            with open(args.symbols, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"cannot read symbols: {exc}", file=sys.stderr)
            return 2
        symbols = normalise(payload.get("symbols") or payload)

    known_titles = vault_titles(args.vault) if args.vault else None

    already_split = set()
    if args.new_only:
        already_split = {os.path.splitext(g)[0]
                         for g in manifest.get("generated_notes") or []
                         if isinstance(g, str)}

    checked, warnings, infos = 0, [], []
    for fid, blocks in split(args.register, prefix).items():
        if args.new_only and fid in already_split:
            continue
        checked += 1
        warns, notes = check_finding(fid, blocks, prefix, symbols,
                                     known_titles)
        warnings += warns
        infos += notes

    if args.format == "json":
        print(json.dumps({"findings_checked": checked,
                          "warnings": warnings,
                          "infos": infos}, indent=1))
    else:
        for w in warnings:
            print(f"WARN {w['id']} [{w['code']}] {w['detail']}")
        for i in infos:
            print(f"INFO {i['id']} [{i['code']}] {i['detail']}")
        print(f"findings_checked: {checked}")
        print(f"warnings:         {len(warnings)}")
        print(f"infos:            {len(infos)}")
    # Only warnings gate: infos are context, never a reason to fail.
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
