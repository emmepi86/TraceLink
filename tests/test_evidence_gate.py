"""ms-13b: a bare word in prose is a candidate, never a link on its own.

Benchmark 04 measured three anchors nobody had written. One came from a
pytest fixture whose name is an ordinary domain word; the other from a
sentence naming a class the way people name classes in a sentence — without
backticks — while backticking the symbol the finding was actually about.

Both were *unique* in the repository, and uniqueness is what the resolver
had been treating as sufficient. It is not the same evidence:

    uniqueness   settles WHICH definition a reference means
    the author   settles THAT a reference was meant

So the rule is narrow. A bare identifier still becomes a candidate, still
counts toward an ambiguity, and still cannot produce a match by itself. What
counts as the author having written a reference is what the resolver already
knew how to recognise — backticks, a qualified name, a cited path, an
explicit override — and nothing semantic: no language, no negation, no
intent is read here.

The measurement that made this safe: on the 50-finding gold set, all 56
useful anchors already carried explicit evidence, so the gate costs nothing
in recall on real findings and removes exactly the class of link nobody
asserted.
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

from tracelink import consult as consult_mod  # noqa: E402
from tracelink import linker, splitter, symbol_index  # noqa: E402

ONE = {"src/app.py": "def only_here(value):\n    return value\n\n\n"
                     "class TenancyMiddleware:\n    pass\n"}
TWO = {"src/a.py": "class FieldType:\n    pass\n",
       "src/b.py": "class FieldType:\n    pass\n"}


def run(tmp, body, code=ONE):
    root = os.path.join(tmp, "proj")
    for rel, text in code.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    register = os.path.join(root, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write(f"# Findings\n\n## E-01 — a finding [MEDIUM]\n"
                 f"### STATUS: OPEN\n\n{body}\n")
    vault = os.path.join(root, "vault")
    symbols = os.path.join(root, "symbols.json")
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        symbol_index.main(["--repo", root, "--backend", "scan",
                           "--out", symbols])
        splitter.main(["--register", register, "--out", vault,
                       "--prefix", "E"])
        linker.main(["--vault", vault, "--symbols", symbols, "--repo", root])
    with open(os.path.join(vault, consult_mod.STATE_FILE)) as fh:
        return json.load(fh)["notes"]["E-01.md"]


class ProseAloneDoesNotLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_pytest_fixture_case(self):
        """'the landing page' must not link to a symbol called landing."""
        entry = run(self.tmp.name,
                    "The report covers the only_here path end to end.")
        self.assertEqual([], entry["linked"])
        self.assertEqual([], entry["ambiguous"])

    def test_the_ordinary_sentence_case(self):
        """A class named the way a sentence names a class."""
        entry = run(self.tmp.name,
                    "A Celery task does not go through TenancyMiddleware, so "
                    "the schema stays on public.")
        self.assertEqual([], entry["linked"])

    def test_backticks_still_link(self):
        entry = run(self.tmp.name,
                    "`TenancyMiddleware` is the only place that pins a tenant.")
        self.assertEqual(["TenancyMiddleware"], entry["linked"])
        self.assertEqual("unique", entry["provenance"][0]["reason"])

    def test_a_qualified_name_in_prose_still_links(self):
        """Explicit is explicit, backticks or not."""
        entry = run(self.tmp.name,
                    "The pinning happens in app.TenancyMiddleware and "
                    "nowhere else.")
        self.assertEqual(["TenancyMiddleware"], entry["linked"])

    def test_a_cited_path_still_links(self):
        entry = run(self.tmp.name,
                    "The only_here helper in src/app.py returns its input.")
        self.assertEqual(["only_here"], entry["linked"])
        self.assertEqual("path-in-note", entry["provenance"][0]["reason"])

    def test_the_gate_is_per_reference_not_per_finding(self):
        """One sentence, two names: one written as code, one not."""
        entry = run(self.tmp.name,
                    "`only_here` is called by TenancyMiddleware during boot.")
        self.assertEqual(["only_here"], entry["linked"])


class AmbiguityIsNotAnAssertion(unittest.TestCase):
    """Refusing to choose is not a claim, so the gate leaves it alone."""

    def test_a_bare_word_with_two_definitions_is_still_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = run(tmp, "The FieldType depends on who uses it.",
                        code=TWO)
            self.assertEqual([], entry["linked"])
            self.assertEqual("FieldType", entry["ambiguous"][0]["name"])
            self.assertEqual(2, len(entry["ambiguous"][0]["candidates"]))

    def test_backticked_and_ambiguous_is_still_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = run(tmp, "`FieldType` is defined twice.", code=TWO)
            self.assertEqual([], entry["linked"])
            self.assertTrue(entry["ambiguous"])


class TheRefusalIsDiagnosable(unittest.TestCase):
    def test_explain_mode_says_why_nothing_was_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "proj")
            os.makedirs(os.path.join(root, "src"))
            with open(os.path.join(root, "src", "app.py"), "w") as fh:
                fh.write(ONE["src/app.py"])
            register = os.path.join(root, "FINDINGS.md")
            with open(register, "w") as fh:
                fh.write("# Findings\n\n## E-01 — a finding [MEDIUM]\n"
                         "### STATUS: OPEN\n\nThe only_here path is fine.\n")
            vault = os.path.join(root, "vault")
            symbols = os.path.join(root, "symbols.json")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer), \
                    contextlib.redirect_stderr(io.StringIO()):
                symbol_index.main(["--repo", root, "--backend", "scan",
                                   "--out", symbols])
                splitter.main(["--register", register, "--out", vault,
                               "--prefix", "E"])
                linker.main(["--vault", vault, "--symbols", symbols,
                             "--repo", root, "--explain"])
            self.assertIn("named only as prose, not as code",
                          buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
