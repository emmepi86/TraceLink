"""`tracelink sync` — bring the memory up to date with the repository.

Three commands already do the work, and this one does none of it again: it
calls `index`, `split` and `link` in process, in that order, with the
arguments the project configured, and stops at the first one that fails.
Orchestration only. If a rule about linking lives here, it lives in two
places, and the copy will be the one that rots.

    index    the repository  ->  symbols.json
    split    the register    ->  one note per finding
    link     both            ->  links, both directions

What it adds is a single answer to "is the memory consistent with the code
in front of me?", and the properties that make that answer worth having:

  * **deterministic** — same repository, same register, same configuration,
    same bytes out. `tests/test_sync.py` runs it twice and compares
    checksums of everything under `.tracelink/`.
  * **idempotent** — a second run changes nothing, and says so.
  * **honest about failure** — the exit code is the failing step's, the
    step is named, and nothing later runs on a broken earlier result.

`--check` writes nothing and exits 1 when a run would change something: the
CI form of the same question.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from . import config as _config
from . import linker, splitter, symbol_index
from .consult import EXIT_OK, EXIT_USAGE, STATE_FILE

#: A step failed. Distinct from a usage error, and from `--check` finding
#: work to do, which is what exit 1 means.
EXIT_FAILED = 1

STEPS = ("index", "split", "link")


class StepResult:
    __slots__ = ("name", "code", "output")

    def __init__(self, name, code, output):
        self.name = name
        self.code = code
        self.output = output

    @property
    def ok(self):
        return self.code == 0


def _run(name, entry, argv, verbose):
    """One step, in process, with its chatter captured unless asked for."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = entry(argv, prog=f"tracelink {name}")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    text = out.getvalue()
    if verbose and text:
        sys.stdout.write(text)
    return StepResult(name, code, text)


def sync(project, values, verbose=False, full=False, symbols=None,
         vault=None):
    """Run the three steps against `project`. Returns (results, summary).

    `symbols` and `vault` override the configured destinations, which is
    how `--check` runs the whole pipeline somewhere else and compares.
    """
    symbols = symbols or _config.resolve(project, values, "symbols")
    vault = vault or _config.resolve(project, values, "vault")
    register = _config.resolve(project, values, "register")
    os.makedirs(os.path.dirname(symbols) or project, exist_ok=True)

    results = []
    results.append(_run("index", symbol_index.main,
                        ["--repo", project, "--backend", values["backend"],
                         "--out", symbols], verbose))
    if results[-1].ok:
        results.append(_run("split", splitter.main,
                            ["--register", register, "--out", vault,
                             "--prefix", values["prefix"]], verbose))
    if results[-1].ok:
        link_argv = ["--vault", vault, "--symbols", symbols,
                     "--repo", project, "--format", "json"]
        if full:
            link_argv.append("--full")
        results.append(_run("link", linker.main, link_argv, verbose))
    return results, _summarise(results, symbols, vault, register)


def checksums(root):
    """Every file under `root`, by content."""
    import hashlib
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                with open(path, "rb") as fh:
                    out[os.path.relpath(path, root)] = hashlib.sha256(
                        fh.read()).hexdigest()
            except OSError:
                out[os.path.relpath(path, root)] = "unreadable"
    return out


def would_change(project, values, full=False):
    """(results, summary, changed paths) — without writing anything.

    `split` has no dry-run mode and `index` writes by definition, so a check
    that ran them in place would be the very change it claims to detect.
    Instead the existing `.tracelink/` is copied, the whole pipeline runs on
    the copy against the real repository, and the two are compared by
    content. That is exact rather than approximate *because* sync is
    deterministic — the property the tests next door pin — and it costs one
    full run, which is what a CI check can afford.
    """
    import shutil
    import tempfile

    live = os.path.join(project, _config.CONFIG_DIR)
    scratch = tempfile.mkdtemp(prefix="tracelink-check-")
    try:
        mirror = os.path.join(scratch, _config.CONFIG_DIR)
        if os.path.isdir(live):
            shutil.copytree(live, mirror)
        else:
            os.makedirs(mirror)
        results, summary = sync(
            project, values, full=full,
            symbols=os.path.join(mirror, os.path.basename(
                _config.resolve(project, values, "symbols"))),
            vault=os.path.join(mirror, os.path.basename(
                _config.resolve(project, values, "vault"))))
        before = checksums(live) if os.path.isdir(live) else {}
        after = checksums(mirror)
        changed = sorted(set(before) ^ set(after)) + sorted(
            path for path in set(before) & set(after)
            if before[path] != after[path])
        return results, summary, sorted(set(changed))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _summarise(results, symbols, vault, register):
    """What happened, from the artefacts themselves rather than from prose."""
    summary = {"steps": {r.name: r.code for r in results},
               "register": os.path.basename(register),
               "notes": None, "symbols": None, "linking": None,
               "state_schema": None}
    try:
        with open(symbols, encoding="utf-8") as fh:
            index = json.load(fh)
        summary["symbols"] = len(index.get("symbols") or {})
        summary["backend"] = (index.get("indexing") or {}).get("backend")
    except Exception:  # noqa: BLE001 — a missing artefact is a None, not a raise
        pass
    try:
        summary["notes"] = len([n for n in os.listdir(vault)
                                if n.endswith(".md")
                                and n != "CODE-INDEX.md"
                                and n != "INDEX.md"])
    except OSError:
        pass
    for result in results:
        if result.name == "link" and result.output:
            try:
                summary["linking"] = json.loads(result.output).get("linking")
            except ValueError:
                pass
    try:
        with open(os.path.join(vault, STATE_FILE), encoding="utf-8") as fh:
            summary["state_schema"] = json.load(fh).get("schema_version")
    except Exception:  # noqa: BLE001
        pass
    return summary


def render_text(results, summary, check, changed=None):
    lines = ["TraceLink sync", ""]
    for result in results:
        mark = "ok" if result.ok else f"FAILED ({result.code})"
        lines.append(f"  {result.name:<8} {mark}")
    lines.append("")
    if summary["symbols"] is not None:
        lines.append(f"  symbols   {summary['symbols']} "
                     f"({summary.get('backend')})")
    if summary["notes"] is not None:
        lines.append(f"  notes     {summary['notes']}")
    linking = summary.get("linking") or {}
    if linking:
        lines.append(f"  links     {linking.get('symbols_linked', 0)} "
                     f"({linking.get('files_linked', 0)} file anchors)")
        lines.append(f"  changed   {linking.get('notes_modified', 0)} note(s)")
        lines.append(f"  ambiguous {linking.get('ambiguous_matches', 0)}")
    lines.append("")
    failed = [r for r in results if not r.ok]
    if failed:
        lines.append(f"  {failed[0].name} failed — nothing after it ran")
    elif check and changed:
        lines.append(f"  out of date: a sync would change {len(changed)} "
                     f"file(s)")
        for path in changed[:10]:
            lines.append(f"    {path}")
    else:
        lines.append("  memory consistent with the working tree")
    return "\n".join(lines)


def main(argv=None, prog=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog,
        description="Bring the vault and its links up to date with the "
                    "repository: index, split, link, in that order, with "
                    "the project's configured paths.")
    ap.add_argument("--repo", default=".", help="repository root (default .)")
    ap.add_argument("--register", default=None,
                    help="findings register (default: config, FINDINGS.md)")
    ap.add_argument("--vault", default=None, help="vault directory")
    ap.add_argument("--symbols", default=None, help="symbol index path")
    ap.add_argument("--prefix", default=None, help="finding id prefix")
    ap.add_argument("--backend", default=None,
                    help="symbol backend: scan | ctags | graphify")
    ap.add_argument("--full", action="store_true",
                    help="relink every note, not only the changed ones")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 if a sync would change "
                         "anything")
    ap.add_argument("--verbose", action="store_true",
                    help="show each step's own output")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable document instead")
    args = ap.parse_args(argv)

    values = _config.read(args.repo)
    for key in ("register", "vault", "symbols", "prefix", "backend"):
        chosen = getattr(args, key)
        if chosen is not None:
            values[key] = chosen

    if not os.path.exists(_config.resolve(args.repo, values, "register")):
        print(f"no register at {values['register']} — write one, or run "
              "`tracelink init`", file=sys.stderr)
        return EXIT_USAGE

    changed = None
    if args.check:
        results, summary, changed = would_change(args.repo, values,
                                                 full=args.full)
    else:
        results, summary = sync(args.repo, values,
                                verbose=args.verbose and not args.json,
                                full=args.full)

    failed = [r for r in results if not r.ok]
    code = EXIT_OK
    if failed:
        code = failed[0].code or EXIT_FAILED
    elif args.check and changed:
        code = EXIT_FAILED

    if args.json:
        document = {"schema_version": 1,
                    "ok": not failed and code == EXIT_OK,
                    "summary": summary}
        if args.check:
            document["changed"] = changed
        print(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        print(render_text(results, summary, args.check, changed))
        if failed and failed[0].output:
            sys.stderr.write(failed[0].output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
