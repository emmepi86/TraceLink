"""ms-5: every link explains itself, and the explanation is a contract.

The resolver has always known why it linked something — `disambiguate`
returns a reason, and `link --explain` printed it. What it did not do was
*record* it, so the knowledge died with the run and nothing downstream could
answer "why is RES-17 pointing at line 88?".

Now the reason and the evidence behind it are written into the link state
(schema 4) and published through two vocabularies that are deliberately not
the resolver's own:

    internal reason  ──total mapping──▶  public state + method

Two directions are tested, because each catches a different rot:

  * **completeness** — every reason `disambiguate` can return is mapped, so
    a new branch cannot leak an unnamed code into a published document;
  * **closure** — every published state, method and basis kind is reached by
    a real linker run over a real fixture, so the vocabulary cannot outlive
    the resolver that was supposed to produce it.

The fixtures below run the actual pipeline (index → split → link) in
process, which is possible at all because ms-1 gave every entry point its
own argv.
"""

import ast
import io
import contextlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import consult as consult_mod, explain as explain_mod  # noqa
from tracelink import linker, splitter, symbol_index  # noqa: E402
from tracelink.consult import (BASIS_KINDS, PUBLIC_METHODS,  # noqa: E402
                               PUBLIC_STATES, RESOLUTION, STATE_FILE)

TWO_FILES = {
    "src/payments.py": "def validate(x):\n    return x\n",
    "src/refunds.py": "def validate(y):\n    return y\n",
}
ONE_FILE = {"src/only.py": "def only_here(x):\n    return x\n"}


def build(tmp, files, register_body, symbols_patch=None, note_patch=None):
    """A real repository + register, taken through the real pipeline."""
    proj = os.path.join(tmp, "proj")
    for rel, text in files.items():
        path = os.path.join(proj, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    register = os.path.join(proj, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write(register_body)
    vault = os.path.join(proj, ".tracelink", "vault")
    symbols = os.path.join(proj, ".tracelink", "symbols.json")
    os.makedirs(os.path.join(proj, ".tracelink"), exist_ok=True)

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        assert symbol_index.main(["--repo", proj, "--backend", "scan",
                                  "--out", symbols]) == 0
        if symbols_patch:
            with open(symbols) as fh:
                index = json.load(fh)
            index["symbols"] = symbols_patch
            with open(symbols, "w") as fh:
                json.dump(index, fh)
        assert splitter.main(["--register", register, "--out", vault,
                              "--prefix", "RES"]) == 0
        if note_patch:
            for name, patch in note_patch.items():
                path = os.path.join(vault, name)
                with open(path) as fh:
                    text = fh.read()
                with open(path, "w") as fh:
                    fh.write(patch(text))
        linker.main(["--vault", vault, "--symbols", symbols, "--repo", proj])
    with open(os.path.join(vault, STATE_FILE)) as fh:
        return proj, vault, json.load(fh)


def finding(body, note_id="RES-01", severity="HIGH"):
    return f"# Findings\n\n## {note_id} — a finding [{severity}]\n{body}\n"


def reasons_of(state, note="RES-01.md"):
    return [r["reason"] for r in state["notes"][note]["provenance"]]


def basis_kinds_of(state, note="RES-01.md"):
    return [b[0] for r in state["notes"][note]["provenance"] for b in r["basis"]]


class TheMappingIsComplete(unittest.TestCase):
    """Every reason the resolver can return has a published name."""

    def test_every_reason_disambiguate_returns_is_mapped(self):
        with open(os.path.join(SRC, "tracelink", "linker.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        func = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "disambiguate")
        returned = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Return) and isinstance(node.value,
                                                           ast.Tuple):
                # (location, reason, basis) — the reason is the second slot
                second = node.value.elts[1]
                if isinstance(second, ast.Constant) and isinstance(
                        second.value, str):
                    returned.add(second.value)
        self.assertTrue(returned, "no reasons found — did the shape change?")
        self.assertEqual(set(), returned - set(RESOLUTION),
                         "an internal reason with no published mapping")

    def test_the_published_states_are_exactly_the_declared_ones(self):
        self.assertEqual(set(PUBLIC_STATES),
                         {state for state, _m in RESOLUTION.values()})

    def test_a_method_is_published_only_where_something_was_asserted(self):
        for reason, (state, method) in RESOLUTION.items():
            with self.subTest(reason=reason):
                if state == "match":
                    self.assertIn(method, PUBLIC_METHODS)
                else:
                    self.assertIsNone(method, "a refusal has no method")


class TheVocabularyIsReached(unittest.TestCase):
    """Closure: a published word no run produces is a lie waiting to happen.

    Each fixture is a real repository the real resolver walks; the methods
    and basis kinds they collectively produce must cover everything
    `consult` declares.
    """

    seen_methods = set()
    seen_states = set()
    seen_basis = set()

    def record(self, state, note="RES-01.md"):
        for reason in reasons_of(state, note):
            public_state, method = RESOLUTION[reason]
            TheVocabularyIsReached.seen_states.add(public_state)
            if method:
                TheVocabularyIsReached.seen_methods.add(method)
        for item in state["notes"][note].get("ambiguous", []):
            TheVocabularyIsReached.seen_states.add(
                RESOLUTION[item[1]][0])
        TheVocabularyIsReached.seen_basis.update(basis_kinds_of(state, note))

    def test_sole_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, ONE_FILE,
                                  finding("`only_here` returns unvalidated "
                                          "input."))
            self.assertEqual(["unique"], reasons_of(state))
            self.record(state)

    def test_qualified_symbol_when_the_note_quotes_the_qualified_name(self):
        """The scan backend's qualified name for `validate` in payments.py
        IS `payments.validate`, so a note writing that quotes it literally
        and the qualified-name branch decides."""
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`payments.validate` accepts an "
                                          "empty body."))
            self.assertEqual(["qualified-name"], reasons_of(state))
            self.assertEqual("qualified_symbol",
                             RESOLUTION["qualified-name"][1])
            self.record(state)

    def test_qualified_symbol_through_a_dotted_prefix(self):
        """When the index spells the qualified name more fully than the
        note does — `app.payments.validate` against a note that writes
        `payments.validate` — nothing is quoted literally and the dotted
        branch resolves it by matching segments."""
        patch = {"validate": [
            {"path": "src/payments.py", "line": 1, "kind": "py",
             "qualified_name": "app.payments.validate"},
            {"path": "src/refunds.py", "line": 1, "kind": "py",
             "qualified_name": "app.refunds.validate"}]}
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`payments.validate` is wrong."),
                                  symbols_patch=patch)
            self.assertEqual(["dotted-name"], reasons_of(state))
            self.assertIn("dotted_reference", basis_kinds_of(state))
            self.record(state)

    def test_qualified_symbol_through_a_scoped_name_the_note_quotes(self):
        """A ctags-shaped index: `Payments::validate` is a qualified name
        that is not a dotted one, so the note quoting it takes the
        qualified-name branch rather than the dotted one."""
        patch = {"validate": [
            {"path": "src/payments.py", "line": 1, "kind": "py",
             "qualified_name": "Payments::validate"},
            {"path": "src/refunds.py", "line": 1, "kind": "py",
             "qualified_name": "Refunds::validate"}]}
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`validate` — see "
                                          "`Payments::validate`."),
                                  symbols_patch=patch)
            self.assertEqual(["qualified-name"], reasons_of(state))
            self.assertIn("qualified_name_in_note", basis_kinds_of(state))
            self.record(state)

    def test_path_in_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`validate` in `src/payments.py` "
                                          "accepts an empty body."))
            self.assertEqual(["path-in-note"], reasons_of(state))
            self.record(state)

    def test_path_suffix_match_when_the_index_has_no_qualified_names(self):
        patch = {"validate": [{"path": "src/payments.py", "line": 1,
                               "kind": "py", "qualified_name": ""},
                              {"path": "src/refunds.py", "line": 1,
                               "kind": "py", "qualified_name": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`payments.validate` is wrong."),
                                  symbols_patch=patch)
            self.assertEqual(["dotted-path"], reasons_of(state))
            self.assertIn("path_suffix_match", basis_kinds_of(state))
            self.record(state)

    def test_explicit_override(self):
        def pin(text):
            return text.replace("tracelink_schema: 1",
                                "tracelink_schema: 1\ntracelink:\n"
                                "  validate: src/payments.py", 1)
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`validate` is wrong."),
                                  note_patch={"RES-01.md": pin})
            self.assertEqual(["frontmatter-override"], reasons_of(state))
            self.assertIn("frontmatter_override", basis_kinds_of(state))
            self.record(state)

    def test_ambiguity_is_recorded_with_its_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(tmp, TWO_FILES,
                                  finding("`validate` is wrong."))
            entry = state["notes"]["RES-01.md"]
            self.assertEqual([], entry["provenance"])
            self.assertEqual("validate", entry["ambiguous"][0][0])
            self.assertEqual("ambiguous", entry["ambiguous"][0][1])
            self.assertEqual(2, len(entry["ambiguous"][0][2]))
            self.record(state)

    def test_conflicting_evidence_is_a_conflict_not_a_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            _p, _v, state = build(
                tmp, TWO_FILES,
                finding("`payments.validate` is wrong, see "
                        "`src/refunds.py`."))
            entry = state["notes"]["RES-01.md"]
            self.assertEqual("qualified-name-and-path-disagree",
                             entry["ambiguous"][0][1])
            self.assertEqual("conflict", RESOLUTION[entry["ambiguous"][0][1]][0])
            self.record(state)

    def test_zz_everything_declared_was_produced(self):
        """Runs last (alphabetical): the union of what the fixtures saw."""
        self.assertEqual(set(PUBLIC_METHODS), self.seen_methods,
                         "a published method no fixture produces")
        self.assertEqual(set(PUBLIC_STATES), self.seen_states,
                         "a published state no fixture produces")
        self.assertEqual(set(BASIS_KINDS), self.seen_basis,
                         "a published basis kind no fixture produces")


class ExplainAnswersTheFourQuestions(unittest.TestCase):
    def explain(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                from tracelink import cli
                code = cli.main(["explain"] + list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def test_a_match_says_where_and_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, vault, _s = build(tmp, TWO_FILES,
                                    finding("`payments.validate` is wrong."))
            code, out, _ = self.explain("RES-01", "--repo", proj)
            self.assertEqual(0, code)
            self.assertIn("MATCH", out)
            self.assertIn("src/payments.py", out)
            self.assertIn("Method: qualified_symbol", out)
            self.assertIn("the finding names the qualified symbol "
                          "`payments.validate`", out)

    def test_an_ambiguity_says_no_link_was_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, TWO_FILES, finding("`validate` fails."))
            code, out, _ = self.explain("RES-01", "--repo", proj)
            self.assertEqual(0, code)
            self.assertIn("AMBIGUOUS", out)
            self.assertIn("src/payments.py", out)
            self.assertIn("src/refunds.py", out)
            self.assertIn("No link was asserted.", out)

    def test_a_finding_with_no_anchor_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, ONE_FILE,
                                 finding("Nothing here names any code."))
            code, out, _ = self.explain("RES-01", "--repo", proj)
            self.assertEqual(0, code)
            self.assertIn("NO ANCHOR", out)

    def test_an_unknown_finding_is_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, ONE_FILE, finding("`only_here` fails."))
            code, _out, err = self.explain("RES-99", "--repo", proj)
            self.assertEqual(consult_mod.EXIT_NOT_FOUND, code)
            self.assertIn("no finding RES-99", err)

    def test_no_state_is_exit_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self.explain("RES-01", "--repo", tmp)
            self.assertEqual(consult_mod.EXIT_NO_STATE, code)
            self.assertIn("tracelink link", err)

    def test_json_is_the_whole_of_stdout_and_carries_the_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, TWO_FILES,
                                 finding("`payments.validate` is wrong."))
            code, out, _ = self.explain("RES-01", "--repo", proj, "--json")
            self.assertEqual(0, code)
            doc = json.loads(out)
            self.assertEqual(1, doc["schema_version"])
            self.assertEqual("RES-01", doc["finding"]["id"])
            link = doc["links"][0]
            self.assertEqual("match", link["state"])
            self.assertEqual("qualified_symbol", link["method"])
            self.assertEqual([{"kind": "qualified_name_in_note",
                               "value": "payments.validate"}], link["basis"])

    def test_the_internal_reason_appears_only_with_debug(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, TWO_FILES,
                                 finding("`payments.validate` is wrong."))
            _c, plain, _ = self.explain("RES-01", "--repo", proj, "--json")
            self.assertNotIn("internal_reason", plain)
            self.assertNotIn("qualified-name", plain)
            _c, debug, _ = self.explain("RES-01", "--repo", proj, "--json",
                                        "--debug")
            self.assertEqual("qualified-name",
                             json.loads(debug)["links"][0]["internal_reason"])

    def test_explain_reads_and_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, vault, _s = build(tmp, TWO_FILES,
                                    finding("`payments.validate` is wrong."))
            before = {}
            for root, _dirs, files in os.walk(os.path.join(proj,
                                                           ".tracelink")):
                for name in files:
                    path = os.path.join(root, name)
                    with open(path, "rb") as fh:
                        before[path] = (fh.read(), os.stat(path).st_mtime)
            self.explain("RES-01", "--repo", proj, "--json")
            for path, (blob, mtime) in before.items():
                with open(path, "rb") as fh:
                    self.assertEqual(blob, fh.read(), path)
                self.assertEqual(mtime, os.stat(path).st_mtime, path)


class ConsultCarriesTheSameProvenance(unittest.TestCase):
    def test_a_consult_anchor_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj, _v, _s = build(tmp, TWO_FILES,
                                 finding("`payments.validate` is wrong."))
            result = consult_mod.consult(proj, "src/payments.py")
            hit = result.notes[0].symbols[0]
            self.assertEqual("match", hit.provenance.state)
            self.assertEqual("qualified_symbol", hit.provenance.method)
            self.assertEqual((("qualified_name_in_note",
                               "payments.validate"),), hit.provenance.basis)

    def test_a_state_without_provenance_leaves_the_link_unexplained(self):
        """Not an error and not a guess: schema 4 is what carries reasons,
        and a link made before it says so."""
        self.assertIsNone(consult_mod._provenance(None))
        self.assertIsNone(consult_mod._provenance({"basis": []}))


if __name__ == "__main__":
    unittest.main()
