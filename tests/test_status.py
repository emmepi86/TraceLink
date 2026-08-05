"""`tracelink status` reports health without changing anything.

Every fact it prints is either recomputed from the inputs (register ids via
the splitter, index freshness via the linker's verifier) or read from state
another command already wrote (the vault manifest, the link-state sidecar).
Where the recorded state cannot answer — a note edited after the last link,
a reason the state never stored — the report must say "unknown (run link)"
rather than quietly re-running the linking to find out. Exit code 0 is
informational; --strict turns any problem into exit 1.

    python3 -m unittest discover tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

REGISTER = """# Findings

## RES-01 — parser drops empty payload

### STATUS: OPEN — SEVERITY: HIGH

`parse_payload` returns {} for an empty body.

## RES-02 — batch retry loop

### STATUS: CLOSED

`ingest_batch` retries forever on 500.
"""

NEW_FINDING = """
## RES-03 — temp files left behind

### STATUS: OPEN

The cleanup step never runs on the error path.
"""


def _run(module_main, argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", list(argv)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = module_main()
    return rc, out.getvalue(), err.getvalue()


class _StatusCase(unittest.TestCase):
    """A real pipeline — repo, index, split, link — then status over it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.repo = os.path.join(base, "repo")
        os.makedirs(os.path.join(self.repo, "src"))
        open(os.path.join(self.repo, "src", "parser.py"), "w").write(
            "def parse_payload(body):\n    return {}\n")
        open(os.path.join(self.repo, "src", "ingest.py"), "w").write(
            "def ingest_batch(rows):\n    pass\n")
        self.register = os.path.join(base, "FINDINGS.md")
        open(self.register, "w").write(REGISTER)
        self.vault = os.path.join(base, "vault")
        self.syms = os.path.join(base, "symbols.json")

    def index(self):
        from tracelink import symbol_index
        rc, out, err = _run(symbol_index.main,
                            ["index", "--repo", self.repo, "--backend", "scan",
                             "--out", self.syms])
        self.assertEqual(rc, 0, out + err)

    def split(self):
        from tracelink import splitter
        rc, out, err = _run(splitter.main,
                            ["split", "--register", self.register,
                             "--out", self.vault, "--prefix", "RES"])
        self.assertEqual(rc, 0, out + err)

    def link(self):
        from tracelink import linker
        rc, out, err = _run(linker.main,
                            ["link", "--vault", self.vault,
                             "--symbols", self.syms, "--repo", self.repo])
        self.assertEqual(rc, 0, out + err)

    def pipeline(self):
        self.index()
        self.split()
        self.link()

    def status(self, *extra):
        from tracelink import status
        return _run(status.main,
                    ["status", "--register", self.register,
                     "--vault", self.vault, "--symbols", self.syms,
                     "--repo", self.repo, *extra])

    def status_json(self, *extra):
        rc, out, err = self.status("--format", "json", *extra)
        return rc, json.loads(out), err


class AHealthyVaultReportsOkWithNoProblems(_StatusCase):
    def test_aligned_vault_and_fresh_index_is_ok(self):
        self.pipeline()
        rc, payload, err = self.status_json()
        self.assertEqual(rc, 0, err)
        self.assertEqual(payload["problems"], [])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["index"]["freshness"], "fresh")
        self.assertTrue(payload["links"]["symbols_fingerprint_matches"])
        self.assertEqual(payload["links"]["unlinked_count"], 0)
        self.assertEqual(payload["register"]["count"], 2)
        self.assertEqual(payload["vault"]["missing_in_vault"], [])
        self.assertEqual(payload["vault"]["extra_in_vault"], [])

    def test_strict_exits_zero_when_healthy(self):
        self.pipeline()
        rc, out, err = self.status("--strict")
        self.assertEqual(rc, 0, out + err)

    def test_text_report_states_one_fact_per_line(self):
        self.pipeline()
        rc, out, err = self.status()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("index_freshness:", out)
        self.assertIn("fresh", out)
        self.assertIn("ok:", out)


class AnUnsplitFindingIsAProblem(_StatusCase):
    def test_a_new_register_entry_is_reported_missing_from_the_vault(self):
        self.pipeline()
        open(self.register, "a").write(NEW_FINDING)
        rc, payload, err = self.status_json()
        self.assertEqual(rc, 0, err)  # informational by default
        self.assertIn("RES-03", payload["vault"]["missing_in_vault"])
        self.assertFalse(payload["ok"])
        self.assertTrue(any("split" in p for p in payload["problems"]),
                        payload["problems"])

    def test_strict_turns_the_misalignment_into_exit_1(self):
        self.pipeline()
        open(self.register, "a").write(NEW_FINDING)
        rc, _out, _err = self.status("--strict")
        self.assertEqual(rc, 1)


class SymbolsChangedAfterTheLastLink(_StatusCase):
    def test_the_links_section_reports_the_fingerprint_mismatch(self):
        self.pipeline()
        # Same payload, different bytes: the link-state fingerprints the file
        # it read, so this is exactly "symbols.json changed since the link".
        payload = json.load(open(self.syms))
        json.dump(payload, open(self.syms, "w"), indent=2)
        rc, report, err = self.status_json()
        self.assertEqual(rc, 0, err)
        self.assertIs(report["links"]["symbols_fingerprint_matches"], False)
        self.assertTrue(any("link" in p for p in report["problems"]),
                        report["problems"])
        rc, _out, _err = self.status("--strict")
        self.assertEqual(rc, 1)


class OpenHighFindingsAreSurfaced(_StatusCase):
    def test_an_open_high_note_appears_with_id_and_title(self):
        self.pipeline()
        rc, payload, err = self.status_json()
        self.assertEqual(rc, 0, err)
        self.assertEqual([f["id"] for f in payload["findings"]["open_high"]],
                         ["RES-01"])
        item = payload["findings"]["open_high"][0]
        self.assertEqual(item["severity"], "high")
        self.assertIn("parser drops empty payload", item["title"])
        self.assertEqual(payload["findings"]["by_status"],
                         {"open": 1, "closed": 1})


class TheJsonReportIsPureAndStable(_StatusCase):
    def test_top_level_keys_are_exactly_the_documented_ones(self):
        self.pipeline()
        rc, out, err = self.status("--format", "json")
        self.assertEqual(rc, 0, err)
        payload = json.loads(out)  # raises if any prose is mixed in
        self.assertEqual(sorted(payload),
                         ["findings", "index", "links", "ok",
                          "problems", "register", "vault"])

    def test_a_problem_run_still_emits_pure_json(self):
        self.pipeline()
        open(self.register, "a").write(NEW_FINDING)
        rc, out, _err = self.status("--format", "json", "--strict")
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])


class MissingInputsFailPolitelyNotWithATraceback(_StatusCase):
    def test_an_absent_vault_is_a_clear_message(self):
        self.index()
        rc, out, err = self.status()
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("Traceback", out + err)
        self.assertIn("run split", out)

    def test_an_absent_manifest_is_a_clear_message(self):
        self.index()
        os.mkdir(self.vault)
        rc, out, err = self.status()
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("Traceback", out + err)
        self.assertIn("manifest", out.lower())

    def test_an_absent_symbols_file_is_a_clear_message(self):
        self.split()
        rc, out, err = self.status()
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("Traceback", out + err)
        self.assertIn("run index", out)

    def test_strict_makes_missing_inputs_exit_1(self):
        rc, _out, _err = self.status("--strict")
        self.assertEqual(rc, 1)


class UnknownIsSaidOutLoudNotEstimated(_StatusCase):
    def test_a_note_edited_after_linking_makes_unlinked_unknown(self):
        """The state cannot vouch for a note it has not seen. Recomputing the
        answer would mean re-running the linking, which status must not do —
        the honest report is "unknown (run link)"."""
        self.pipeline()
        path = os.path.join(self.vault, "RES-01.md")
        text = open(path).read().replace(
            "returns {} for an empty body",
            "now also names `mystery_helper` in passing")
        open(path, "w").write(text)
        rc, payload, err = self.status_json()
        self.assertEqual(rc, 0, err)
        self.assertEqual(payload["links"]["unlinked_count"],
                         "unknown (run link)")
        self.assertFalse(payload["ok"])


class TheCliDispatchesStatus(_StatusCase):
    def test_tracelink_status_reaches_the_module(self):
        from tracelink import cli
        self.pipeline()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", list(sys.argv)), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["status", "--register", self.register,
                           "--vault", self.vault, "--symbols", self.syms,
                           "--repo", self.repo, "--format", "json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
