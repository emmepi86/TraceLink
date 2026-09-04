"""`tracelink doctor` — is this installation set up correctly?

The distinction from `status` is the whole point of having both:

    status   how is the memory?   register↔vault sync, freshness, findings
    doctor   how is the setup?    config, paths, backend, hook, schemas

They answer different questions and fail for different reasons. A vault
full of stale notes is a `status` problem; a `ctags` backend configured on
a machine with no `tags` file is a `doctor` problem, and reading one report
looking for the other is how people conclude the tool is broken.

**Diagnosis only.** There is no `--fix`, deliberately: a repair that runs
before the diagnosis is understood is how a tool destroys a vault someone
cared about. Every check says what is wrong and what would fix it, and
leaves the doing to a person.

Checks are `ok`, `warn` or `fail`, and the exit code follows the worst one.
The codes are internal for now — `TLxxx` diagnostics become API in their
own ministep, and inventing them here would publish a vocabulary nobody has
finished designing.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

from . import config as _config
from . import consult as _consult
from .consult import EXIT_OK

#: Something is wrong that will stop tracelink working.
EXIT_UNHEALTHY = 1

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "⚠", FAIL: "✗"}


class Check:
    __slots__ = ("state", "what", "detail", "remedy")

    def __init__(self, state, what, detail="", remedy=""):
        self.state = state
        self.what = what
        self.detail = detail
        self.remedy = remedy


def _config_checks(project, values):
    yield Check(OK if os.path.isdir(os.path.join(project,
                                                 _config.CONFIG_DIR))
                else FAIL,
                f"{_config.CONFIG_DIR}/ exists",
                remedy="tracelink init")
    path = _config.path(project)
    if not os.path.exists(path):
        yield Check(WARN, "config.json present",
                    "absent — every value is a default",
                    "tracelink init")
    else:
        try:
            with open(path, encoding="utf-8") as fh:
                parsed = json.load(fh)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
            yield Check(OK, "config.json parses")
        except Exception as exc:  # noqa: BLE001
            yield Check(FAIL, "config.json parses", f"{type(exc).__name__}",
                        "fix the JSON, or delete it to fall back to defaults")
        unknown = _config.unknown_keys(project)
        if unknown:
            yield Check(WARN, "config keys understood",
                        "ignored: " + ", ".join(unknown),
                        "remove them, or upgrade tracelink")


def _register_check(project, values):
    register = _config.resolve(project, values, "register")
    if not os.path.exists(register):
        return Check(FAIL, "register readable",
                     f"no file at {values['register']}",
                     "tracelink init, or point `register` at yours")
    try:
        with open(register, encoding="utf-8", errors="replace") as fh:
            fh.read(1)
        return Check(OK, "register readable", values["register"])
    except OSError as exc:
        return Check(FAIL, "register readable", str(exc))


def _vault_checks(project, values):
    vault = _config.resolve(project, values, "vault")
    if not os.path.isdir(vault):
        yield Check(WARN, "vault exists", "not built yet", "tracelink sync")
        return
    yield Check(OK if os.access(vault, os.W_OK) else FAIL, "vault writable",
                vault if not os.access(vault, os.W_OK) else "")
    state_path = os.path.join(vault, _consult.STATE_FILE)
    if not os.path.exists(state_path):
        yield Check(WARN, "link state present", "no links recorded yet",
                    "tracelink sync")
        return
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        version = state.get("schema_version")
    except Exception:  # noqa: BLE001
        yield Check(FAIL, "link state readable", "corrupt", "tracelink sync")
        return
    if version == _consult.STATE_SCHEMA:
        yield Check(OK, f"link state schema {version}")
    else:
        yield Check(WARN, "link state schema",
                    f"{version}, this tracelink writes "
                    f"{_consult.STATE_SCHEMA}",
                    "tracelink sync — it rewrites the state in full")


def _upstream_check(project, values):
    """Is the evidence behind the index current, or merely unexamined?"""
    symbols = _config.resolve(project, values, "symbols")
    if not os.path.exists(symbols):
        return None
    try:
        with open(symbols, encoding="utf-8") as fh:
            upstream = ((json.load(fh).get("indexing") or {})
                        .get("upstream") or {})
    except Exception:  # noqa: BLE001
        return None
    state = upstream.get("state")
    if state == "verified":
        return Check(OK, "index evidence verified", upstream.get("reason", ""))
    if state == "stale":
        return Check(FAIL, "index evidence current",
                     "the artefact this index was built from describes "
                     "another repository state",
                     "regenerate the backend artefact, then tracelink sync")
    return Check(WARN, "index evidence current",
                 upstream.get("reason") or "not recorded",
                 "nothing to fix if the artefact is current — but tracelink "
                 "cannot confirm it, so freshness reads `unknown`")


def _coordinates_check(project, values):
    """Do the index's coordinates name files in this repository?

    Not the same question as completeness. An index can cover the whole
    repository and still record every path against the wrong base.
    """
    symbols = _config.resolve(project, values, "symbols")
    if not os.path.exists(symbols):
        return None
    try:
        with open(symbols, encoding="utf-8") as fh:
            integrity = ((json.load(fh).get("indexing") or {})
                         .get("path_integrity") or {})
    except Exception:  # noqa: BLE001
        return None
    invalid = integrity.get("invalid_locations")
    if not integrity:
        return Check(WARN, "index coordinates checked",
                     "index predates the check",
                     "tracelink sync")
    if not invalid:
        return Check(OK, "index coordinates",
                     f"{integrity.get('checked_locations', 0)} locations "
                     "name files in the repository")
    return Check(WARN, "index coordinates",
                 f"{invalid} location(s) named no file here and were dropped",
                 "check that --repo names the base the backend's paths are "
                 "relative to")


def _backend_check(project, values):
    backend = values["backend"]
    if backend == "scan":
        return Check(OK, "symbol backend: scan", "no external input needed")
    if backend == "ctags":
        if os.path.exists(os.path.join(project, "tags")):
            return Check(OK, "symbol backend: ctags", "tags file present")
        return Check(FAIL, "symbol backend: ctags",
                     "configured, but there is no tags file",
                     "ctags -R --fields=+n -f tags ."
                     + ("" if shutil.which("ctags")
                        else "  (and ctags is not on PATH)"))
    if backend == "graphify":
        if os.path.exists(os.path.join(project, "graphify-out",
                                       "graph.json")):
            return Check(OK, "symbol backend: graphify", "graph.json present")
        return Check(FAIL, "symbol backend: graphify",
                     "configured, but there is no graphify-out/graph.json",
                     "graphify update <repo>")
    return Check(WARN, f"symbol backend: {backend}", "unknown to this version",
                 "use scan, ctags or graphify")


def _git_checks(project):
    git = os.path.join(project, ".git")
    if not (os.path.isdir(git) or os.path.isfile(git)):
        yield Check(WARN, "git repository", "none — freshness stays unknown")
        return
    yield Check(OK, "git repository")
    from . import hook as _hook
    try:
        directory = _hook.hooks_dir(project)
        path = os.path.join(directory, "post-commit") if directory else None
    except Exception:  # noqa: BLE001 — hook layout is not doctor's business
        return
    if not path or not os.path.exists(path):
        yield Check(WARN, "post-commit hook", "not installed",
                    "tracelink hook install")
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if _hook.MARK_START in text:
        yield Check(OK, "post-commit hook installed")
    else:
        yield Check(WARN, "post-commit hook", "a hook exists, but not ours",
                    "tracelink hook install (it coexists)")


def diagnose(project, values):
    checks = list(_config_checks(project, values))
    checks.append(_register_check(project, values))
    checks.extend(_vault_checks(project, values))
    checks.append(_backend_check(project, values))
    upstream = _upstream_check(project, values)
    if upstream is not None:
        checks.append(upstream)
    coordinates = _coordinates_check(project, values)
    if coordinates is not None:
        checks.append(coordinates)
    checks.extend(_git_checks(project))
    return checks


def render_text(checks):
    lines = ["TraceLink doctor", ""]
    for check in checks:
        line = f"  {_MARK[check.state]} {check.what}"
        if check.detail:
            line += f" — {check.detail}"
        lines.append(line)
        if check.state != OK and check.remedy:
            lines.append(f"      fix: {check.remedy}")
    worst = worst_state(checks)
    lines += ["", "  " + {OK: "installation looks correct",
                          WARN: "usable, with things worth knowing",
                          FAIL: "something here will stop tracelink working"
                          }[worst]]
    return "\n".join(lines)


def worst_state(checks):
    states = {c.state for c in checks}
    return FAIL if FAIL in states else (WARN if WARN in states else OK)


def main(argv=None, prog=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog,
        description="Check the installation and configuration — not the "
                    "memory, which is `tracelink status`. Reports only: "
                    "there is no --fix, on purpose.")
    ap.add_argument("--repo", default=".", help="repository root (default .)")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable document instead")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on warnings too")
    args = ap.parse_args(argv)

    values = _config.read(args.repo)
    checks = diagnose(args.repo, values)
    if args.json:
        print(json.dumps({"schema_version": 1,
                          "state": worst_state(checks),
                          "checks": [{"state": c.state, "check": c.what,
                                      "detail": c.detail, "remedy": c.remedy}
                                     for c in checks]},
                         indent=2, ensure_ascii=False))
    else:
        print(render_text(checks))

    worst = worst_state(checks)
    if worst == FAIL or (args.strict and worst == WARN):
        return EXIT_UNHEALTHY
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
