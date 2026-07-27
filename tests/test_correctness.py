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
        once = link.apply_block(note, [("parse_payload", "inline-code")], SYMS)
        twice = link.apply_block(once, [("parse_payload", "inline-code")], SYMS)
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
