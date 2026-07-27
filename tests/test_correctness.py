"""The cases that produce silently wrong output.

Every test here corresponds to a defect that shipped in 0.1.0 and was found by
review rather than by use — which is the argument for having them.

    python3 -m unittest discover tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import link  # noqa: E402
from split import classify, severity  # noqa: E402

SYMS = {"parse_payload": "src/parser.py:L4", "severity": "scripts/split.py:L60"}
_LOC = {"path": "src/parser.py", "line": 4, "kind": "py",
        "qualified_name": "parser.parse_payload"}


class TracelinkNeverReadsItsOwnOutput(unittest.TestCase):
    """The defect that made the shipped demo report a false success: both
    example notes were linked to `severity`, a function inside split.py, and
    the tool announced "2/2 notes linked"."""

    def test_frontmatter_is_not_scanned(self):
        note = "---\nid: RES-01\nstatus: open\nseverity: high\n---\n\n# RES-01\n\nnothing here.\n"
        self.assertEqual(link.candidates(link.matchable(note), SYMS, 7, set()), [])

    def test_the_generated_block_is_not_scanned(self):
        note = ("# RES-01\n\nprose without symbols.\n\n"
                f"{link._BLOCK_START}\n## Linked code\n\n"
                "- `parse_payload` — src/parser.py:L4\n"
                f"{link._BLOCK_END}\n")
        self.assertEqual(link.candidates(link.matchable(note), SYMS, 7, set()), [])

    def test_a_legacy_block_is_not_scanned_either(self):
        note = "# RES-01\n\nprose.\n\n## Linked code\n\n- `parse_payload` — src/parser.py:L4\n"
        self.assertEqual(link.candidates(link.matchable(note), SYMS, 7, set()), [])

    def test_real_prose_is_still_matched(self):
        note = "---\nid: RES-01\n---\n\n# RES-01\n\n`parse_payload` returns {} for an empty body.\n"
        found = [n for n, _ in link.candidates(link.matchable(note), SYMS, 7, set())]
        self.assertEqual(found, ["parse_payload"])


class LinksAreNotImmortal(unittest.TestCase):
    def test_a_stale_link_is_removed(self):
        """Symbol leaves the prose -> its link must go with it."""
        note = ("# RES-01\n\nprose without symbols.\n\n"
                f"{link._BLOCK_START}\n## Linked code\n\n"
                "- `parse_payload` — src/parser.py:L4\n"
                f"{link._BLOCK_END}\n")
        out = link.apply_block(note, [], SYMS)
        self.assertNotIn("parse_payload", out)
        self.assertNotIn(link._BLOCK_START, out)

    def test_the_block_is_regenerated_not_appended(self):
        note = "# RES-01\n\n`parse_payload` here.\n"
        once = link.apply_block(note, [("parse_payload", _LOC, "inline-code")], SYMS)
        twice = link.apply_block(once, [("parse_payload", _LOC, "inline-code")], SYMS)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(link._BLOCK_START), 1)

    def test_inline_code_outranks_a_bare_identifier(self):
        note = "prose mentioning severity and `parse_payload`."
        ranked = link.candidates(note, SYMS, 4, set())
        self.assertEqual(ranked[0][0], "parse_payload")
        self.assertEqual(ranked[0][1], "inline-code")


class StatusIsNeverGuessed(unittest.TestCase):
    def test_unresolved_is_not_resolved(self):
        """Substring matching classified UNRESOLVED as closed, because it
        contains RESOLVED."""
        self.assertEqual(classify(["## RES-01 — UNRESOLVED"]), "open")

    def test_reopened_after_closed_is_open(self):
        self.assertEqual(
            classify(["## RES-01 — CLOSED", "### RES-01 — REOPENED"]), "open")

    def test_explicit_grammar_wins(self):
        self.assertEqual(classify(["## RES-01", "### STATUS: WITHDRAWN"]), "withdrawn")

    def test_the_last_explicit_status_wins(self):
        self.assertEqual(
            classify(["### STATUS: CLOSED", "### STATUS: REOPENED"]), "open")

    def test_a_body_mentioning_another_finding_does_not_leak(self):
        """Only headings are ever read; this is the caller's contract."""
        self.assertEqual(classify(["## RES-02 — two writers, one structure"]), "open")

    def test_the_latest_severity_wins(self):
        self.assertEqual(severity(["## R [HIGH]", "### R [LOW]"]), "low")
        self.assertEqual(severity(["### SEVERITY: HIGH", "### SEVERITY: MEDIUM"]), "medium")


class TheDemoLinksWhatItClaims(unittest.TestCase):
    def test_end_to_end(self):
        import json
        import subprocess
        root = os.path.join(os.path.dirname(__file__), "..")
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "vault")
            syms = os.path.join(tmp, "symbols.json")
            subprocess.run([sys.executable, f"{root}/scripts/split.py",
                            "--register", f"{root}/examples/FINDINGS.example.md",
                            "--out", vault, "--prefix", "RES"], check=True,
                           capture_output=True)
            subprocess.run([sys.executable, f"{root}/scripts/symbols.py",
                            "--repo", f"{root}/examples/demo-project",
                            "--backend", "scan", "--out", syms], check=True,
                           capture_output=True)
            subprocess.run([sys.executable, f"{root}/scripts/link.py",
                            "--vault", vault, "--symbols", syms], check=True,
                           capture_output=True)
            linked = ""
            for f in os.listdir(vault):
                if f.startswith("RES-"):
                    linked += open(os.path.join(vault, f)).read()
            self.assertIn("parse_payload", linked)
            self.assertNotIn("severity", linked.split("## Linked code")[-1])
            with open(syms) as fh:
                self.assertIn("parse_payload", json.load(fh)["symbols"])


if __name__ == "__main__":
    unittest.main()


class ExplicitGrammarWorksThroughTheCLI(unittest.TestCase):
    """0.2.0 documented `### STATUS: CLOSED` and did not honour it.

    `note_body` filtered headings to `## RES-n` before classifying, so the
    explicit lines were discarded and a note marked CLOSED came out open. The
    unit tests missed it because they called `classify()` directly — a test that
    skips the caller cannot see the caller's mistake.
    """

    def test_status_and_severity_reach_the_note(self):
        from split import note_body
        block = "## RES-01 — example\n\n### STATUS: CLOSED\n### SEVERITY: LOW\n"
        _body, status, sev, _n, _t = note_body("RES-01", [block], "RES")
        self.assertEqual(status, "closed")
        self.assertEqual(sev, "low")

    def test_bracket_severity_still_works(self):
        from split import note_body
        _b, status, sev, _n, _t = note_body("RES-01", ["## RES-01 — x [HIGH]\n"], "RES")
        self.assertEqual((status, sev), ("open", "high"))

    def test_a_body_mentioning_another_closure_does_not_close_the_note(self):
        from split import note_body
        block = "## RES-02 — x [MEDIUM]\n\nThe earlier RES-01 finding is CLOSED, but this is open.\n"
        _b, status, _sev, _n, _t = note_body("RES-02", [block], "RES")
        self.assertEqual(status, "open")


class NotesAreOwnedAndPruned(unittest.TestCase):
    def test_generated_notes_carry_the_ownership_marker(self):
        from split import note_body
        body, *_ = note_body("RES-01", ["## RES-01 — x\n"], "RES")
        self.assertIn("tracelink_schema: 1", body)

    def test_unmanaged_markdown_is_skipped_by_the_linker(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            hand = os.path.join(tmp, "MY-NOTES.md")
            original = "# my own notes\n\n`parse_payload` is mentioned here.\n"
            open(hand, "w").write(original)
            syms = os.path.join(tmp, "s.json")
            import json as j
            j.dump({"backend": "test", "symbols": {"parse_payload": "src/parser.py:L4"}},
                   open(syms, "w"))
            subprocess.run([sys.executable, f"{root}/scripts/link.py",
                            "--vault", tmp, "--symbols", syms], capture_output=True)
            self.assertEqual(open(hand).read(), original)


class TheDemoLinksExactlyTheseSymbols(unittest.TestCase):
    """Asserting exact sets, not membership: a regression that added `severity`
    to one note passed the earlier looser check."""

    def test_exact_symbol_sets(self):
        import json
        import re as _re
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            vault, syms = os.path.join(tmp, "v"), os.path.join(tmp, "s.json")
            for cmd in (
                [f"{root}/scripts/split.py", "--register",
                 f"{root}/examples/FINDINGS.example.md", "--out", vault, "--prefix", "RES"],
                [f"{root}/scripts/symbols.py", "--repo",
                 f"{root}/examples/demo-project", "--backend", "scan", "--out", syms],
                [f"{root}/scripts/link.py", "--vault", vault, "--symbols", syms],
            ):
                subprocess.run([sys.executable] + cmd, check=True, capture_output=True)

            def linked(note):
                text = open(os.path.join(vault, note)).read()
                block = text.split("<!-- tracelink:linked-code:start -->")[1]
                block = block.split("<!-- tracelink:linked-code:end -->")[0]
                return set(_re.findall(r"`([^`]+)`", block))

            self.assertEqual(linked("RES-01.md"), {"parse_payload"})
            self.assertEqual(linked("RES-02.md"), {"ingest_batch", "ingest_stream"})

    def test_second_run_modifies_nothing(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            vault, syms = os.path.join(tmp, "v"), os.path.join(tmp, "s.json")
            for cmd in (
                [f"{root}/scripts/split.py", "--register",
                 f"{root}/examples/FINDINGS.example.md", "--out", vault, "--prefix", "RES"],
                [f"{root}/scripts/symbols.py", "--repo",
                 f"{root}/examples/demo-project", "--backend", "scan", "--out", syms],
                [f"{root}/scripts/link.py", "--vault", vault, "--symbols", syms],
            ):
                subprocess.run([sys.executable] + cmd, check=True, capture_output=True)
            r = subprocess.run([sys.executable, f"{root}/scripts/link.py",
                                "--vault", vault, "--symbols", syms, "--check"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


class AmbiguousSymbolsAreNeverGuessed(unittest.TestCase):
    """0.2.x kept one location per name and discarded the rest, so a finding
    naming `validate` where two modules define it was linked to whichever the
    backend returned first — an answer that depended on filesystem order and
    carried no warning."""

    TWO = {"validate": [
        {"path": "src/users.py", "line": 31, "kind": "py", "qualified_name": "users.validate"},
        {"path": "src/payments.py", "line": 74, "kind": "py", "qualified_name": "payments.validate"},
    ]}

    def test_a_bare_name_is_not_linked(self):
        loc, how = link.disambiguate("validate", self.TWO["validate"],
                                     "the `validate` helper is wrong.", {})
        self.assertIsNone(loc)
        self.assertEqual(how, "ambiguous")

    def test_a_qualified_name_resolves_it(self):
        loc, how = link.disambiguate("validate", self.TWO["validate"],
                                     "the bug is in `payments.validate`.", {})
        self.assertEqual(loc["path"], "src/payments.py")
        self.assertEqual(how, "qualified-name")

    def test_a_path_in_the_note_resolves_it(self):
        loc, how = link.disambiguate("validate", self.TWO["validate"],
                                     "see src/users.py for the failing branch.", {})
        self.assertEqual(loc["path"], "src/users.py")
        self.assertEqual(how, "path-in-note")

    def test_a_frontmatter_override_wins(self):
        loc, how = link.disambiguate("validate", self.TWO["validate"], "prose.",
                                     {"validate": "src/payments.py"})
        self.assertEqual(loc["path"], "src/payments.py")
        self.assertEqual(how, "frontmatter-override")

    def test_a_single_definition_still_links(self):
        loc, how = link.disambiguate(
            "parse_payload",
            [{"path": "src/parser.py", "line": 4, "kind": "py",
              "qualified_name": "parser.parse_payload"}], "prose.", {})
        self.assertEqual(how, "unique")

    def test_v1_symbol_files_still_load(self):
        norm = link.normalise({"parse_payload": "src/parser.py:L4"})
        self.assertEqual(norm["parse_payload"][0]["path"], "src/parser.py")
        self.assertEqual(link.fmt(norm["parse_payload"][0]), "src/parser.py:L4")


class TheSymbolIndexCarriesProvenance(unittest.TestCase):
    def test_schema_and_backend_are_recorded(self):
        import json
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "s.json")
            subprocess.run([sys.executable, f"{root}/scripts/symbols.py",
                            "--repo", f"{root}/examples/demo-project",
                            "--backend", "scan", "--out", out],
                           check=True, capture_output=True)
            d = json.load(open(out))
            self.assertEqual(d["schema_version"], 2)
            self.assertEqual(d["backend"], "scan")
            self.assertIn("repo_commit", d)
            self.assertIsInstance(d["symbols"]["parse_payload"], list)
