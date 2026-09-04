"""F9: a silent answer must not read as a clean bill of health.

`nothing recorded about alembic/env.py` can mean two very different things:

    this area was examined and carries no constraints
    nobody has ever written anything about this area

TraceLink cannot tell them apart — and until now it said the same words for
both, which lets a reader assume the first. The agent benchmark walked into
exactly this: three runs consulted thirteen times about migrations, got
`nothing recorded` every time, and had no way to know the vault had never
covered that subtree at all.

So a silent answer now reports where memory exists. Deliberately as counts
and never as a percentage: TraceLink does not know how much memory a
repository *should* have, so a coverage score would be a number about
nothing.

Two boundaries this keeps: the work happens only when there is nothing to
say, over a state already parsed, so answering pays nothing for it; and the
edit hook stays silent, because volunteering "I know nothing about this
file" after every edit is noise, and not speaking uninvited is the hook's
whole contract.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tracelink import cli, consult as consult_mod  # noqa: E402
from tracelink import linker, splitter, symbol_index  # noqa: E402

CODE = {"app/core/thing.py": "def pinned(v):\n    return v\n",
        "app/services/other.py": "def helper(v):\n    return v\n",
        "alembic/env.py": "def run_migrations():\n    return None\n"}

REGISTER = ("# Findings\n\n## C-01 — a finding [HIGH]\n### STATUS: OPEN\n\n"
            "`pinned` must stay explicit.\n\n"
            "## C-02 — another [LOW]\n### STATUS: OPEN\n\n"
            "`helper` is fine.\n")


def project(tmp):
    root = os.path.join(tmp, "proj")
    for rel, text in CODE.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    register = os.path.join(root, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write(REGISTER)
    vault = os.path.join(root, ".tracelink", "vault")
    symbols = os.path.join(root, ".tracelink", "symbols.json")
    os.makedirs(os.path.dirname(symbols), exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        symbol_index.main(["--repo", root, "--backend", "scan",
                           "--out", symbols])
        splitter.main(["--register", register, "--out", vault,
                       "--prefix", "C"])
        linker.main(["--vault", vault, "--symbols", symbols, "--repo", root])
    return root, vault


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["consult"] + list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class SilenceExplainsItself(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root, self.vault = project(self.tmp.name)

    def test_an_uncovered_area_says_it_is_uncovered(self):
        code, _out, err = run("alembic/env.py", "--repo", self.root,
                              "--vault", self.vault)
        self.assertEqual(0, code)
        self.assertIn("no recorded anchors", err)
        self.assertIn("alembic/", err)
        self.assertIn("cannot conclude that no constraints exist", err)

    def test_it_shows_where_memory_does_exist(self):
        _c, _out, err = run("alembic/env.py", "--repo", self.root,
                            "--vault", self.vault)
        self.assertIn("app/core/", err)
        self.assertIn("app/services/", err)

    def test_a_covered_answer_says_none_of_this(self):
        code, out, err = run("app/core/thing.py", "--repo", self.root,
                             "--vault", self.vault)
        self.assertEqual(0, code)
        self.assertIn("C-01", out)
        self.assertNotIn("cannot conclude", err)

    def test_there_is_no_percentage_anywhere(self):
        """A score would be a number about nothing: TraceLink does not know
        how much memory a repository should have."""
        _c, _out, err = run("alembic/env.py", "--repo", self.root,
                            "--vault", self.vault)
        self.assertNotIn("%", err)

    def test_the_json_document_carries_the_same_facts(self):
        _c, out, _err = run("alembic/env.py", "--repo", self.root,
                            "--vault", self.vault, "--json")
        doc = json.loads(out)
        self.assertEqual(1, doc["schema_version"])
        self.assertEqual([], doc["hits"])
        self.assertEqual(0, doc["coverage"]["anchors_here"])
        self.assertEqual("alembic", doc["coverage"]["target_directory"])
        self.assertIn("app/core", doc["coverage"]["by_directory"])

    def test_coverage_is_absent_when_there_is_something_to_say(self):
        _c, out, _err = run("app/core/thing.py", "--repo", self.root,
                            "--vault", self.vault, "--json")
        self.assertNotIn("coverage", json.loads(out))


class TheHookStaysSilent(unittest.TestCase):
    def test_an_uncovered_file_produces_no_additional_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _vault = project(tmp)
            with open(os.path.join(root, ".tracelink", "config.json"),
                      "w") as fh:
                json.dump({"consult": True}, fh)
            import plugin_refresh
            payload = json.dumps({"tool_name": "Edit", "tool_input": {
                "file_path": os.path.join(root, "alembic", "env.py")}})
            self.assertEqual("", plugin_refresh.consult(root, payload),
                             "the hook spoke to say it knew nothing")

    def test_render_text_is_still_empty_for_a_silent_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, vault = project(tmp)
            result = consult_mod.consult(root, "alembic/env.py", vault=vault)
            self.assertEqual("", consult_mod.render_text(result))
            self.assertTrue(consult_mod.render_coverage(result))


if __name__ == "__main__":
    unittest.main()
