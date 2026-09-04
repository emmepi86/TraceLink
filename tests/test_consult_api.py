"""ms-2: consult is a library call, and the Claude hook is one of its callers.

The lookup used to live inside `scripts/plugin_refresh.py`, shaped around a
PostToolUse payload. It now lives in `tracelink.consult`, returns objects,
and the hook is an adapter that owns only what is Claude-shaped: the opt-in
gate, the payload, the envelope.

Three properties are worth a test each:

  1. **The briefing did not change.** The text the hook injects is compared
     byte-for-byte against the output the 0.8.0 implementation produced for
     the same vault — ordering, cap, hidden count, the file-anchor line and
     the empty-severity tag included.
  2. **The core is a leaf.** Importing `tracelink.consult` must not drag in
     `linker` (~30ms) or `dataclasses` (~26ms). This runs after every edit;
     a stray import is a regression nobody would notice by reading.
  3. **Silence has a reason.** Missing state, wrong schema and "no note
     mentions this file" are three different answers, not one empty string.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
REFRESH = os.path.join(ROOT, "scripts", "plugin_refresh.py")
sys.path.insert(0, SRC)

from tracelink import consult as consult_mod  # noqa: E402
from tracelink.consult import consult, render_text  # noqa: E402

#: The 0.8.0 output for FIXTURE, captured before the extraction.
GOLDEN = (
    "TraceLink — known findings about this file (src/app.py):\n"
    "- RES-03 [open/critical] retry storm — symbols: c (L3)\n"
    "- RES-02 [open/high] totals ignore tax — symbols: compute_total (L12), "
    "validate (L30)\n"
    "- RES-04 [open/medium] four — symbols: d (L4)\n"
    "- RES-06 [open/low] six — symbols: f (L6)\n"
    "- RES-05 [open] no severity recorded — file: src/app.py\n"
    "(full notes: .tracelink/vault/<id>.md — read before assuming this area "
    "is clean)\n"
    "…and 2 more in CODE-INDEX.md")

#: (id, status, severity, title, [(symbol, path, line)], [file anchors]).
#: Covers ranking (open first, then severity, then most reasons, then id),
#: the cap at five with a hidden count, a note anchored only to the file, a
#: note with no severity, and a note about a different file.
FIXTURE = [
    ("RES-01", "closed", "critical", "old news", [("a", "src/app.py", 1)], []),
    ("RES-02", "open", "high", "totals ignore tax",
     [("compute_total", "src/app.py", 12), ("validate", "src/app.py", 30)], []),
    ("RES-03", "open", "critical", "retry storm", [("c", "src/app.py", 3)], []),
    ("RES-04", "open", "medium", "four", [("d", "src/app.py", 4)], []),
    ("RES-05", "open", "", "no severity recorded", [], ["src/app.py"]),
    ("RES-06", "open", "low", "six", [("f", "src/app.py", 6)], []),
    ("RES-07", "closed", "medium", "seven", [("g", "src/app.py", 7)], []),
    ("RES-08", "open", "high", "elsewhere", [("z", "src/other.py", 9)], []),
]


def note_md(note_id, status, severity, title):
    heading = f"# {note_id} — {title}" + (f" [{severity.upper()}]"
                                          if severity else "")
    return ("---\ntracelink_schema: 1\n"
            f"tracelink_id: {note_id}\nid: {note_id}\nstatus: {status}\n"
            f"severity: {severity}\nsections: 1\n---\n\n"
            "<!-- tracelink:linked-code:start -->\n## Linked code\n\n"
            "- `x` — src/app.py:L1\n<!-- tracelink:linked-code:end -->\n\n"
            f"{heading}\n\nBody that consult must never need to read.\n")


def make_project(tmp, notes=FIXTURE, schema=3, state="normal",
                 config={"consult": True}):
    """A project with a hand-built vault + link-state, as the linker writes
    it. `state`: "normal", "absent", "corrupt" or "not-an-object"."""
    proj = os.path.join(tmp, "proj")
    vault = os.path.join(proj, ".tracelink", "vault")
    os.makedirs(vault)
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "app.py"), "w") as fh:
        fh.write("x = 1\n")
    payload = {"schema_version": schema, "symbols_fingerprint": "sha256:0",
               "options_fingerprint": "sha256:0", "symbol_locations": {},
               "notes": {}}
    for note_id, status, severity, title, symbols, files in notes:
        name = note_id + ".md"
        with open(os.path.join(vault, name), "w") as fh:
            fh.write(note_md(note_id, status, severity, title))
        payload["notes"][name] = {
            "content_hash": "sha256:0",
            "linked": [s[0] for s in symbols],
            "locations": [{"path": s[1], "line": s[2]} for s in symbols],
            "files": list(files), "files_fingerprint": "sha256:0"}
    path = os.path.join(vault, consult_mod.STATE_FILE)
    if state == "normal":
        with open(path, "w") as fh:
            json.dump(payload, fh)
    elif state == "corrupt":
        with open(path, "w") as fh:
            fh.write("{not json")
    elif state == "not-an-object":
        with open(path, "w") as fh:
            fh.write("[]")
    if config is not None:
        with open(os.path.join(proj, ".tracelink", "config.json"), "w") as fh:
            json.dump(config, fh)
    return proj


class TheBriefingDidNotChange(unittest.TestCase):
    def test_library_call_renders_the_0_8_0_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            result = consult(proj, os.path.join(proj, "src", "app.py"))
            self.assertEqual(GOLDEN, render_text(result))

    def test_the_hook_injects_exactly_that_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            env = dict(os.environ)
            env.pop("CLAUDE_PROJECT_DIR", None)
            env.pop("TRACELINK_PROJECT_DIR", None)
            run = subprocess.run(
                [sys.executable, REFRESH, "consult", proj],
                input=json.dumps({"tool_name": "Edit", "tool_input": {
                    "file_path": os.path.join(proj, "src", "app.py")}}),
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(0, run.returncode, run.stderr)
            payload = json.loads(run.stdout)["hookSpecificOutput"]
            self.assertEqual("PostToolUse", payload["hookEventName"])
            self.assertEqual(GOLDEN, payload["additionalContext"])

    def test_a_relative_target_is_the_same_as_an_absolute_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            self.assertEqual(render_text(consult(proj, "src/app.py")),
                             render_text(consult(proj, os.path.join(
                                 proj, "src", "app.py"))))


class TheCoreIsALeaf(unittest.TestCase):
    """Cost is a property of the import graph, so the test reads the graph."""

    FORBIDDEN = ("tracelink.linker", "tracelink.symbol_index",
                 "tracelink.lint", "tracelink.status", "tracelink.splitter",
                 "dataclasses", "argparse", "typing")

    def test_importing_consult_pulls_in_nothing_expensive(self):
        probe = ("import sys; sys.path.insert(0, sys.argv[1]);"
                 "import tracelink.consult;"
                 "print(' '.join(sorted(m for m in sys.modules"
                 " if m.startswith('tracelink') or m in"
                 f" {self.FORBIDDEN!r})))")
        run = subprocess.run([sys.executable, "-c", probe, SRC],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(0, run.returncode, run.stderr)
        loaded = set(run.stdout.split())
        self.assertEqual({"tracelink", "tracelink.consult"}, loaded,
                         "consult must import stdlib only")

    def test_the_module_declares_no_intra_package_imports(self):
        """The runtime probe proves it for this import order; the syntax
        tree proves it for every import order, including a lazy one.

        One import is deliberately lazy and therefore allowed: `main` pulls
        argparse, which the hook never reaches. Anything expensive at module
        level, and anything from tracelink anywhere, is a regression.
        """
        import ast
        with open(os.path.join(SRC, "tracelink", "consult.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        def names(node):
            if isinstance(node, ast.Import):
                return {a.name.split(".")[0] for a in node.names}
            if isinstance(node, ast.ImportFrom):
                if node.level:  # `from . import x`
                    return {"tracelink"}
                return {node.module.split(".")[0]} if node.module else set()
            return set()

        top = set()
        for node in tree.body:
            top |= names(node)
        self.assertEqual(set(), top & set(self.FORBIDDEN),
                         "expensive import at module level")

        everywhere, lazy_argparse_in = set(), []
        for node in ast.walk(tree):
            found = names(node)
            everywhere |= found
            if "argparse" in found:
                lazy_argparse_in.append(node)
        self.assertNotIn("tracelink", everywhere,
                         "the leaf module imports the package")

        inside_main = set()
        for func in tree.body:
            if isinstance(func, ast.FunctionDef) and func.name == "main":
                for node in ast.walk(func):
                    inside_main |= names(node)
        self.assertTrue(lazy_argparse_in, "argparse should be imported lazily")
        self.assertIn("argparse", inside_main,
                      "argparse must be imported inside main(), nowhere else")

    def test_the_linker_shares_these_constants_instead_of_copying_them(self):
        from tracelink import linker
        self.assertIs(consult_mod.STATE_FILE, linker.STATE_FILE)
        self.assertEqual(consult_mod.STATE_SCHEMA, linker._STATE_SCHEMA)


class SilenceHasAReason(unittest.TestCase):
    def result_for(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, **kwargs)
            return consult(proj, "src/app.py")

    def test_no_state_at_all(self):
        self.assertEqual("no-link-state",
                         self.result_for(state="absent").silence)

    def test_corrupt_state(self):
        self.assertEqual("no-link-state",
                         self.result_for(state="corrupt").silence)

    def test_state_that_is_not_an_object(self):
        self.assertEqual("no-link-state",
                         self.result_for(state="not-an-object").silence)

    def test_a_schema_from_another_version_is_not_guessed_at(self):
        self.assertEqual("state-schema", self.result_for(schema=2).silence)

    def test_a_file_no_note_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            result = consult(proj, "src/untouched.py")
            self.assertEqual("not-linked", result.silence)
            self.assertEqual("", render_text(result))

    def test_an_empty_target(self):
        self.assertEqual("no-target", consult("/tmp", "").silence)

    def test_a_result_with_notes_carries_no_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            self.assertIsNone(consult(proj, "src/app.py").silence)


class TheResultIsObjects(unittest.TestCase):
    def consult(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, **kwargs)
            return consult(proj, "src/app.py")

    def test_ranked_capped_and_counted(self):
        result = self.consult()
        self.assertEqual(["RES-03", "RES-02", "RES-04", "RES-06", "RES-05"],
                         [n.note_id for n in result.notes])
        self.assertEqual(2, result.hidden)
        self.assertEqual(7, result.total)  # RES-08 is about another file
        self.assertTrue(result)

    def test_symbols_carry_their_lines(self):
        note = {n.note_id: n for n in self.consult().notes}["RES-02"]
        self.assertEqual(["compute_total", "validate"],
                         [s.name for s in note.symbols])
        self.assertEqual([12, 30], [s.line for s in note.symbols])
        self.assertEqual(2, note.matches)
        self.assertTrue(note.is_open)
        self.assertFalse(note.file_anchor)

    def test_a_note_can_match_the_file_itself(self):
        note = {n.note_id: n for n in self.consult().notes}["RES-05"]
        self.assertTrue(note.file_anchor)
        self.assertEqual((), note.symbols)
        self.assertEqual(1, note.matches)

    def test_the_title_drops_the_id_and_severity_the_renderer_reprints(self):
        note = {n.note_id: n for n in self.consult().notes}["RES-02"]
        self.assertEqual("totals ignore tax", note.title)

    def test_limit_is_the_callers_to_choose(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            result = consult(proj, "src/app.py", limit=2)
            self.assertEqual(2, len(result.notes))
            self.assertEqual(5, result.hidden)

    def test_a_note_that_is_not_utf_8_is_read_anyway_never_raised(self):
        """0.8.0 let a UnicodeDecodeError out of the hook. Corrupt input is
        silence or degraded text — never an exception in someone's turn."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            path = os.path.join(proj, ".tracelink", "vault", "RES-02.md")
            with open(path, "wb") as fh:
                fh.write(b"---\ntracelink_id: RES-02\nstatus: open\n"
                         b"severity: high\n---\n\n# RES-02 \xff\xfe bad\n")
            result = consult(proj, "src/app.py")
            note = {n.note_id: n for n in result.notes}["RES-02"]
            self.assertEqual("open", note.status)
            self.assertIn("RES-02", render_text(result))

    def test_a_missing_line_is_not_invented(self):
        notes = [("RES-01", "open", "high", "no line",
                  [("a", "src/app.py", None)], [])]
        result = self.consult(notes=notes)
        self.assertIsNone(result.notes[0].symbols[0].line)
        self.assertIn("— symbols: a\n", render_text(result) + "\n")


if __name__ == "__main__":
    unittest.main()
