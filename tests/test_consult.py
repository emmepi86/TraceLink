"""0.7.0 — consult: the codebase warns the agent that is editing it.

The same PostToolUse hook that marks the vault stale now also answers a
question: does the vault already know something about the file just edited?
If it does — and only if the project opted in via `.tracelink/config.json` —
the hook prints a `hookSpecificOutput.additionalContext` JSON that Claude
Code injects into the very turn the edit happened in.

Two properties are non-negotiable and get their own tests here. First, the
common case — a file no note links — must cost almost nothing: one config
read, one link-state read, zero note files opened. Second, nothing in this
path may ever block Claude: corrupt state, corrupt config, corrupt stdin all
mean silent stdout and exit 0.

    python3 -m unittest discover tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
HOOKS = os.path.join(ROOT, "hooks")
REFRESH = os.path.join(SCRIPTS, "plugin_refresh.py")

sys.path.insert(0, SCRIPTS)

import plugin_refresh  # noqa: E402

STATE_FILE = ".tracelink-link-state.json"


def note_md(note_id, status, severity, title):
    """A note exactly as split+link leave it: frontmatter, linked-code block,
    then the `# id — title [SEV]` heading."""
    return (f"---\n"
            f"tracelink_schema: 1\n"
            f"tracelink_id: {note_id}\n"
            f"id: {note_id}\n"
            f"status: {status}\n"
            f"severity: {severity}\n"
            f"sections: 1\n"
            f"---\n\n"
            "<!-- tracelink:linked-code:start -->\n"
            "## Linked code\n\n"
            "- `x` — src/app.py:L1\n"
            "<!-- tracelink:linked-code:end -->\n\n"
            f"# {note_id} — {title} [{severity.upper()}]\n\n"
            "Body that consult must never need to read.\n")


def make_project(tmp, notes, config={"consult": True}):
    """A project with a hand-built vault + link-state (v3, same shape the
    linker writes). `notes`: (id, status, severity, title, [(sym, path, line)]).
    `config`: dict (written as json), raw str (written verbatim), or None
    (no config file at all)."""
    proj = os.path.join(tmp, "proj")
    vault = os.path.join(proj, ".tracelink", "vault")
    os.makedirs(vault)
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "app.py"), "w") as fh:
        fh.write("def compute_total(items):\n    return sum(items)\n")
    state = {"schema_version": 3,
             "symbols_fingerprint": "sha256:0",
             "options_fingerprint": "sha256:0",
             "symbol_locations": {},
             "notes": {}}
    for note_id, status, severity, title, symbols in notes:
        fname = note_id + ".md"
        with open(os.path.join(vault, fname), "w") as fh:
            fh.write(note_md(note_id, status, severity, title))
        state["notes"][fname] = {
            "content_hash": "sha256:0",
            "linked": [s[0] for s in symbols],
            "locations": [{"path": s[1], "line": s[2]} for s in symbols],
            "files": [],
            "files_fingerprint": "sha256:0",
        }
    with open(os.path.join(vault, STATE_FILE), "w") as fh:
        json.dump(state, fh)
    if config is not None:
        with open(os.path.join(proj, ".tracelink", "config.json"), "w") as fh:
            fh.write(config if isinstance(config, str)
                     else json.dumps(config))
    return proj


def payload_for(path):
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})


def run_consult(proj, stdin_text):
    """Invoke plugin_refresh.py consult the way on-edit.sh does."""
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("TRACELINK_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, REFRESH, "consult", proj],
        input=stdin_text, capture_output=True, text=True, env=env,
        timeout=120)


ONE_NOTE = [("RES-01", "open", "high", "totals ignore tax",
             [("compute_total", "src/app.py", 1),
              ("validate", "src/app.py", 5)])]


def context_of(r):
    out = json.loads(r.stdout)
    return out["hookSpecificOutput"]


class TestConsultEmits(unittest.TestCase):
    """A file the vault knows about, in a project that opted in."""

    def test_notes_become_additional_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            r = run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py")))
        self.assertEqual(r.returncode, 0, r.stderr)
        hso = context_of(r)
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        ctx = hso["additionalContext"]
        self.assertIn("src/app.py", ctx)
        self.assertIn("RES-01", ctx)
        self.assertIn("[open/high]", ctx)
        self.assertIn("compute_total (L1)", ctx)
        self.assertIn("validate (L5)", ctx)
        self.assertIn(".tracelink/vault/", ctx)
        self.assertIn("read before assuming", ctx)

    def test_title_is_shown_without_repeating_id_and_severity(self):
        """The heading is `# RES-01 — totals ignore tax [HIGH]`; the bullet
        already prints the id and severity, so the title must be just the
        words in between."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            r = run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py")))
        ctx = context_of(r)["additionalContext"]
        self.assertIn("totals ignore tax", ctx)
        self.assertNotIn("RES-01 — totals", ctx)
        self.assertNotIn("[HIGH]", ctx)

    def test_relative_file_path_matches_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            r = run_consult(proj, payload_for("src/app.py"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RES-01", context_of(r)["additionalContext"])

    def test_consult_still_marks_the_vault_stale(self):
        """One process serves both duties; consulting must not lose the mark."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            r = run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py")))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(
                os.path.join(proj, ".tracelink", ".stale")))


class TestConsultStaysSilent(unittest.TestCase):
    """Every one of these must be: empty stdout, exit 0, no traceback."""

    def assert_silent(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("Traceback", r.stderr)

    def test_file_without_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            other = os.path.join(proj, "src", "other.py")
            open(other, "w").close()
            self.assert_silent(run_consult(proj, payload_for(other)))

    def test_config_absent_means_off(self):
        """Opt-in: a project that never wrote config.json gets nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE, config=None)
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_config_consult_false_means_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE, config={"consult": False})
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_config_unparseable_means_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE, config="{not json at all")
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_link_state_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            os.remove(os.path.join(proj, ".tracelink", "vault", STATE_FILE))
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_link_state_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            with open(os.path.join(proj, ".tracelink", "vault",
                                   STATE_FILE), "w") as fh:
                fh.write("{{{{ definitely not json")
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_link_state_wrong_schema_version(self):
        """A v1 (or future) state has a shape this code never audited —
        silence beats guessing."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            path = os.path.join(proj, ".tracelink", "vault", STATE_FILE)
            with open(path) as fh:
                state = json.load(fh)
            state["schema_version"] = 1
            with open(path, "w") as fh:
                json.dump(state, fh)
            self.assert_silent(run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py"))))

    def test_stdin_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            self.assert_silent(run_consult(proj, "this is not json"))

    def test_stdin_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            self.assert_silent(run_consult(proj, ""))

    def test_payload_without_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            self.assert_silent(run_consult(
                proj, '{"tool_name": "Edit", "tool_input": {}}'))


FIVE_PLUS_TWO = [
    ("RES-01", "closed", "low", "one",
     [("a", "src/app.py", 1)]),
    ("RES-02", "open", "high", "two",
     [("b", "src/app.py", 2)]),
    ("RES-03", "open", "critical", "three",
     [("c", "src/app.py", 3)]),
    ("RES-04", "open", "medium", "four",
     [("d", "src/app.py", 4)]),
    ("RES-05", "closed", "critical", "five",
     [("e", "src/app.py", 5)]),
    ("RES-06", "open", "low", "six",
     [("f", "src/app.py", 6)]),
    ("RES-07", "closed", "medium", "seven",
     [("g", "src/app.py", 7)]),
]


class TestOrderingAndCap(unittest.TestCase):
    def consult(self, notes):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, notes)
            r = run_consult(proj, payload_for(
                os.path.join(proj, "src", "app.py")))
        self.assertEqual(r.returncode, 0, r.stderr)
        return context_of(r)["additionalContext"]

    def test_open_before_closed_whatever_the_severity(self):
        ctx = self.consult([
            ("RES-01", "closed", "critical", "old news",
             [("a", "src/app.py", 1)]),
            ("RES-02", "open", "low", "still live",
             [("b", "src/app.py", 2)]),
        ])
        self.assertLess(ctx.index("RES-02"), ctx.index("RES-01"))

    def test_severity_orders_within_open(self):
        ctx = self.consult([
            ("RES-01", "open", "low", "one", [("a", "src/app.py", 1)]),
            ("RES-02", "open", "critical", "two", [("b", "src/app.py", 2)]),
            ("RES-03", "open", "high", "three", [("c", "src/app.py", 3)]),
            ("RES-04", "open", "medium", "four", [("d", "src/app.py", 4)]),
        ])
        order = [ctx.index(i) for i in ("RES-02", "RES-03", "RES-04",
                                        "RES-01")]
        self.assertEqual(order, sorted(order))

    def test_more_symbols_wins_the_tie(self):
        ctx = self.consult([
            ("RES-01", "open", "high", "one symbol",
             [("a", "src/app.py", 1)]),
            ("RES-02", "open", "high", "two symbols",
             [("b", "src/app.py", 2), ("c", "src/app.py", 3)]),
        ])
        self.assertLess(ctx.index("RES-02"), ctx.index("RES-01"))

    def test_cap_at_five_with_a_count_of_the_rest(self):
        ctx = self.consult(FIVE_PLUS_TWO)
        bullets = [l for l in ctx.splitlines() if l.startswith("- ")]
        self.assertEqual(len(bullets), 5)
        self.assertIn("…and 2 more in CODE-INDEX.md", ctx)
        # the two dropped are the closed low/medium tail, not the open ones
        self.assertNotIn("RES-01", ctx)
        self.assertNotIn("RES-07", ctx)


class TestBudget(unittest.TestCase):
    """The path everyone pays for — an edit to a file without notes — must
    not open a single note. Tested at the cause (which files get opened),
    not the wall clock."""

    def test_no_note_file_opened_when_file_has_no_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            other = os.path.join(proj, "src", "other.py")
            open(other, "w").close()
            real_open, opened = open, []

            def spy(path, *args, **kwargs):
                opened.append(str(path))
                return real_open(path, *args, **kwargs)

            with mock.patch("builtins.open", side_effect=spy):
                out = plugin_refresh.consult(proj, payload_for(other))
            self.assertEqual(out, "")
            md = [p for p in opened if p.endswith(".md")]
            self.assertEqual(md, [], "consult opened notes for a file "
                                     "the link-state says is clean")

    def test_consult_off_reads_only_the_config(self):
        """With the gate closed even the link-state stays untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE, config={"consult": False})
            real_open, opened = open, []

            def spy(path, *args, **kwargs):
                opened.append(str(path))
                return real_open(path, *args, **kwargs)

            with mock.patch("builtins.open", side_effect=spy):
                out = plugin_refresh.consult(
                    proj, payload_for(os.path.join(proj, "src", "app.py")))
            self.assertEqual(out, "")
            self.assertEqual([p for p in opened
                              if not p.endswith("config.json")], [])


class TestWrapperForwardsStdin(unittest.TestCase):
    """on-edit.sh used to drain stdin; now the payload must reach Python."""

    def test_on_edit_emits_context_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run(
                [os.path.join(HOOKS, "on-edit.sh")],
                input=payload_for(os.path.join(proj, "src", "app.py")),
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("RES-01", context_of(r)["additionalContext"])
            self.assertTrue(os.path.exists(
                os.path.join(proj, ".tracelink", ".stale")))

    def test_on_edit_without_opt_in_is_silent_and_still_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, ONE_NOTE, config=None)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run(
                [os.path.join(HOOKS, "on-edit.sh")],
                input=payload_for(os.path.join(proj, "src", "app.py")),
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertTrue(os.path.exists(
                os.path.join(proj, ".tracelink", ".stale")))


if __name__ == "__main__":
    unittest.main()
