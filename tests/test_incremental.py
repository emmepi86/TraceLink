"""Incremental relinking must never trade correctness for speed.

The sidecar `.tracelink-link-state.json` lets the linker skip notes it can
PROVE would come out identical. Every test here is a way that proof could be
faked: a moved symbol behind an unchanged note, a hand-edited block the state
does not know about, an override edit hidden in frontmatter the content hash
does not see, a corrupt state file. In every doubtful case the answer is the
same: relink, because a stale link is a wrong answer delivered quickly.

    python3 -m unittest discover tests -v
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
STATE = ".tracelink-link-state.json"


def _stat(r, key):
    m = re.search(key + r":\s+(\d+)", r.stdout)
    assert m, f"{key} not reported:\n{r.stdout}"
    return int(m.group(1))


class _VaultCase(unittest.TestCase):
    """Two owned notes, two unambiguous symbols, one linker run away."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = os.path.join(self._tmp.name, "v")
        os.mkdir(self.vault)
        self.syms = os.path.join(self._tmp.name, "s.json")
        self.addCleanup(self._tmp.cleanup)

    def note(self, stem, prose, frontmatter=""):
        open(os.path.join(self.vault, f"{stem}.md"), "w").write(
            f"---\ntracelink_schema: 1\nid: {stem}\n{frontmatter}---\n\n"
            f"# {stem}\n\n{prose}\n")

    def symbols(self, mapping):
        json.dump({"backend": "test", "symbols": mapping}, open(self.syms, "w"))

    def link(self, *extra):
        return subprocess.run(
            [sys.executable, f"{ROOT}/scripts/link.py",
             "--vault", self.vault, "--symbols", self.syms, *extra],
            capture_output=True, text=True)

    def read(self, name):
        return open(os.path.join(self.vault, name)).read()

    def mtime(self, name):
        return os.stat(os.path.join(self.vault, name)).st_mtime_ns

    def standard_vault(self):
        self.note("RES-01", "`parse_payload` returns {} for an empty body.")
        self.note("RES-02", "`ingest_batch` retries forever on 500.")
        self.symbols({"parse_payload": "src/parser.py:L4",
                      "ingest_batch": "src/ingest.py:L9"})


class UnchangedVaultsAreSkippedWithoutBeingTouched(_VaultCase):
    def test_second_run_skips_every_note_and_rewrites_no_file(self):
        self.standard_vault()
        r1 = self.link()
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertEqual(_stat(r1, "notes_skipped_unchanged"), 0)
        before = {n: self.mtime(n) for n in ("RES-01.md", "RES-02.md")}
        index_before = self.read("CODE-INDEX.md")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 2)
        self.assertEqual(_stat(r2, "notes_scanned"), 2)
        self.assertEqual(_stat(r2, "notes_modified"), 0)
        for n, t in before.items():
            self.assertEqual(self.mtime(n), t, f"{n} was rewritten")
        self.assertEqual(self.read("CODE-INDEX.md"), index_before)

    def test_the_json_report_carries_the_additive_key(self):
        self.standard_vault()
        self.link()
        r2 = self.link("--format", "json")
        payload = json.loads(r2.stdout)
        self.assertEqual(payload["linking"]["notes_skipped_unchanged"], 2)
        self.assertEqual(payload["linking"]["notes_scanned"], 2)

    def test_the_backward_index_still_lists_skipped_notes(self):
        """Skipping a note must not evict its rows from CODE-INDEX — the
        inverse index is rebuilt whole, so every skipped note has to
        contribute exactly what a full run would have computed for it."""
        self.standard_vault()
        self.link()
        r2 = self.link()
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 2)
        index = self.read("CODE-INDEX.md")
        self.assertIn("[[RES-01]]", index)
        self.assertIn("[[RES-02]]", index)
        self.assertEqual(_stat(r2, "symbols_linked"), 2)


class OnlyWhatChangedIsRelinked(_VaultCase):
    def test_editing_one_note_relinks_only_that_note(self):
        self.standard_vault()
        self.link()
        t1 = self.mtime("RES-01.md")
        self.note("RES-02", "`ingest_stream` drops the tail chunk.")
        self.symbols({"parse_payload": "src/parser.py:L4",
                      "ingest_batch": "src/ingest.py:L9",
                      "ingest_stream": "src/ingest.py:L31"})
        # symbols changed too (a new name) — but RES-01 neither links nor
        # mentions it, so RES-01 must still be skipped.
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1)
        self.assertEqual(self.mtime("RES-01.md"), t1)
        self.assertIn("ingest_stream", self.read("RES-02.md"))

    def test_a_symbol_moving_line_relinks_the_note_that_links_it(self):
        """The managed block prints `path:Lline`. A moved definition with an
        untouched note is exactly the stale link this feature must not
        create."""
        self.standard_vault()
        self.link()
        self.assertIn("src/parser.py:L4", self.read("RES-01.md"))
        t2 = self.mtime("RES-02.md")
        self.symbols({"parse_payload": "src/parser.py:L40",
                      "ingest_batch": "src/ingest.py:L9"})
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("src/parser.py:L40", self.read("RES-01.md"))
        self.assertNotIn(":L4\n", self.read("RES-01.md").split("## Linked code")[-1])
        self.assertEqual(self.mtime("RES-02.md"), t2, "unrelated note rewritten")
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1)
        self.assertIn("src/parser.py:L40", self.read("CODE-INDEX.md"))

    @staticmethod
    def _block(text):
        if "tracelink:linked-code:start" not in text:
            return ""
        return text.split("tracelink:linked-code:start -->")[1].split(
            "<!-- tracelink:linked-code:end")[0]

    def test_a_new_symbol_an_old_note_mentions_gets_linked(self):
        self.note("RES-01", "`parse_payload` breaks when `brand_new_helper` is absent.")
        self.note("RES-02", "`ingest_batch` retries forever on 500.")
        self.symbols({"parse_payload": "src/parser.py:L4",
                      "ingest_batch": "src/ingest.py:L9"})
        self.link()
        self.assertNotIn("brand_new_helper", self._block(self.read("RES-01.md")))
        self.symbols({"parse_payload": "src/parser.py:L4",
                      "ingest_batch": "src/ingest.py:L9",
                      "brand_new_helper": "src/helper.py:L2"})
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("brand_new_helper", self._block(self.read("RES-01.md")))
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1)

    def test_a_removed_symbol_unlinks_the_note_that_linked_it(self):
        self.standard_vault()
        self.link()
        self.symbols({"ingest_batch": "src/ingest.py:L9"})
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("## Linked code", self.read("RES-01.md"))
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1)
        self.assertNotIn("parse_payload", self.read("CODE-INDEX.md"))


class TheStateIsNeverTrustedBeyondItsProof(_VaultCase):
    def test_corrupt_state_forces_a_full_relink_without_crashing(self):
        self.standard_vault()
        self.link()
        open(os.path.join(self.vault, STATE), "w").write("{not json")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 0)
        # and the run heals the state for the next one
        json.load(open(os.path.join(self.vault, STATE)))

    def test_different_options_force_a_full_relink(self):
        self.standard_vault()
        self.link()
        r2 = self.link("--min-len", "5")
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 0)

    def test_full_flag_reprocesses_everything(self):
        self.standard_vault()
        self.link()
        r2 = self.link("--full")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 0)

    def test_a_hand_edited_block_is_healed_not_skipped(self):
        """The authored text is unchanged, so the content hash matches — but
        the block on disk is not what the state promised. Skipping would
        leave the lie in place."""
        self.standard_vault()
        self.link()
        path = os.path.join(self.vault, "RES-01.md")
        edited = self.read("RES-01.md").replace("src/parser.py:L4",
                                                "src/parser.py:L999")
        open(path, "w").write(edited)
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("src/parser.py:L4", self.read("RES-01.md"))
        self.assertNotIn("L999", self.read("RES-01.md"))

    def test_an_override_only_edit_is_not_masked_by_the_state(self):
        """`tracelink:` overrides live in frontmatter, which matchable()
        strips. A content hash blind to them would freeze the link on the
        old destination — the exact class of stale link this task exists
        to prevent."""
        self.note("RES-01", "`validate` rejects the empty payload.",
                  frontmatter="tracelink:\n  validate: src/users.py\n")
        self.symbols({"validate": [
            {"path": "src/users.py", "line": 3, "kind": "py", "qualified_name": None},
            {"path": "src/payments.py", "line": 7, "kind": "py", "qualified_name": None}]})
        self.link()
        self.assertIn("src/users.py:L3", self.read("RES-01.md"))
        text = self.read("RES-01.md").replace("validate: src/users.py",
                                              "validate: src/payments.py")
        open(os.path.join(self.vault, "RES-01.md"), "w").write(text)
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("src/payments.py:L7", self.read("RES-01.md"))


class CheckModeIgnoresTheState(_VaultCase):
    def test_check_verifies_everything_and_writes_nothing(self):
        self.standard_vault()
        self.link()
        state_before = self.read(STATE)
        path = os.path.join(self.vault, "RES-01.md")
        edited = self.read("RES-01.md").replace("src/parser.py:L4",
                                                "src/parser.py:L999")
        open(path, "w").write(edited)
        r = self.link("--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("file(s) would change", r.stdout)
        self.assertEqual(_stat(r, "notes_skipped_unchanged"), 0)
        self.assertEqual(self.read(STATE), state_before,
                         "--check must not write the state")
        self.assertIn("L999", self.read("RES-01.md"),
                      "--check must not rewrite the note")


class TheStateIsRewrittenOnEverySuccessfulRun(_VaultCase):
    def test_even_a_run_that_relinked_nothing_rewrites_the_state(self):
        self.standard_vault()
        self.link()
        before = os.stat(os.path.join(self.vault, STATE)).st_mtime_ns
        content_before = self.read(STATE)
        r2 = self.link()
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 2)
        self.assertGreater(os.stat(os.path.join(self.vault, STATE)).st_mtime_ns,
                           before, "state not rewritten")
        self.assertEqual(json.loads(self.read(STATE)),
                         json.loads(content_before))

    def test_the_state_records_the_symbols_fingerprint(self):
        from tracelink import linker
        self.standard_vault()
        self.link()
        state = json.loads(self.read(STATE))
        self.assertEqual(state["schema_version"], linker._STATE_SCHEMA)
        self.assertTrue(state["symbols_fingerprint"].startswith("sha256:"))
        self.assertEqual(sorted(state["notes"]), ["RES-01.md", "RES-02.md"])


class TheStateFileIsNotANote(_VaultCase):
    def test_the_sidecar_is_neither_matched_nor_warned_about(self):
        self.standard_vault()
        self.link()
        r2 = self.link()
        self.assertEqual(_stat(r2, "notes_scanned"), 2)
        self.assertNotIn("without tracelink_schema", r2.stdout + r2.stderr)
        self.assertNotIn("link-state", self.read("CODE-INDEX.md"))


class SkippingActuallySkipsTheWork(_VaultCase):
    """Correctness first — but a "skipped" note that is disambiguated all
    over again has not been skipped, it has been relinked without the write.
    The state caches each link's resolved location, whose invariance is
    already proven by `symbol_locations`, so the skip path renders the block
    from the cache and compares bytes: no candidate scan, no disambiguation.
    Any mismatch still falls back to a full recompute of that note."""

    def _main(self, *extra):
        from tracelink import linker
        argv = ["link", "--vault", self.vault, "--symbols", self.syms, *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out):
            rc = linker.main()
        return rc, out.getvalue()

    def _spies(self):
        from tracelink import linker
        dis = mock.patch.object(linker, "disambiguate",
                                side_effect=linker.disambiguate)
        cand = mock.patch.object(linker, "candidates",
                                 side_effect=linker.candidates)
        return dis, cand

    def test_a_skipped_note_is_neither_scanned_nor_disambiguated(self):
        self.standard_vault()
        rc, out = self._main()
        self.assertEqual(rc, 0, out)
        dis, cand = self._spies()
        with dis as dis_spy, cand as cand_spy:
            rc, out = self._main()
        self.assertEqual(rc, 0, out)
        self.assertIn("notes_skipped_unchanged: 2", out)
        self.assertEqual(cand_spy.call_count, 0,
                         "skipped notes were candidate-scanned again")
        self.assertEqual(dis_spy.call_count, 0,
                         "skipped notes were disambiguated again")

    def test_a_tampered_block_forces_the_recompute_and_the_repair(self):
        self.standard_vault()
        rc, out = self._main()
        self.assertEqual(rc, 0, out)
        path = os.path.join(self.vault, "RES-01.md")
        edited = self.read("RES-01.md").replace("src/parser.py:L4",
                                                "src/parser.py:L999")
        open(path, "w").write(edited)
        dis, _cand = self._spies()
        with dis as dis_spy:
            rc, out = self._main()
        self.assertEqual(rc, 0, out)
        self.assertGreater(dis_spy.call_count, 0,
                           "the mismatch did not fall back to a recompute")
        self.assertIn("src/parser.py:L4", self.read("RES-01.md"))
        self.assertNotIn("L999", self.read("RES-01.md"))


class SkippingNeverHidesAWarning(_VaultCase):
    def test_an_ambiguous_note_is_reanalysed_every_run(self):
        """A note the linker refused to link is a warning, and warnings must
        survive the second run: the ambiguous section of CODE-INDEX and the
        AMBIGUOUS lines are rebuilt from analysis, so the note is never
        marked skippable."""
        self.note("RES-01", "`validate` is wrong.")
        self.symbols({"validate": [
            {"path": "src/users.py", "line": 3, "kind": "py", "qualified_name": None},
            {"path": "src/payments.py", "line": 7, "kind": "py", "qualified_name": None}]})
        self.link()
        index_before = self.read("CODE-INDEX.md")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("AMBIGUOUS validate", r2.stdout)
        self.assertEqual(self.read("CODE-INDEX.md"), index_before)
        self.assertIn("## Ambiguous references", self.read("CODE-INDEX.md"))

    def test_a_skipped_unlinked_note_is_still_reported_unlinked(self):
        self.note("RES-01", "prose that names no symbol at all.")
        self.note("RES-02", "`parse_payload` returns {}.")
        self.symbols({"parse_payload": "src/parser.py:L4"})
        self.link()
        r2 = self.link("--require-linked")
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "unlinked_notes"), 1)


if __name__ == "__main__":
    unittest.main()
