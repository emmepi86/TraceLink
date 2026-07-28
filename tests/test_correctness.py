"""The cases that produce silently wrong output.

Every test here corresponds to a defect that shipped in 0.1.0 and was found by
review rather than by use — which is the argument for having them.

    python3 -m unittest discover tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tracelink import linker as link  # noqa: E402
from tracelink.splitter import classify, severity, note_body  # noqa: E402

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
            subprocess.run([sys.executable, f"{root}/src/../scripts/symbols.py",
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
        pass  # note_body imported at module level
        block = "## RES-01 — example\n\n### STATUS: CLOSED\n### SEVERITY: LOW\n"
        _body, status, sev, _n, _t = note_body("RES-01", [block], "RES")
        self.assertEqual(status, "closed")
        self.assertEqual(sev, "low")

    def test_bracket_severity_still_works(self):
        pass  # note_body imported at module level
        _b, status, sev, _n, _t = note_body("RES-01", ["## RES-01 — x [HIGH]\n"], "RES")
        self.assertEqual((status, sev), ("open", "high"))

    def test_a_body_mentioning_another_closure_does_not_close_the_note(self):
        pass  # note_body imported at module level
        block = "## RES-02 — x [MEDIUM]\n\nThe earlier RES-01 finding is CLOSED, but this is open.\n"
        _b, status, _sev, _n, _t = note_body("RES-02", [block], "RES")
        self.assertEqual(status, "open")


class NotesAreOwnedAndPruned(unittest.TestCase):
    def test_generated_notes_carry_the_ownership_marker(self):
        pass  # note_body imported at module level
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
                [f"{root}/src/../scripts/symbols.py", "--repo",
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
                [f"{root}/src/../scripts/symbols.py", "--repo",
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
            subprocess.run([sys.executable, f"{root}/src/../scripts/symbols.py",
                            "--repo", f"{root}/examples/demo-project",
                            "--backend", "scan", "--out", out],
                           check=True, capture_output=True)
            d = json.load(open(out))
            self.assertEqual(d["schema_version"], 3)
            self.assertEqual(d["indexing"]["backend"], "scan")
            self.assertIn("repository", d)
            self.assertIsInstance(d["symbols"]["parse_payload"], list)
            self.assertIn("fingerprint", d["repository"])


class EveryBackendPreservesDuplicates(unittest.TestCase):
    """0.3.0 shipped `_add()` to keep every definition and left `label in out`
    in the graphify path, so that backend still resolved homonyms by node
    order — the exact defect the release claimed to remove. The suite missed it
    because no test exercised a backend with two same-named definitions."""

    def test_graphify(self):
        import json as j
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "graphify-out"))
            j.dump({"nodes": [
                {"label": "validate", "source_file": "src/users.py", "source_location": 10},
                {"label": "validate", "source_file": "src/payments.py", "source_location": 20},
            ]}, open(os.path.join(tmp, "graphify-out", "graph.json"), "w"))
            syms, _err, _c = symbols.from_graphify(tmp)
        self.assertEqual(len(syms["validate"]), 2)

    def test_graphify_accepts_display_line_locations(self):
        import json as j
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "graphify-out"))
            graph_path = os.path.join(tmp, "graphify-out", "graph.json")
            with open(graph_path, "w") as graph_file:
                j.dump({"nodes": [
                    {"label": "from_prefixed", "source_file": "src/a.py",
                     "source_location": "L88"},
                    {"label": "from_numeric", "source_file": "src/b.py",
                     "source_location": "89"},
                    {"label": "from_range", "source_file": "src/c.py",
                     "source_location": "L90-L96"},
                    {"label": "unknown_location", "source_file": "src/d.py",
                     "source_location": "not-a-line"},
                ]}, graph_file)
            syms, err, _considered = symbols.from_graphify(tmp)

        self.assertIsNone(err)
        self.assertEqual(syms["from_prefixed"][0]["line"], 88)
        self.assertEqual(syms["from_numeric"][0]["line"], 89)
        self.assertEqual(syms["from_range"][0]["line"], 90)
        self.assertIsNone(syms["unknown_location"][0]["line"])

    def test_line_location_parser_is_strict_and_fail_open(self):
        from tracelink.symbol_index import _line_number

        accepted = (
            (88, 88),
            (88.0, 88),
            ("88", 88),
            ("L88", 88),
            ("l 88", 88),
            ("88-94", 88),
            ("L88-L94", 88),
            ("L88..L94", 88),
            ("L88–L94", 88),
        )
        for raw, expected in accepted:
            with self.subTest(raw=raw):
                self.assertEqual(_line_number(raw), expected)

        for raw in (None, False, True, 0, -1, 88.5, "", "L", "Lx",
                    "line 88", "L88 trailing", ["L88"], {"line": 88}):
            with self.subTest(raw=raw):
                self.assertIsNone(_line_number(raw))

    def test_scan(self):
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "a"))
            os.makedirs(os.path.join(tmp, "b"))
            open(os.path.join(tmp, "a", "users.py"), "w").write("def validate(x):\n    return x\n")
            open(os.path.join(tmp, "b", "payments.py"), "w").write("def validate(x):\n    return x\n")
            syms, _err, _c = symbols.from_scan(tmp)
        self.assertEqual(len(syms["validate"]), 2)

    def test_ctags(self):
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "tags"), "w").write(
                "validate\tsrc/users.py\t/^def validate/;\"\tf\tline:10\n"
                "validate\tsrc/payments.py\t/^def validate/;\"\tf\tline:20\n")
            syms, _err, _c = symbols.from_ctags(tmp)
        self.assertEqual(len(syms["validate"]), 2)

    def test_qualified_name_is_null_when_the_backend_cannot_qualify(self):
        """Repeating the bare name and calling it qualified would make the
        qualified-name disambiguation silently useless."""
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "tags"), "w").write(
                "validate\tsrc/users.py\t/^def validate/;\"\tf\tline:10\n")
            syms, _err, _c = symbols.from_ctags(tmp)
        self.assertIsNone(syms["validate"][0]["qualified_name"])


class ContradictoryEvidenceStaysAmbiguous(unittest.TestCase):
    TWO = [
        {"path": "src/users.py", "line": 31, "kind": "py", "qualified_name": "users.validate"},
        {"path": "src/payments.py", "line": 74, "kind": "py", "qualified_name": "payments.validate"},
    ]

    def test_two_qualified_names(self):
        _loc, how = link.disambiguate(
            "validate", self.TWO, "`users.validate` differs from `payments.validate`.", {})
        self.assertEqual(how, "multiple-qualified-names")

    def test_two_paths(self):
        _loc, how = link.disambiguate(
            "validate", self.TWO, "compares src/users.py and src/payments.py.", {})
        self.assertEqual(how, "multiple-paths-in-note")

    def test_qualified_name_and_path_disagree(self):
        """Both are authorial evidence of the same weight. Inventing a
        precedence between them would be the tool deciding, not the author."""
        _loc, how = link.disambiguate(
            "validate", self.TWO,
            "`users.validate` has the bug described in src/payments.py.", {})
        self.assertEqual(how, "qualified-name-and-path-disagree")

    def test_they_agree(self):
        loc, how = link.disambiguate(
            "validate", self.TWO,
            "`payments.validate` in src/payments.py is the one.", {})
        self.assertEqual(loc["path"], "src/payments.py")

    def test_a_single_qualified_name_still_resolves(self):
        loc, how = link.disambiguate("validate", self.TWO, "see `payments.validate`.", {})
        self.assertEqual((loc["path"], how), ("src/payments.py", "qualified-name"))


class OwnershipIsStructural(unittest.TestCase):
    def test_the_marker_in_prose_does_not_grant_ownership(self):
        self.assertFalse(link.is_owned_note(
            "# my notes\n\ntracelink recognises notes by `tracelink_schema: 1`.\n"))

    def test_the_marker_in_frontmatter_does(self):
        self.assertTrue(link.is_owned_note("---\ntracelink_schema: 1\nid: RES-01\n---\n\n# x\n"))

    def test_no_frontmatter_is_not_owned(self):
        self.assertFalse(link.is_owned_note("tracelink_schema: 1\n\n# x\n"))


class PruningCannotEscapeTheVault(unittest.TestCase):
    def test_a_traversing_manifest_entry_is_refused(self):
        import json as j
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            os.makedirs(vault)
            outside = os.path.join(tmp, "important.md")
            open(outside, "w").write("---\ntracelink_schema: 1\n---\n\n# not yours\n")
            j.dump({"schema_version": 1, "generated_notes": ["../important.md"]},
                   open(os.path.join(vault, ".tracelink-manifest.json"), "w"))
            reg = os.path.join(tmp, "F.md")
            open(reg, "w").write("## RES-01 — x\nbody\n")
            subprocess.run([sys.executable, f"{root}/scripts/split.py", "--register", reg,
                            "--out", vault, "--prefix", "RES"], capture_output=True)
            self.assertTrue(os.path.exists(outside), "a file outside the vault was deleted")


class FreshnessIsVerifiedNotAssumed(unittest.TestCase):
    """0.3.x recorded provenance and printed it. It never compared it, so an
    index built on one commit was used against another without a word — and the
    changelog claimed staleness "can be detected instead of trusted", which was
    true of a consumer and not of tracelink."""

    @staticmethod
    def _index(repo, out):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run([sys.executable, f"{root}/src/../scripts/symbols.py",
                        "--repo", repo, "--backend", "scan", "--out", out],
                       check=True, capture_output=True)
        return json.load(open(out))

    def setUp(self):
        global json
        import json

    def test_the_index_does_not_invalidate_itself(self):
        """Writing symbols.json inside the repository made the tree stale the
        instant the index was written. Found by a test that indexed into its own
        fixture directory — the tool was invalidating its own output."""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            out = os.path.join(tmp, "symbols.json")
            payload = self._index(tmp, out)
            self.assertEqual(link.verify_freshness(payload, tmp, out).status, "fresh")

    def test_an_unchanged_tree_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            out = os.path.join(tmp, "s.json")
            payload = self._index(tmp, out)
            f = link.verify_freshness(payload, tmp, out)
        self.assertEqual(f.status, "fresh")
        self.assertIn("fingerprint-match", f.reasons)

    def test_a_modified_file_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.py")
            open(src, "w").write("def alpha():\n    pass\n")
            out = os.path.join(tmp, "s.json")
            payload = self._index(tmp, out)
            open(src, "a").write("# changed\n")
            f = link.verify_freshness(payload, tmp, out)
        self.assertEqual(f.status, "stale")
        self.assertIn("fingerprint-mismatch", f.reasons)

    def test_an_added_file_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            out = os.path.join(tmp, "s.json")
            payload = self._index(tmp, out)
            open(os.path.join(tmp, "b.py"), "w").write("def beta():\n    pass\n")
            f = link.verify_freshness(payload, tmp, out)
        self.assertEqual(f.status, "stale")

    def test_a_removed_file_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            open(os.path.join(tmp, "b.py"), "w").write("def beta():\n    pass\n")
            out = os.path.join(tmp, "s.json")
            payload = self._index(tmp, out)
            os.remove(os.path.join(tmp, "b.py"))
            f = link.verify_freshness(payload, tmp, out)
        self.assertEqual(f.status, "stale")

    def test_a_legacy_index_is_unknown_not_invalid(self):
        f = link.verify_freshness({"parse_payload": "src/parser.py:L4"}, ".")
        self.assertEqual(f.status, "unknown")
        self.assertIn("legacy-index-without-provenance", f.reasons)

    def test_a_v2_index_with_a_matching_commit_on_a_clean_tree_is_unknown(self):
        """A commit proves the checkout, not the working tree, so a v2 index
        cannot be called fresh. It also cannot be called stale on a clean tree —
        there is no evidence of divergence. That is what `unknown` is for.

        The repository must be clean for this: on a dirty tree the correct
        answer is `stale`, and asserting `unknown` there would be asserting the
        wrong contract."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                        ["config", "user.name", "t"]):
                subprocess.run(["git", "-C", tmp] + cmd, capture_output=True)
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            subprocess.run(["git", "-C", tmp, "add", "."], capture_output=True)
            subprocess.run(["git", "-C", tmp, "commit", "-qm", "x"], capture_output=True)
            cur = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            f = link.verify_freshness(
                {"schema_version": 2, "repo_commit": cur, "symbols": {}}, tmp)
        self.assertEqual(f.status, "unknown")
        self.assertIn("commit-match-without-fingerprint", f.reasons)

    def test_a_v2_index_on_a_dirty_tree_is_stale(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"],
                        ["config", "user.name", "t"]):
                subprocess.run(["git", "-C", tmp] + cmd, capture_output=True)
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            subprocess.run(["git", "-C", tmp, "add", "."], capture_output=True)
            subprocess.run(["git", "-C", tmp, "commit", "-qm", "x"], capture_output=True)
            cur = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
            open(os.path.join(tmp, "a.py"), "a").write("# dirty\n")
            f = link.verify_freshness(
                {"schema_version": 2, "repo_commit": cur, "symbols": {}}, tmp)
        self.assertEqual(f.status, "stale")
        self.assertIn("working-tree-modified", f.reasons)

    def test_a_corrupt_index_is_invalid(self):
        f = link.verify_freshness({"symbols": "not a mapping"}, ".")
        self.assertEqual(f.status, "invalid")


class TheFingerprintDependsOnContentAlone(unittest.TestCase):
    def test_the_path_used_to_reach_the_tree_does_not_matter(self):
        from tracelink import symbol_index as symbols
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "examples", "demo-project")
        a = symbols.fingerprint(root)[0]
        b = symbols.fingerprint(root + os.sep)[0]
        c = symbols.fingerprint(os.path.join(root, ".."))[0]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_content_change_changes_the_digest(self):
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "x.py")
            open(f, "w").write("a")
            before = symbols.fingerprint(tmp)[0]
            open(f, "w").write("b")
            self.assertNotEqual(before, symbols.fingerprint(tmp)[0])

    def test_it_is_stable_across_runs(self):
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("c.py", "a.py", "b.py"):
                open(os.path.join(tmp, n), "w").write(f"# {n}\n")
            self.assertEqual(symbols.fingerprint(tmp)[0], symbols.fingerprint(tmp)[0])


class FreshnessTracksTheIndexScopeNotTheRepository(unittest.TestCase):
    """These go through `verify_freshness`, not `fingerprint`.

    0.4.1 computed the indexing fingerprint over the backend's scope and
    recomputed the verification fingerprint over the whole tree, so the two
    described different sets and a freshly written index came out `stale`
    immediately. The 0.4.1 tests could not see it: they compared two direct
    calls to `fingerprint()` and never exercised the caller that failed to pass
    the scope along — the same blindness as the `note_body` case in 0.2.1.
    """

    @staticmethod
    def _indexed(tmp):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = os.path.join(tmp, "s.json")
        subprocess.run([sys.executable, f"{root}/src/../scripts/symbols.py", "--repo", tmp,
                        "--backend", "scan", "--out", out], check=True, capture_output=True)
        import json as j
        return j.load(open(out)), out

    def _case(self, mutate):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            open(os.path.join(tmp, "README.md"), "w").write("# docs\n")
            payload, out = self._indexed(tmp)
            mutate(tmp)
            return link.verify_freshness(payload, tmp, out).status

    def test_a_fresh_index_is_fresh(self):
        """The regression that shipped in 0.4.1: this returned `stale`."""
        self.assertEqual(self._case(lambda t: None), "fresh")

    def test_a_readme_change_stays_fresh(self):
        self.assertEqual(
            self._case(lambda t: open(os.path.join(t, "README.md"), "a").write("x")), "fresh")

    def test_a_generated_vault_stays_fresh(self):
        def mutate(t):
            os.makedirs(os.path.join(t, "vault"))
            open(os.path.join(t, "vault", "R.md"), "w").write("---\nid: R\n---\n")
        self.assertEqual(self._case(mutate), "fresh")

    def test_a_source_change_is_stale(self):
        self.assertEqual(
            self._case(lambda t: open(os.path.join(t, "a.py"), "a").write("# x\n")), "stale")

    def test_an_added_source_file_is_stale(self):
        """Replaying only the recorded file list would miss this: a new source
        file would never be hashed, the digest would match, and an unindexed
        symbol would sit behind a `fresh` verdict. The scope is re-derived."""
        self.assertEqual(
            self._case(lambda t: open(os.path.join(t, "b.py"), "w").write("def beta(): pass\n")),
            "stale")

    def test_a_removed_source_file_is_stale(self):
        self.assertEqual(self._case(lambda t: os.remove(os.path.join(t, "a.py"))), "stale")

    def test_a_v3_index_without_a_recorded_scope_is_unknown(self):
        """0.4.0 and 0.4.1 wrote a scoped fingerprint without the scope. It
        cannot be reproduced, and saying so beats hashing a different set."""
        payload = {"schema_version": 3, "symbols": {},
                   "repository": {"fingerprint": "sha256:deadbeef"},
                   "indexing": {"backend": "scan"}}
        f = link.verify_freshness(payload, ".")
        self.assertEqual(f.status, "unknown")
        self.assertIn("fingerprint-scope-not-recorded", f.reasons)

    def test_the_index_records_its_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha(): pass\n")
            payload, _out = self._indexed(tmp)
        self.assertEqual(payload["indexing"]["scope"]["kind"], "extensions")
        self.assertIn("a.py", payload["indexing"]["files_considered"])
        self.assertEqual(payload["tracelink_version"], "0.4.2")


class TruncationIsNeverSilent(unittest.TestCase):
    def test_the_scan_limit_is_reported(self):
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                open(os.path.join(tmp, f"f{i}.py"), "w").write("def x():\n    pass\n")
            _syms, err, _c = symbols.from_scan(tmp, max_files=1)
        self.assertIn("max-files-reached", err or "")

    def test_build_keeps_notes_from_backends_that_failed_first(self):
        """Returning as soon as a backend produced symbols discarded the note,
        so a truncated scan could report partial: false."""
        from tracelink import symbol_index as symbols
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "a.py"), "w").write("def alpha():\n    pass\n")
            _s, used, notes, _c = symbols.build(tmp, "auto")
        self.assertEqual(used, "scan")
        self.assertTrue(notes, "notes from graphify/ctags were discarded")
