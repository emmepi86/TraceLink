#!/usr/bin/env python3
"""Keep a project's tracelink vault fresh from the Claude Code plugin hooks.

Four modes, four budgets:

  mark     runs after every Edit/Write/MultiEdit (PostToolUse). If the project
           has a `.tracelink/` directory, touch `.tracelink/.stale` and stop.
           Nothing else — no tracelink import, no tree walk — because this
           runs on EVERY edit and must cost less than the edit itself.
  consult  what on-edit.sh actually invokes: mark, then — only when the
           project opted in with `"consult": true` in `.tracelink/config.json`
           — read the hook payload from stdin and, if the linker's sidecar
           state says the vault holds notes about the edited file, print a
           `hookSpecificOutput.additionalContext` JSON so Claude Code injects
           those findings into the same turn. The lookup uses ONLY the
           link-state already on disk (no tracelink import, no vault walk),
           and only the notes that actually match get their frontmatter read.
           A file without notes costs one config read and one json load.
  refresh  runs once at end of turn (Stop). If the marker exists, rebuild
           `.tracelink/symbols.json` and relink `.tracelink/vault` in-process,
           then remove the marker. If the repository holds more source files
           than the scan backend would index (MAX_FILES), skip the work, say
           so once on stderr, and STILL remove the marker — a surviving marker
           would retry the same skip on every following turn.
  capture-check
           what on-stop.sh actually invokes: refresh, then — only when the
           project opted in with `"capture": true` in `.tracelink/config.json`
           — ask whether this session's edits were ever distilled into the
           findings register. Three markers in `.tracelink/`, all distinct
           from `.stale` (which refresh consumes every turn): `.session-edits`
           says an edit happened while capture was on, `.capture-baseline`
           freezes size+mtime+sha of the register BEFORE the session's first
           edit (written by mark, so growth is measured against the
           pre-session register), `.capture-prompted` says the one prompt was
           already spent. When the register did not grow and the prompt is
           unspent, print `{"decision": "block", "reason": ...}` — the
           documented Stop-hook block for a command hook, which requires
           exit 0 — with the distillation instruction as the reason. A
           payload whose `stop_hook_active` is true always passes: that stop
           IS the continuation our own block caused, and blocking it again
           would loop forever.

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

#: Capture markers. `.stale` is consumed by every refresh; these three live
#: for a whole capture cycle — until the register grows — so they are
#: separate files, not extra meanings piled onto `.stale`.
_SESSION_MARKER = ".session-edits"
_BASELINE_MARKER = ".capture-baseline"
_PROMPTED_MARKER = ".capture-prompted"
_DEFAULT_REGISTER = "FINDINGS.md"


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
    _mark_session(project, tl)


def _touch(path):
    with open(path, "a"):
        os.utime(path, None)


def _mark_session(project, tl):
    """Capture bookkeeping, gated like consult: it must be asked for.

    On the FIRST edit of a session the register is fingerprinted into
    `.capture-baseline` — before the session has had any chance to append to
    it, so `capture_check` later measures growth against the pre-session
    register. Every further edit only touches `.session-edits`.
    """
    if _read_config(project).get("capture") is not True:
        return
    session = os.path.join(tl, _SESSION_MARKER)
    if not os.path.exists(session):
        _write_baseline(os.path.join(tl, _BASELINE_MARKER),
                        _register_path(project)[0])
    _touch(session)


def _register_path(project, cfg=None):
    """(absolute path, configured spelling) of the findings register."""
    reg = (_read_config(project) if cfg is None else cfg).get("register")
    if not isinstance(reg, str) or not reg:
        reg = _DEFAULT_REGISTER
    return (reg if os.path.isabs(reg)
            else os.path.join(project, reg)), reg


def _register_sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _write_baseline(baseline_path, register_abs):
    """Freeze what the register looked like before this session touched
    anything. A register that does not exist yet is itself a fact worth
    recording: any register at all later means the session recorded."""
    import json
    try:
        st = os.stat(register_abs)
        payload = {"exists": True, "size": st.st_size, "mtime": st.st_mtime,
                   "sha256": _register_sha(register_abs)}
    except OSError:
        payload = {"exists": False}
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


#: Severity sort rank for consult; anything unknown sinks below `low`.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: How many notes consult will show before deferring to CODE-INDEX.md.
_MAX_CONSULT_NOTES = 5

_STATE_FILE = ".tracelink-link-state.json"
_STATE_SCHEMA = 2


def _read_config(project):
    """The opt-in gates from `.tracelink/config.json`, as a dict.

    Absent file, unreadable file, broken json, json that is not an object:
    all mean `{}` — and a missing key means the feature stays OFF. Consult
    speaks up inside other people's turns; it must be asked for.
    """
    import json
    try:
        with open(os.path.join(project, ".tracelink", "config.json"),
                  encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:  # noqa: BLE001 — no config shape may break a hook
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _note_head(vault, note_file):
    """(id, status, severity, title) from the top of one note — and only the
    top: the frontmatter plus the first `# ` heading after it. Notes can be
    long; consult lives inside a PostToolUse budget and never reads bodies."""
    stem = note_file[:-3] if note_file.endswith(".md") else note_file
    note_id, status, severity, title = stem, "", "", ""
    try:
        with open(os.path.join(vault, note_file), encoding="utf-8") as fh:
            in_front = False
            for lineno, raw in enumerate(fh):
                if lineno > 200:  # no heading by now — give up, keep the id
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
    """`RES-01 — totals ignore tax [HIGH]` → `totals ignore tax`: the bullet
    already prints the id and severity next to the title."""
    for sep in (" — ", " – ", " - "):
        if title.startswith(note_id + sep):
            title = title[len(note_id) + len(sep):]
            break
    if severity:
        suffix = "[" + severity + "]"
        if title.lower().endswith(suffix.lower()):
            title = title[:-len(suffix)]
    return title.strip()


def consult(project, payload_text):
    """The additionalContext JSON for the file in `payload_text`, or "".

    Every early return is silence: gate closed, payload unusable, state
    missing/corrupt/wrong-schema, file not linked by any note. Only when the
    link-state names the edited file does this open anything under the vault,
    and then only the heads of the matching notes.
    """
    import json

    if _read_config(project).get("consult") is not True:
        return ""
    try:
        payload = json.loads(payload_text)
    except Exception:  # noqa: BLE001
        return ""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return ""
    if not os.path.isabs(file_path):
        file_path = os.path.join(project, file_path)
    rel = os.path.relpath(os.path.realpath(file_path),
                          os.path.realpath(project)).replace(os.sep, "/")

    vault = os.path.join(project, ".tracelink", "vault")
    try:
        with open(os.path.join(vault, _STATE_FILE), encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:  # noqa: BLE001 — absent or corrupt state is silence
        return ""
    if not isinstance(state, dict) or state.get("schema_version") != _STATE_SCHEMA:
        return ""
    notes = state.get("notes")
    if not isinstance(notes, dict):
        return ""

    hits = []  # (note_file, [(symbol, line), ...]) for notes linking `rel`
    for note_file, entry in notes.items():
        if not isinstance(entry, dict):
            continue
        linked, locations = entry.get("linked"), entry.get("locations")
        if not isinstance(linked, list) or not isinstance(locations, list):
            continue
        symbols = [(name, loc.get("line"))
                   for name, loc in zip(linked, locations)
                   if isinstance(name, str) and isinstance(loc, dict)
                   and loc.get("path") == rel]
        if symbols:
            hits.append((str(note_file), symbols))
    if not hits:
        return ""

    ranked = []
    for note_file, symbols in hits:
        note_id, status, severity, title = _note_head(vault, note_file)
        ranked.append((0 if status == "open" else 1,
                       _SEVERITY_RANK.get(severity, len(_SEVERITY_RANK)),
                       -len(symbols), note_id,
                       (note_id, status, severity, title, symbols)))
    ranked.sort(key=lambda r: r[:4])
    shown = [r[-1] for r in ranked[:_MAX_CONSULT_NOTES]]
    hidden = len(ranked) - len(shown)

    lines = [f"TraceLink — known findings about this file ({rel}):"]
    for note_id, status, severity, title, symbols in shown:
        tag = "/".join(p for p in (status, severity) if p) or "?"
        syms = ", ".join(f"{name} (L{line})" if isinstance(line, int)
                         else name for name, line in symbols)
        head = f"- {note_id} [{tag}]" + (f" {title}" if title else "")
        lines.append(f"{head} — symbols: {syms}")
    lines.append("(full notes: .tracelink/vault/<id>.md — read before "
                 "assuming this area is clean)")
    if hidden:
        lines.append(f"…and {hidden} more in CODE-INDEX.md")
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines)}})


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


# --------------------------------------------------------------------------- #
# capture — did this session's edits ever reach the register?
# --------------------------------------------------------------------------- #


def _clear_session(tl):
    """Close the capture cycle: the next session starts with a fresh
    baseline and a fresh prompt."""
    for name in (_SESSION_MARKER, _BASELINE_MARKER, _PROMPTED_MARKER):
        try:
            os.remove(os.path.join(tl, name))
        except OSError:
            pass


def _register_grew(tl, register_abs):
    """Positive evidence that the register changed since the baseline.

    Anything short of that proof — no register, no baseline, an unreadable
    baseline — is False: capture then prompts, and a wrong prompt costs one
    sentence, where a wrongly-swallowed one costs the session's memory.
    """
    import json
    if not os.path.isfile(register_abs):
        return False
    try:
        with open(os.path.join(tl, _BASELINE_MARKER), encoding="utf-8") as fh:
            baseline = json.load(fh)
    except Exception:  # noqa: BLE001 — corrupt baseline must not block
        return False
    if not isinstance(baseline, dict):
        return False
    if baseline.get("exists") is False:
        return True  # the session created the register: it recorded
    try:
        st = os.stat(register_abs)
        if (st.st_size == baseline.get("size")
                and st.st_mtime == baseline.get("mtime")):
            return False  # cheap prefilter: demonstrably untouched
        return _register_sha(register_abs) != baseline.get("sha256")
    except OSError:
        return False


def _register_prefix(project):
    """The finding-id prefix the reason should show — the vault manifest's
    when a vault exists, `RES` otherwise (the same fallback status uses)."""
    import json
    try:
        with open(os.path.join(project, ".tracelink", "vault",
                               ".tracelink-manifest.json"),
                  encoding="utf-8") as fh:
            man = json.load(fh)
        prefix = (man.get("register") or {}).get("prefix") or man.get("prefix")
        if isinstance(prefix, str) and prefix:
            return prefix
    except Exception:  # noqa: BLE001
        pass
    return "RES"


def _capture_reason(project, register_name):
    """An operative prompt, not an error message: it tells the agent exactly
    what shape a finding takes and how to verify what it wrote."""
    prefix = _register_prefix(project)
    lint_cmd = f"tracelink lint --register {register_name}"
    if os.path.isdir(os.path.join(project, ".tracelink", "vault")):
        lint_cmd += " --vault .tracelink/vault"
    return ("TraceLink capture: this session edited code but recorded "
            f"nothing. Append durable discoveries to {register_name} as "
            f"findings (### {prefix}-<n> heading, ### STATUS:/### SEVERITY: "
            "lines, name the exact symbols/files). Only facts worth knowing "
            "next session — constraints, gotchas, invariants, fixed bugs. "
            "If nothing qualifies, append nothing and stop again. "
            f"Then run: {lint_cmd}")


def capture_check(project, payload_text):
    """The Stop-block JSON for a session that recorded nothing, or "".

    Every early return is a pass-through: gate closed, our own block already
    continuing (`stop_hook_active`), no edits this session, register grown
    (cycle closed, markers cleared), prompt already spent. Only the first
    stop of a session that edited code and grew nothing gets the one block —
    `{"decision": "block", "reason": ...}` printed by the caller with exit 0,
    which is what makes a command-type Stop hook block.
    """
    import json

    cfg = _read_config(project)
    if cfg.get("capture") is not True:
        return ""
    try:
        payload = json.loads(payload_text)
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, dict) and payload.get("stop_hook_active"):
        return ""
    tl = os.path.join(project, ".tracelink")
    if not os.path.exists(os.path.join(tl, _SESSION_MARKER)):
        return ""
    register_abs, register_name = _register_path(project, cfg)
    if _register_grew(tl, register_abs):
        _clear_session(tl)
        return ""
    prompted = os.path.join(tl, _PROMPTED_MARKER)
    if os.path.exists(prompted):
        return ""  # one prompt per session; the agent already declined
    _touch(prompted)
    return json.dumps({"decision": "block",
                       "reason": _capture_reason(project, register_name)})


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        mode = argv[0] if argv else ""
        project = resolve_project(argv[1] if len(argv) > 1 else None)
        if mode == "mark":
            mark(project)
        elif mode == "consult":
            # One process for the whole PostToolUse: mark first (the cheap,
            # unconditional duty), then read the payload the wrapper now
            # forwards and maybe speak. Reading stdin also drains it, so the
            # writer never blocks whatever else happens.
            mark(project)
            out = consult(project, sys.stdin.read())
            if out:
                print(out)
        elif mode == "refresh":
            refresh(project)
        elif mode == "capture-check":
            # One process for the whole Stop: drain stdin first (the writer
            # must never block on a long refresh), refresh — the original
            # duty — then maybe block the stop. The block JSON is the ONLY
            # thing ever printed to stdout here.
            payload = sys.stdin.read()
            refresh(project)
            out = capture_check(project, payload)
            if out:
                print(out)
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
