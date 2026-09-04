"""`tracelink init` — the smallest thing that makes `sync` work.

Two rules, and everything here follows from them.

**It creates; it does not repair.** Anything already present is left exactly
as it is and reported as kept — no merging a config, no rewriting a register,
no "fixing" a vault. A command that quietly repairs is a command you cannot
run twice without reading its source first, and the second run is the one
people do when something already looks wrong. Diagnosis belongs to `doctor`.

**It detects rather than asks.** No wizard, no prompts. Whether this is a git
repository, and which symbol backend has its input on disk right now, are
facts to be looked up and reported — with the reason, so a `scan` that was
chosen because there is no `tags` file says so instead of looking like a
preference.

Everything it writes is listed, so the output is the audit of the run.
"""

from __future__ import annotations

import json
import os
import sys

from . import config as _config
from .consult import EXIT_OK, EXIT_USAGE

#: A register that explains, in the file itself, what a linkable finding is.
STARTER_REGISTER = """# Findings

<!-- One finding per heading. The heading carries the id and the severity;
     `### STATUS:` and `### SEVERITY:` lines override them later, and the
     last one wins — a finding downgraded from HIGH to LOW must not keep
     reading HIGH.

     Name code in backticks: `compute_total`, `payments.validate`,
     `src/app.py`. Prose alone links to nothing, and TraceLink will tell
     you so rather than guess. -->

## {prefix}-01 — replace this with something you learned [MEDIUM]
### STATUS: OPEN

`example_symbol` in `src/example.py` does something worth remembering.
Say what is true, where it is true, and how you know.
"""


class Step:
    __slots__ = ("action", "what", "detail")

    def __init__(self, action, what, detail=""):
        self.action = action      # created | kept | detected
        self.what = what
        self.detail = detail


def detect_backend(project):
    """(backend, why) — chosen by what is on disk, not by preference."""
    if os.path.exists(os.path.join(project, "graphify-out", "graph.json")):
        return "graphify", "graphify-out/graph.json is present"
    if os.path.exists(os.path.join(project, "tags")):
        return "ctags", "a tags file is present"
    return "scan", ("no tags file and no graphify-out/graph.json — the "
                    "built-in scanner needs neither")


def is_git_repository(project):
    path = os.path.join(project, ".git")
    return os.path.isdir(path) or os.path.isfile(path)  # worktrees are files


def init(project, register=None, prefix=None, backend=None):
    """Create what is missing. Returns the steps taken, in order."""
    steps = []
    existing = _config.read(project)
    register = register or existing["register"]
    prefix = prefix or existing["prefix"]
    detected, why = detect_backend(project)
    backend = backend or detected

    directory = os.path.join(project, _config.CONFIG_DIR)
    if os.path.isdir(directory):
        steps.append(Step("kept", f"{_config.CONFIG_DIR}/"))
    else:
        os.makedirs(directory)
        steps.append(Step("created", f"{_config.CONFIG_DIR}/"))

    config_path = _config.path(project)
    if os.path.exists(config_path):
        steps.append(Step("kept", os.path.relpath(config_path, project),
                          "left exactly as it is"))
    else:
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump({"register": register, "prefix": prefix,
                       "backend": backend}, fh, indent=1)
            fh.write("\n")
        steps.append(Step("created", os.path.relpath(config_path, project)))

    register_path = (register if os.path.isabs(register)
                     else os.path.join(project, register))
    if os.path.exists(register_path):
        steps.append(Step("kept", register, "your register, untouched"))
    else:
        with open(register_path, "w", encoding="utf-8") as fh:
            fh.write(STARTER_REGISTER.format(prefix=prefix))
        steps.append(Step("created", register))

    steps.append(Step("detected", "git repository"
                      if is_git_repository(project) else "no git repository",
                      "" if is_git_repository(project)
                      else "freshness will be reported as unknown"))
    steps.append(Step("detected", f"symbol backend: {backend}", why))
    return steps


def render_text(steps, project):
    lines = ["TraceLink init", ""]
    for step in steps:
        line = f"  {step.action:<9} {step.what}"
        if step.detail:
            line += f"  — {step.detail}"
        lines.append(line)
    lines += ["", "  next: tracelink sync"]
    return "\n".join(lines)


def main(argv=None, prog=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog=prog,
        description="Create the minimum a project needs for `tracelink "
                    "sync`: a .tracelink directory, a config, and a register "
                    "if there is none. Never overwrites, never repairs — run "
                    "it twice and the second run changes nothing.")
    ap.add_argument("--repo", default=".", help="repository root (default .)")
    ap.add_argument("--register", default=None,
                    help="findings register to use or create "
                         "(default FINDINGS.md)")
    ap.add_argument("--prefix", default=None,
                    help="finding id prefix (default RES)")
    ap.add_argument("--backend", default=None,
                    help="symbol backend; detected from the repository when "
                         "not given")
    ap.add_argument("--json", action="store_true",
                    help="print the machine-readable document instead")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.repo):
        print(f"no such directory: {args.repo}", file=sys.stderr)
        return EXIT_USAGE

    steps = init(args.repo, args.register, args.prefix, args.backend)
    if args.json:
        print(json.dumps({"schema_version": 1,
                          "steps": [{"action": s.action, "what": s.what,
                                     "detail": s.detail} for s in steps]},
                         indent=2, ensure_ascii=False))
    else:
        print(render_text(steps, args.repo))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
