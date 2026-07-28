"""0.5.1 — the failures found by using the tool on a real project.

Three of these come from one session against a clinical codebase, and each was
a case where tracelink did something defensible and unhelpful:

- a register whose findings read `### F1` produced nothing, and the message
  named the pattern it wanted without showing what the file contained;
- splitting a second register into an existing vault rewrote `INDEX.md` to
  describe only the newcomer, orphaning the notes already there — a vault that
  is formally valid and semantically false;
- a symbol defined in two places was named by a note and then vanished from the
  inverse index entirely, which is silence exactly where the answer was two
  answers.

    python3 -m unittest discover tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tracelink.splitter import (  # noqa: E402
    detect_identifier_styles,
    finding_number,
    finding_pattern,
    split,
)

_SPLIT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split.py")

REGISTER_HYPHENLESS = """# Audit

## F1 — Negations become active diagnoses

### STATUS: OPEN
### SEVERITY: HIGH

The guard misses them.

## F2 — Rare phenotypes collapse

### STATUS: CLOSED
### SEVERITY: MEDIUM

Fixed.
"""

REGISTER_HYPHENATED = """# Audit

## RES-7 — something

### STATUS: OPEN

Body.
"""


class TestIdentifiersAreAcceptedAsWritten(unittest.TestCase):
    """`F-1` and `F1` are one identifier written by two people."""

    def test_both_spellings_match(self):
        import re
        pattern = finding_pattern("F")
        self.assertTrue(re.match(pattern, "F1"))
        self.assertTrue(re.match(pattern, "F-1"))

    def test_the_human_identifier_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.md")
            open(path, "w").write(REGISTER_HYPHENLESS)
            found = split(path, "F")
        self.assertEqual(list(found), ["F1", "F2"],
                         "an id written F1 must not be rewritten to F-1")

    def test_numbers_survive_separators_and_hyphenated_prefixes(self):
        self.assertEqual(finding_number("F1"), 1)
        self.assertEqual(finding_number("RES-39"), 39)
        self.assertEqual(finding_number("P1-CQR-4"), 4)

    def test_a_prefix_containing_hyphens_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.md")
            open(path, "w").write("## P1-CQR-4 — a finding\n\nBody.\n")
            self.assertEqual(list(split(path, "P1-CQR")), ["P1-CQR-4"])


class TestFailureIsDiagnostic(unittest.TestCase):
    def test_it_reports_the_styles_actually_present(self):
        styles = detect_identifier_styles(REGISTER_HYPHENLESS)
        self.assertIn("F<n>", styles)
        self.assertEqual(styles["F<n>"], 2)

    def test_a_failed_split_shows_what_it_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.md")
            open(path, "w").write(REGISTER_HYPHENLESS)
            out = subprocess.run(
                [sys.executable, _SPLIT, "--register", path,
                 "--out", os.path.join(tmp, "v"), "--prefix", "RES"],
                capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("First headings found", out.stdout)
        self.assertIn("F1", out.stdout)

    def test_inspect_writes_nothing_and_lists_styles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.md")
            open(path, "w").write(REGISTER_HYPHENLESS)
            vault = os.path.join(tmp, "v")
            out = subprocess.run(
                [sys.executable, _SPLIT, "--register", path,
                 "--out", vault, "--prefix", "F", "--inspect"],
                capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("F<n>", out.stdout)
        self.assertFalse(os.path.exists(vault), "--inspect must not create a vault")


class TestAVaultBelongsToOneRegister(unittest.TestCase):
    """Splitting a second register into a vault rewrote INDEX.md to describe
    only the newcomer and left the earlier notes orphaned. Merging is a
    deliberate operation, not a side effect of running split twice."""

    def _split(self, register_text, prefix, vault, tmp, name="reg.md", extra=()):
        path = os.path.join(tmp, name)
        open(path, "w").write(register_text)
        return subprocess.run(
            [sys.executable, _SPLIT, "--register", path, "--out", vault,
             "--prefix", prefix, *extra],
            capture_output=True, text=True)

    def test_a_different_prefix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            first = self._split(REGISTER_HYPHENATED, "RES", vault, tmp, "a.md")
            self.assertEqual(first.returncode, 0)

            second = self._split(REGISTER_HYPHENLESS, "F", vault, tmp, "b.md")
            self.assertEqual(second.returncode, 2)
            self.assertIn("already belongs to prefix RES", second.stdout)
            self.assertTrue(os.path.exists(os.path.join(vault, "RES-7.md")),
                            "the refused split must not disturb what was there")
            self.assertFalse(os.path.exists(os.path.join(vault, "F1.md")))

    def test_a_different_register_with_the_same_prefix_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            self._split(REGISTER_HYPHENATED, "RES", vault, tmp, "a.md")
            second = self._split(REGISTER_HYPHENATED.replace("RES-7", "RES-8"),
                                 "RES", vault, tmp, "b.md")
            self.assertEqual(second.returncode, 2)
            self.assertIn("was built from a.md", second.stdout)

    def test_adopt_vault_is_the_explicit_way_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            self._split(REGISTER_HYPHENATED, "RES", vault, tmp, "a.md")
            second = self._split(REGISTER_HYPHENLESS, "F", vault, tmp, "b.md",
                                 extra=("--adopt-vault",))
            self.assertEqual(second.returncode, 0)

    def test_the_manifest_records_the_register_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            self._split(REGISTER_HYPHENATED, "RES", vault, tmp, "a.md")
            manifest = json.load(open(os.path.join(vault, ".tracelink-manifest.json")))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["register"], {"prefix": "RES", "source": "a.md"})

    def test_a_v1_manifest_is_still_understood(self):
        """An existing vault must not become unusable on upgrade."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = os.path.join(tmp, "v")
            os.makedirs(vault)
            json.dump({"schema_version": 1, "generated_from": "a.md",
                       "generated_notes": ["RES-7.md"]},
                      open(os.path.join(vault, ".tracelink-manifest.json"), "w"))
            refused = self._split(REGISTER_HYPHENLESS, "F", vault, tmp, "b.md")
            self.assertEqual(refused.returncode, 2)


if __name__ == "__main__":
    unittest.main()
