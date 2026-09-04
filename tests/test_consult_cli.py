"""ms-4: `tracelink consult` — the first public primitive.

The lookup ms-2 turned into a library call now has a command, and with it a
JSON document other programs will parse. That makes three things contracts
rather than implementation details, and each one is tested here:

  * **the exit codes** — 0 answered, 2 unusable arguments, 3 no such file,
    4 the name means two things, 5 no readable link state;
  * **the JSON document** — `schema_version: 1`, deliberately small, and
    never a raw internal reason code: `PUBLIC_ERROR` maps the vault's own
    vocabulary onto the published one, and a test keeps that mapping total;
  * **stdout** — with `--json`, stdout is the document and nothing else,
    including when the answer is an error. Humans read stderr.

The rule for reading a bare target is deterministic and is tested in both
directions: a target that names something on disk is a file, everything else
is a symbol, and `--file` / `--symbol` overrule the rule rather than a
heuristic guessing better.

One judgement is worth spelling out. A symbol nothing links exits 0 with no
hits, not 3: the link state knows which symbols have *findings*, not which
symbols *exist*, so "not found" would assert something this command cannot
know. A file that is not on disk is different — that, it can check.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import cli, consult as consult_mod  # noqa: E402
from tracelink.consult import (EXIT_AMBIGUOUS, EXIT_NOT_FOUND,  # noqa: E402
                               EXIT_NO_STATE, EXIT_OK, EXIT_USAGE,
                               PUBLIC_ERROR)

sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_consult_api import make_project  # noqa: E402

#: Notes whose symbols exercise exact, suffix and ambiguous resolution.
SYMBOLS = [
    ("RES-01", "open", "high", "totals ignore tax",
     [("payments.validate", "src/payments.py", 88)], []),
    ("RES-02", "open", "low", "retries",
     [("payments.retry", "src/payments.py", 120)], []),
    ("RES-03", "open", "medium", "refunds validate too",
     [("refunds.validate", "src/refunds.py", 12)], []),
    ("RES-04", "closed", "low", "an unlinked file",
     [], ["src/payments.py"]),
]


def project(tmp, notes=SYMBOLS, **kwargs):
    """A project whose fixture paths also exist on disk — `consult` reads a
    bare target as a file when the filesystem says so, and a test that never
    creates the files would be testing a different rule."""
    proj = make_project(tmp, notes=notes, **kwargs)
    for _id, _st, _sev, _title, symbols, files in notes:
        for path in [s[1] for s in symbols] + list(files):
            full = os.path.join(proj, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if not os.path.exists(full):
                with open(full, "w") as fh:
                    fh.write("# fixture\n")
    return proj


def run(*argv):
    """`tracelink consult ...` in-process: (exit, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["consult"] + list(argv))
        except SystemExit as exc:  # argparse
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class TargetsResolveDeterministically(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = project(self.tmp.name)

    def test_a_path_is_read_as_a_file(self):
        code, out, _ = run("src/payments.py", "--repo", self.proj)
        self.assertEqual(EXIT_OK, code)
        self.assertIn("known findings about this file", out)

    def test_an_exact_symbol_name_wins_outright(self):
        code, out, _ = run("payments.validate", "--repo", self.proj)
        self.assertEqual(EXIT_OK, code)
        self.assertIn("known findings about this symbol "
                      "(payments.validate)", out)
        self.assertIn("src/payments.py:L88", out)

    def test_a_tail_resolves_when_it_is_unambiguous(self):
        code, out, _ = run("retry", "--repo", self.proj)
        self.assertEqual(EXIT_OK, code)
        self.assertIn("(payments.retry)", out)

    def test_a_tail_that_means_two_things_is_never_chosen_for_you(self):
        code, out, err = run("validate", "--repo", self.proj)
        self.assertEqual(EXIT_AMBIGUOUS, code)
        self.assertEqual("", out)
        self.assertIn("payments.validate", err)
        self.assertIn("refunds.validate", err)

    def test_a_file_that_does_not_exist_is_not_found(self):
        code, _, err = run("src/gone.py", "--repo", self.proj)
        self.assertEqual(EXIT_NOT_FOUND, code)
        self.assertIn("no such file", err)

    def test_a_symbol_nothing_links_is_an_answer_not_an_error(self):
        """The state knows which symbols have findings, not which exist."""
        code, out, err = run("unheard_of", "--repo", self.proj)
        self.assertEqual(EXIT_OK, code)
        self.assertEqual("", out)
        self.assertIn("nothing recorded", err)

    def test_file_flag_forces_a_path_reading(self):
        code, _, err = run("payments.validate", "--file", "--repo", self.proj)
        self.assertEqual(EXIT_NOT_FOUND, code)
        self.assertIn("no such file", err)

    def test_symbol_flag_forces_a_symbol_reading(self):
        """`src/app.py` exists, so the rule says file; --symbol overrules."""
        code, out, err = run("src/app.py", "--symbol", "--repo", self.proj)
        self.assertEqual(EXIT_OK, code)
        self.assertEqual("", out)
        self.assertIn("nothing recorded", err)

    def test_the_same_question_gives_the_same_bytes(self):
        first = run("payments.validate", "--repo", self.proj, "--json")
        second = run("payments.validate", "--repo", self.proj, "--json")
        self.assertEqual(first, second)


class TheJsonDocumentIsAContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = project(self.tmp.name)

    def doc(self, *argv):
        code, out, _ = run(*(argv + ("--repo", self.proj, "--json")))
        return code, json.loads(out)

    def test_schema_version_and_target(self):
        code, doc = self.doc("payments.validate")
        self.assertEqual(EXIT_OK, code)
        self.assertEqual(1, doc["schema_version"])
        self.assertEqual({"input": "payments.validate", "kind": "symbol",
                          "resolved": "payments.validate"}, doc["target"])

    def test_a_hit_carries_the_finding_and_its_anchors(self):
        _, doc = self.doc("payments.validate")
        self.assertEqual(1, len(doc["hits"]))
        hit = doc["hits"][0]
        self.assertEqual("RES-01", hit["finding_id"])
        self.assertEqual("open", hit["status"])
        self.assertEqual("high", hit["severity"])
        self.assertEqual("totals ignore tax", hit["title"])
        anchor = hit["anchors"][0]
        # schema 1 fields, unchanged
        self.assertEqual("symbol", anchor["kind"])
        self.assertEqual("payments.validate", anchor["name"])
        self.assertEqual("src/payments.py", anchor["path"])
        self.assertEqual(88, anchor["line"])
        # ms-5 added provenance to the same object: additive, still schema 1
        self.assertEqual("match", anchor["state"])
        self.assertIn(anchor["method"], (None,) + tuple(
            consult_mod.PUBLIC_METHODS))

    def test_a_file_target_reports_both_kinds_of_anchor(self):
        _, doc = self.doc("src/payments.py")
        kinds = {a["kind"] for hit in doc["hits"] for a in hit["anchors"]}
        self.assertEqual({"symbol", "file"}, kinds)

    def test_nothing_written_is_an_empty_hit_list_not_an_error(self):
        code, doc = self.doc("unheard_of")
        self.assertEqual(EXIT_OK, code)
        self.assertEqual([], doc["hits"])
        self.assertNotIn("error", doc)

    def test_ambiguity_names_the_candidates(self):
        code, doc = self.doc("validate")
        self.assertEqual(EXIT_AMBIGUOUS, code)
        self.assertEqual("ambiguous_target", doc["error"]["code"])
        self.assertEqual(["payments.validate", "refunds.validate"],
                         doc["error"]["candidates"])

    def test_a_missing_state_says_so_in_the_document_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, state="absent")
            code, out, err = run("src/payments.py", "--repo", proj, "--json")
            self.assertEqual(EXIT_NO_STATE, code)
            self.assertEqual("no_state", json.loads(out)["error"]["code"])
            self.assertIn("tracelink link", err)

    def test_a_state_from_another_version_is_its_own_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, schema=99)
            code, out, _ = run("src/payments.py", "--repo", proj, "--json")
            self.assertEqual(EXIT_NO_STATE, code)
            self.assertEqual("state_schema_unsupported",
                             json.loads(out)["error"]["code"])

    def test_internal_reasons_never_reach_the_document(self):
        """Every silence the core can produce has a published name."""
        import ast
        with open(os.path.join(SRC, "tracelink", "consult.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        produced = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.keyword) and node.arg == "silence"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                produced.add(node.value.value)
        self.assertTrue(produced)
        self.assertEqual(set(), produced - set(PUBLIC_ERROR),
                         "an internal reason with no published name")


class StdoutBelongsToTheMachine(unittest.TestCase):
    """A real process, because in-process capture cannot prove this."""

    def test_json_stdout_parses_whole_even_when_the_answer_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, notes=SYMBOLS)
            env = dict(os.environ, PYTHONPATH=SRC)
            run_ = subprocess.run(
                [sys.executable, "-m", "tracelink.cli", "consult",
                 "validate", "--repo", proj, "--json"],
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(EXIT_AMBIGUOUS, run_.returncode)
            doc = json.loads(run_.stdout)  # the whole of stdout, or it fails
            self.assertEqual("ambiguous_target", doc["error"]["code"])
            self.assertIn("could mean", run_.stderr)

    def test_text_mode_keeps_the_briefing_on_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, notes=SYMBOLS)
            env = dict(os.environ, PYTHONPATH=SRC)
            run_ = subprocess.run(
                [sys.executable, "-m", "tracelink.cli", "consult",
                 "payments.validate", "--repo", proj],
                capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(EXIT_OK, run_.returncode)
            self.assertIn("RES-01", run_.stdout)
            self.assertEqual("", run_.stderr)


class TheGateBelongsToThePlugin(unittest.TestCase):
    """`consult: false` silences the hook. It must not silence the command:
    the opt-in exists so the vault does not speak inside somebody's turn
    uninvited, which is not a reason to refuse an answer to a question."""

    def test_the_cli_answers_with_the_gate_shut(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, config={"consult": False})
            code, out, _ = run("payments.validate", "--repo", proj)
            self.assertEqual(EXIT_OK, code)
            self.assertIn("RES-01", out)

    def test_the_cli_answers_with_no_config_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, config=None)
            code, out, _ = run("payments.validate", "--repo", proj)
            self.assertEqual(EXIT_OK, code)
            self.assertIn("RES-01", out)

    def test_the_hook_stays_shut(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import plugin_refresh
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, config={"consult": False})
            payload = json.dumps({"tool_name": "Edit", "tool_input": {
                "file_path": os.path.join(proj, "src", "payments.py")}})
            self.assertEqual("", plugin_refresh.consult(proj, payload))


class UsageErrorsAreExitTwo(unittest.TestCase):
    def test_no_target(self):
        code, _, _ = run()
        self.assertEqual(EXIT_USAGE, code)

    def test_unknown_flag(self):
        code, _, _ = run("x", "--nope")
        self.assertEqual(EXIT_USAGE, code)


if __name__ == "__main__":
    unittest.main()
