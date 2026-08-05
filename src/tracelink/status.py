#!/usr/bin/env python3
"""One-shot health of register, vault, index and links.

`tracelink status` answers, without writing anything, the question every
other command silently assumes an answer to: is this vault still true?

Four sections, each read from the authority that owns the fact:

  register ↔ vault   ids from `splitter.split()` against the manifest's
                     `generated_notes` — findings not yet split, notes no
                     longer in the register, a manifest naming a different
                     source file.
  index freshness    `linker.verify_freshness()` verbatim; the comparison is
                     not reimplemented here.
  links              the `.tracelink-link-state.json` sidecar the linker
                     wrote: its age, whether the symbols file still matches
                     the fingerprint it recorded, and how many notes it can
                     no longer vouch for. Nothing is relinked to find out:
                     when the state cannot answer — a note edited after the
                     last link, a reason the state never stored — the report
                     says "unknown (run link)" instead of estimating.
  findings           status/severity counts from the notes' frontmatter,
                     with every open critical/high finding listed by id and
                     title.

JSON report (`--format json` prints ONLY the JSON), top-level keys exactly
`register`, `vault`, `index`, `links`, `findings`, `problems`, `ok`:

  register   path, found, prefix, ids, count
  vault      path, found, manifest_present, source, source_matches,
             generated_notes, notes_on_disk, missing_in_vault, extra_in_vault
  index      path, found, freshness, reasons, partial
  links      state_present, state_age_seconds, symbols_fingerprint_matches,
             notes_recorded, notes_unverified, unlinked_count (int or
             "unknown (run link)"), unlinked_by_reason (mapping, or
             "unknown (run link)" — the state does not record reasons)
  findings   total, by_status, open_high (id, severity, title)
  problems   list of strings, empty when healthy
  ok         true exactly when problems is empty

Exit codes: 0 always — the command informs, it does not gate. Under
`--strict` it exits 1 when `problems` is non-empty (register and vault
misaligned, index stale or of unknown freshness, link-state absent or
stale, unlinked notes). 2 is argparse's own usage error.

Usage:
    tracelink status --register FINDINGS.md --vault vault/ \
        --symbols symbols.json [--repo .] [--format text|json] [--strict]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time

try:
    from . import linker, splitter
except ImportError:  # running as a loose script
    import linker  # type: ignore
    import splitter  # type: ignore

_MANIFEST = ".tracelink-manifest.json"
#: split.py's title line: `# RES-01 — title`
_TITLE = re.compile(r"^#\s+\S+\s+—\s+(.*)$", re.M)


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _owned_notes(vault: str) -> dict:
    """name -> text for every note tracelink generated. Same exclusions as
    the linker: hand-written markdown is not ours to report on."""
    notes = {}
    for p in sorted(os.listdir(vault)):
        if not p.endswith(".md") or p in ("INDEX.md", "CODE-INDEX.md"):
            continue
        text = open(os.path.join(vault, p), errors="replace").read()
        if linker.is_owned_note(text):
            notes[p] = text
    return notes


def _frontmatter_fields(text: str) -> dict:
    """Top-level `key: value` pairs of the frontmatter block."""
    m = linker._FM_BLOCK.match(text)
    out = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _register_vault(register: str, vault: str):
    problems = []
    reg = {"path": register, "found": os.path.isfile(register),
           "prefix": None, "ids": [], "count": 0}
    va = {"path": vault, "found": os.path.isdir(vault),
          "manifest_present": False, "source": None, "source_matches": None,
          "generated_notes": 0, "notes_on_disk": 0,
          "missing_in_vault": [], "extra_in_vault": []}
    if not reg["found"]:
        problems.append(f"register-not-found: {register}")
    if not va["found"]:
        problems.append(f"vault-not-found: {vault} (run split)")
        return reg, va, {}, problems

    notes = _owned_notes(vault)
    va["notes_on_disk"] = len(notes)
    manifest = _load_json(os.path.join(vault, _MANIFEST))
    if not isinstance(manifest, dict):
        problems.append("manifest-missing (run split)")
        manifest = {}
    else:
        va["manifest_present"] = True
    reg_meta = manifest.get("register") or {}
    prefix = reg_meta.get("prefix") or manifest.get("prefix") or "RES"
    source = reg_meta.get("source") or manifest.get("generated_from")
    va["source"] = source
    if source is not None:
        va["source_matches"] = source == os.path.basename(register)
        if not va["source_matches"]:
            problems.append(f"register-source-mismatch: vault was built "
                            f"from {source}, not {os.path.basename(register)}")
    generated = [g for g in manifest.get("generated_notes") or []
                 if isinstance(g, str)]
    va["generated_notes"] = len(generated)

    if reg["found"]:
        reg["prefix"] = prefix
        ids = list(splitter.split(register, prefix))
        reg["ids"], reg["count"] = ids, len(ids)
        vault_ids = {os.path.splitext(g)[0] for g in generated
                     if os.path.exists(os.path.join(vault, g))}
        missing = [i for i in ids if i not in vault_ids]
        extra = sorted(vault_ids - set(ids), key=splitter.finding_number)
        va["missing_in_vault"], va["extra_in_vault"] = missing, extra
        if missing:
            problems.append(f"vault-behind-register: {len(missing)} "
                            f"finding(s) not split (run split)")
        if extra:
            problems.append(f"vault-has-extra-notes: {len(extra)} note(s) "
                            f"not in the register (run split)")
    return reg, va, notes, problems


def _index(symbols_path: str, repo: str):
    problems = []
    out = {"path": symbols_path, "found": os.path.isfile(symbols_path),
           "freshness": "unknown", "reasons": [], "partial": False}
    if not out["found"]:
        out["reasons"] = ["symbols-file-missing"]
        problems.append(f"symbols-not-found: {symbols_path} (run index)")
        return out, problems
    payload = _load_json(symbols_path)
    if not isinstance(payload, dict):
        out["freshness"] = "invalid"
        out["reasons"] = ["not-a-json-object"]
        problems.append("index-invalid: symbols file is not a JSON object")
        return out, problems
    fresh = linker.verify_freshness(payload, repo, symbols_path)
    out["freshness"] = fresh.status
    out["reasons"] = list(fresh.reasons)
    out["partial"] = bool(getattr(fresh, "partial", False))
    if fresh.status == "stale":
        problems.append("index-stale (run index)")
    elif fresh.status == "unknown":
        problems.append("index-freshness-unknown: " + ", ".join(fresh.reasons))
    elif fresh.status == "invalid":
        problems.append("index-invalid: " + ", ".join(fresh.reasons))
    return out, problems


def _links(vault: str, symbols_path: str, notes: dict, vault_found: bool):
    problems = []
    out = {"state_present": False, "state_age_seconds": None,
           "symbols_fingerprint_matches": None, "notes_recorded": None,
           "notes_unverified": None,
           "unlinked_count": "unknown (run link)",
           "unlinked_by_reason": "unknown (run link)"}
    if not vault_found:
        return out, problems  # vault-not-found is already the problem
    state_path = os.path.join(vault, linker._STATE_FILE)
    if not os.path.exists(state_path):
        problems.append("link-state-missing (run link)")
        return out, problems
    out["state_present"] = True
    out["state_age_seconds"] = max(
        0, int(time.time() - os.stat(state_path).st_mtime))
    state = linker.load_state(state_path)
    if state is None:
        problems.append("link-state-unreadable (run link)")
        return out, problems
    out["notes_recorded"] = len(state["notes"])

    matches = None
    try:
        with open(symbols_path, "rb") as fh:
            matches = linker._sha(fh.read()) == state["symbols_fingerprint"]
    except OSError:
        matches = None
    out["symbols_fingerprint_matches"] = matches
    if matches is False:
        problems.append("link-state-stale: symbols.json changed since the "
                        "last link (run link)")

    # A note the state can vouch for is one it recorded with the content
    # hash the note still has. Anything else — edited since, deleted since,
    # or never recorded because its analysis was ambiguous — is unverified,
    # and the unlinked count over an unverified vault is not a count.
    unverified = 0
    for name, text in notes.items():
        entry = state["notes"].get(name)
        h = linker.note_fingerprint(linker.matchable(text),
                                    linker.read_overrides(text))
        if entry is None or entry["content_hash"] != h:
            unverified += 1
    unverified += len(set(state["notes"]) - set(notes))
    out["notes_unverified"] = unverified
    if unverified:
        problems.append(f"link-state-stale: {unverified} note(s) the state "
                        f"cannot vouch for (run link)")
    if matches and not unverified:
        unlinked = sum(1 for n, e in state["notes"].items()
                       if n in notes and not e["linked"])
        out["unlinked_count"] = unlinked
        if unlinked:
            problems.append(f"unlinked-notes: {unlinked} "
                            f"(run link --report-unlinked for the reasons)")
        else:
            out["unlinked_by_reason"] = {}
    return out, problems


def _findings(notes: dict) -> dict:
    by_status: dict = {}
    open_high = []
    for name in sorted(notes,
                       key=lambda n: splitter.finding_number(
                           os.path.splitext(n)[0])):
        fm = _frontmatter_fields(notes[name])
        st = fm.get("status", "unspecified")
        sv = fm.get("severity", "unspecified")
        by_status[st] = by_status.get(st, 0) + 1
        if st == "open" and sv in ("critical", "high"):
            m = _TITLE.search(notes[name])
            open_high.append({"id": fm.get("id") or os.path.splitext(name)[0],
                              "severity": sv,
                              "title": m.group(1).strip() if m else ""})
    return {"total": len(notes), "by_status": by_status,
            "open_high": open_high}


def _age(seconds) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render_text(report: dict) -> str:
    reg, va = report["register"], report["vault"]
    lines = []
    if reg["found"]:
        lines.append(f"register:           {reg['path']} — "
                     f"{reg['count']} finding(s), prefix {reg['prefix']}")
    else:
        lines.append(f"register:           {reg['path']} — NOT FOUND")
    if va["found"]:
        man = ("manifest present" if va["manifest_present"]
               else "manifest MISSING (run split)")
        lines.append(f"vault:              {va['path']} — "
                     f"{va['notes_on_disk']} note(s), {man}")
        if va["source"] is not None:
            suffix = "" if va["source_matches"] else "  (MISMATCH)"
            lines.append(f"vault_source:       {va['source']}{suffix}")
        if va["missing_in_vault"]:
            lines.append("missing_in_vault:   " + " ".join(va["missing_in_vault"]))
        if va["extra_in_vault"]:
            lines.append("extra_in_vault:     " + " ".join(va["extra_in_vault"]))
    else:
        lines.append(f"vault:              {va['path']} — NOT FOUND (run split)")

    idx = report["index"]
    lines.append(f"index_freshness:    {idx['freshness']}")
    for reason in idx["reasons"]:
        lines.append(f"reason:             {reason}")
    if idx["partial"]:
        lines.append("index_completeness: partial — it does not cover the whole repository")

    links = report["links"]
    if links["state_present"]:
        lines.append(f"link_state:         present — age {_age(links['state_age_seconds'])}")
        if links["symbols_fingerprint_matches"] is not None:
            lines.append("symbols_match:      "
                         + ("yes" if links["symbols_fingerprint_matches"]
                            else "NO — symbols.json changed since the last link"))
        if links["notes_unverified"]:
            lines.append(f"notes_unverified:   {links['notes_unverified']} "
                         f"since the last link")
        lines.append(f"unlinked_notes:     {links['unlinked_count']}")
    else:
        lines.append("link_state:         absent (run link)")

    f = report["findings"]
    by = "  ".join(f"{k}={v}" for k, v in sorted(f["by_status"].items()))
    lines.append(f"findings:           {f['total']} note(s)"
                 + (f" — {by}" if by else ""))
    for item in f["open_high"]:
        lines.append(f"attention:          {item['id']} [{item['severity']}] — "
                     f"{item['title']}")

    if report["problems"]:
        lines.append("")
        for p in report["problems"]:
            lines.append(f"problem:            {p}")
    lines.append("")
    lines.append(f"ok:                 {'yes' if report['ok'] else 'no'}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="One-shot health of register, vault, index and links. "
                    "Writes nothing. Exit 0 always; --strict exits 1 when "
                    "any problem is found.")
    ap.add_argument("--register", required=True)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--repo", default=".",
                    help="repository the index claims to describe (default .)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any problem is found (for CI)")
    args = ap.parse_args()

    problems: list = []
    reg, va, notes, p = _register_vault(args.register, args.vault)
    problems += p
    idx, p = _index(args.symbols, args.repo)
    problems += p
    links, p = _links(args.vault, args.symbols, notes, va["found"])
    problems += p

    report = {"register": reg, "vault": va, "index": idx, "links": links,
              "findings": _findings(notes), "problems": problems,
              "ok": not problems}
    if args.format == "json":
        print(json.dumps(report, indent=1))
    else:
        print(render_text(report))
    return 1 if (args.strict and problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
