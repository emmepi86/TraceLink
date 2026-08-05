#!/usr/bin/env python3
"""Keep a project's tracelink vault fresh from the Claude Code plugin hooks.

Two modes, two budgets:

  mark     runs after every Edit/Write/MultiEdit (PostToolUse). If the project
           has a `.tracelink/` directory, touch `.tracelink/.stale` and stop.
           Nothing else — no tracelink import, no tree walk — because this
           runs on EVERY edit and must cost less than the edit itself.
  refresh  runs once at end of turn (Stop). If the marker exists, rebuild
           `.tracelink/symbols.json` and relink `.tracelink/vault` in-process,
           then remove the marker. If the repository holds more source files
           than the scan backend would index (MAX_FILES), skip the work, say
           so once on stderr, and STILL remove the marker — a surviving marker
           would retry the same skip on every following turn.

The project directory is resolved in this order (the overrides exist so tests
can point the script at a fixture without touching the real environment):

  1. an explicit second argument:  plugin_refresh.py refresh /path/to/project
  2. $TRACELINK_PROJECT_DIR
  3. $CLAUDE_PROJECT_DIR           (what Claude Code sets for hooks)
  4. the current working directory

Exit code is ALWAYS 0. A hook that fails blocks Claude, and no refresh is
worth that; failures are a line on stderr at most, and stderr is kept for
things worth interrupting a human for.
"""

from __future__ import annotations

import os
import sys

#: Same ceiling as the scan backend's max_files: past it the index would be
#: partial anyway, so an automatic background refresh stops pretending.
MAX_FILES = 20000

MARKER = ".stale"


def resolve_project(arg=None):
    return (arg
            or os.environ.get("TRACELINK_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd())


def mark(project):
    """Touch the stale marker — only for projects that opted into tracelink."""
    tl = os.path.join(project, ".tracelink")
    if not os.path.isdir(tl):
        return
    marker = os.path.join(tl, MARKER)
    with open(marker, "a"):
        os.utime(marker, None)


def _count_source_files(project, limit):
    """How many files the scan backend would consider, stopping past `limit`.

    Imports tracelink for the extension and skip lists rather than copying
    them: a copy would drift, and refresh pays for the import anyway.
    """
    from tracelink.symbol_index import _DEF_PATTERNS, _SKIP_DIRS
    exts = {e for e, _rx in _DEF_PATTERNS}
    count = 0
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if os.path.splitext(fn)[1] in exts:
                count += 1
                if count > limit:
                    return count
    return count


def _run(entry, argv):
    """Call another script's main() in-process with its own argv, swallowing
    its chatter. Returns (exit_code, captured_stderr_tail)."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    saved = sys.argv
    try:
        sys.argv = argv
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = entry()
    except SystemExit as exc:  # argparse errors and explicit exits
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001 — never propagate out of a hook
        code = 1
        err.write(f"{type(exc).__name__}: {exc}\n")
    finally:
        sys.argv = saved
    tail = err.getvalue().strip().splitlines()
    return code, (tail[-1] if tail else "")


def refresh(project):
    """Rebuild index + links if the marker says an edit happened this turn."""
    tl = os.path.join(project, ".tracelink")
    marker = os.path.join(tl, MARKER)
    if not os.path.exists(marker):
        return
    # Consumed up front: whatever happens below, the next turn starts clean
    # instead of retrying a refresh that just demonstrated it cannot succeed.
    try:
        os.remove(marker)
    except OSError:
        pass
    vault = os.path.join(tl, "vault")
    if not os.path.isdir(vault):
        return  # marked stale before the first split — nothing to refresh yet

    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "src"))

    if _count_source_files(project, MAX_FILES) > MAX_FILES:
        print(f"tracelink: over {MAX_FILES} source files — automatic refresh "
              "skipped; run `tracelink index` + `tracelink link` manually",
              file=sys.stderr)
        return

    from tracelink.linker import main as link_main
    from tracelink.symbol_index import main as index_main

    symbols = os.path.join(tl, "symbols.json")
    code, detail = _run(index_main,
                        ["tracelink-index", "--repo", project,
                         "--out", symbols])
    if code != 0:
        print(f"tracelink: auto-refresh index failed ({detail or code})",
              file=sys.stderr)
        return
    code, detail = _run(link_main,
                        ["tracelink-link", "--vault", vault,
                         "--symbols", symbols, "--repo", project])
    if code != 0:
        print(f"tracelink: auto-refresh link failed ({detail or code})",
              file=sys.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        mode = argv[0] if argv else ""
        project = resolve_project(argv[1] if len(argv) > 1 else None)
        if mode == "mark":
            mark(project)
        elif mode == "refresh":
            refresh(project)
        # any other mode: a misconfigured hook must not become a blocked turn
    except Exception as exc:  # noqa: BLE001
        try:
            print(f"tracelink hook: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        except Exception:  # noqa: BLE001 — even a dead stderr must not raise
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
