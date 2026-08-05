"""0.7.0 — `tracelink lint`: a read-only quality gate over the register.

Four rules, each catching a way a finding fails its future reader:

  prose-only        no linkable identifier at all — the linker will connect
                    it to nothing (detection reused from the linker itself)
  unknown-symbols   with --symbols: deliberate identifiers the index has
                    never heard of — typos and renamed code. 0.7.1: warns
                    only when the finding cites NO known symbol; alongside
                    known ones the unknowns are INFO lines (`infos` in the
                    JSON), because on the real benchmark 9/10 linked notes
                    were warning over properties and config keys
  duplicate         with --vault: a title that normalises to an existing
                    note's title — the same discovery recorded twice
  missing-status /  the explicit `### STATUS:` / severity grammar the
  missing-severity  splitter documents is absent

`--new-only` restricts the check to findings the vault manifest has not seen,
which is what the capture prompt tells the agent to run. Exit 0 with zero
warnings, 1 otherwise — lint IS the gate, no --strict needed. The register is
never written.

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

from tracelink import lint  # noqa: E402


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["tracelink lint"] + list(argv)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = lint.main()
    return rc, out.getvalue(), err.getvalue()


GOOD = ("# Findings\n\n"
        "## RES-01 — totals ignore tax [HIGH]\n"
        "### STATUS: OPEN\n"
        "`compute_total` in `src/app.py` sums line items but never "
        "applies tax.\n")

PROSE_ONLY = ("# Findings\n\n"
              "## RES-01 — negations mishandled [HIGH]\n"
              "### STATUS: OPEN\n"
              "The narrative pipeline mishandles negations everywhere.\n")


def note_md(note_id, title, severity="high"):
    return (f"---\ntracelink_schema: 1\ntracelink_id: {note_id}\n"
            f"id: {note_id}\nstatus: open\nseverity: {severity}\n"
            f"sections: 1\n---\n\n"
            f"# {note_id} — {title} [{severity.upper()}]\n\nBody.\n")


class _LintCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = self._tmp.name
        self.register = os.path.join(self.base, "FINDINGS.md")

    def write_register(self, text):
        with open(self.register, "w") as fh:
            fh.write(text)

    def write_symbols(self, names):
        path = os.path.join(self.base, "symbols.json")
        with open(path, "w") as fh:
            json.dump({"symbols": {n: [{"path": "src/app.py", "line": 1,
                                        "kind": "function",
                                        "qualified_name": n}]
                                   for n in names}}, fh)
        return path

    def write_vault(self, notes, prefix="RES"):
        """notes: [(id, title)] — a vault exactly as split leaves it."""
        vault = os.path.join(self.base, "vault")
        os.makedirs(vault, exist_ok=True)
        for note_id, title in notes:
            with open(os.path.join(vault, note_id + ".md"), "w") as fh:
                fh.write(note_md(note_id, title))
        with open(os.path.join(vault, ".tracelink-manifest.json"), "w") as fh:
            json.dump({"schema_version": 2,
                       "register": {"prefix": prefix,
                                    "source": "FINDINGS.md"},
                       "generated_from": "FINDINGS.md",
                       "generated_notes": [n + ".md" for n, _ in notes]}, fh)
        return vault

    def codes_for(self, out_json, fid=None):
        return [w["code"] for w in out_json["warnings"]
                if fid is None or w["id"] == fid]


class TestProseOnly(_LintCase):
    def test_clean_finding_passes(self):
        self.write_register(GOOD)
        rc, out, err = _run(["--register", self.register])
        self.assertEqual(rc, 0, out + err)

    def test_prose_only_finding_warns(self):
        self.write_register(PROSE_ONLY)
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertIn("prose-only", self.codes_for(report, "RES-01"))

    def test_camel_case_symbol_counts_as_identifier(self):
        self.write_register("## RES-01 — double charge [HIGH]\n"
                            "### STATUS: OPEN\n"
                            "PaymentProcessor charges twice on retry.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertNotIn("prose-only", self.codes_for(json.loads(out)))

    def test_sentence_case_word_is_not_an_identifier(self):
        """`Negations` at the start of a sentence is prose, not CamelCase."""
        self.write_register("## RES-01 — negations [HIGH]\n"
                            "### STATUS: OPEN\n"
                            "Negations are mishandled. Everything breaks.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertIn("prose-only", self.codes_for(json.loads(out)))

    def test_snake_case_outside_backticks_counts(self):
        self.write_register("## RES-01 — tax [HIGH]\n### STATUS: OPEN\n"
                            "compute_total never applies tax.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertNotIn("prose-only", self.codes_for(json.loads(out)))

    def test_known_symbol_via_index_counts(self):
        """With --symbols the linker's own candidate detection decides: a
        bare word the index knows is linkable even without an underscore."""
        self.write_register("## RES-01 — sanitize [HIGH]\n### STATUS: OPEN\n"
                            "sanitise is skipped on the error path.\n")
        symbols = self.write_symbols(["sanitise"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertNotIn("prose-only", self.codes_for(json.loads(out)))


class TestUnknownSymbols(_LintCase):
    def test_unknown_identifier_warns_with_the_name(self):
        self.write_register("## RES-01 — ghost [HIGH]\n### STATUS: OPEN\n"
                            "`made_up_helper` corrupts the cache.\n")
        symbols = self.write_symbols(["compute_total"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertIn("unknown-symbols", self.codes_for(report, "RES-01"))
        detail = [w["detail"] for w in report["warnings"]
                  if w["code"] == "unknown-symbols"][0]
        self.assertIn("made_up_helper", detail)

    def test_known_identifier_does_not_warn(self):
        self.write_register(GOOD)
        symbols = self.write_symbols(["compute_total"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertNotIn("unknown-symbols", self.codes_for(json.loads(out)))

    def test_paths_in_backticks_are_not_symbols(self):
        """`src/app.py` and `FINDINGS.md` must not be reported as unknown —
        a path's extension is not a dotted name (same rule as the linker)."""
        self.write_register("## RES-01 — tax [HIGH]\n### STATUS: OPEN\n"
                            "`compute_total` in `src/app.py` (see "
                            "`FINDINGS.md`) ignores tax.\n")
        symbols = self.write_symbols(["compute_total"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertNotIn("unknown-symbols", self.codes_for(json.loads(out)))

    def test_no_symbols_flag_means_no_unknown_rule(self):
        self.write_register("## RES-01 — ghost [HIGH]\n### STATUS: OPEN\n"
                            "`made_up_helper` corrupts the cache.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertNotIn("unknown-symbols", self.codes_for(json.loads(out)))


HUB = ("## RES-01 — hub config [HIGH]\n### STATUS: OPEN\n"
       "`getConfig` feeds `mintToken`, but `waitForStartMs` and `qrTtlSec` "
       "are read before the hub config exists.\n")


class TestUnknownSymbolsDemotedToInfo(_LintCase):
    """Benchmark evidence (Todi): 9/10 real notes warned unknown-symbols
    while linking perfectly — properties, env vars and config keys in
    backticks are physiological. With at least one KNOWN symbol cited, the
    unknowns must inform, not gate."""

    def test_unknowns_beside_known_symbols_are_info_not_warnings(self):
        self.write_register(HUB)
        symbols = self.write_symbols(["getConfig", "mintToken"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertEqual(rc, 0, out)
        report = json.loads(out)
        self.assertEqual(report["warnings"], [])
        infos = report["infos"]
        self.assertEqual(len(infos), 2)
        for info in infos:
            self.assertEqual(set(info), {"id", "code", "detail"})
            self.assertEqual(info["id"], "RES-01")
            self.assertEqual(info["code"], "unknown-symbols")
        blob = " ".join(i["detail"] for i in infos)
        self.assertIn("waitForStartMs", blob)
        self.assertIn("qrTtlSec", blob)

    def test_only_unknowns_still_warns_and_gates(self):
        self.write_register("## RES-01 — ghost [HIGH]\n### STATUS: OPEN\n"
                            "`made_up_helper` corrupts the cache.\n")
        symbols = self.write_symbols(["compute_total"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertIn("unknown-symbols", self.codes_for(report, "RES-01"))
        self.assertEqual(report["infos"], [])

    def test_all_known_symbols_yield_neither_warning_nor_info(self):
        self.write_register(GOOD)
        symbols = self.write_symbols(["compute_total"])
        rc, out, _ = _run(["--register", self.register,
                           "--symbols", symbols, "--format", "json"])
        report = json.loads(out)
        self.assertNotIn("unknown-symbols", self.codes_for(report))
        self.assertEqual(report["infos"], [])

    def test_text_output_prints_info_lines_and_exits_zero(self):
        self.write_register(HUB)
        symbols = self.write_symbols(["getConfig", "mintToken"])
        rc, out, _ = _run(["--register", self.register, "--symbols", symbols])
        self.assertEqual(rc, 0, out)
        info_lines = [l for l in out.splitlines() if l.startswith("INFO ")]
        self.assertEqual(len(info_lines), 2)
        for line in info_lines:
            self.assertIn("RES-01", line)
            self.assertIn("[unknown-symbols]", line)
        self.assertFalse([l for l in out.splitlines()
                          if l.startswith("WARN ")])

    def test_infos_key_is_present_even_without_symbols(self):
        self.write_register(GOOD)
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        report = json.loads(out)
        self.assertIn("infos", report)
        self.assertEqual(report["infos"], [])


class TestDuplicates(_LintCase):
    def test_same_title_as_an_existing_note_warns(self):
        self.write_register("## RES-02 — Totals ignore TAX! [HIGH]\n"
                            "### STATUS: OPEN\n"
                            "`compute_total` drops tax again.\n")
        vault = self.write_vault([("RES-01", "totals ignore tax")])
        rc, out, _ = _run(["--register", self.register,
                           "--vault", vault, "--format", "json"])
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertIn("duplicate", self.codes_for(report, "RES-02"))
        detail = [w["detail"] for w in report["warnings"]
                  if w["code"] == "duplicate"][0]
        self.assertIn("RES-01", detail)

    def test_a_finding_is_not_its_own_duplicate(self):
        self.write_register(GOOD)
        vault = self.write_vault([("RES-01", "totals ignore tax")])
        rc, out, _ = _run(["--register", self.register,
                           "--vault", vault, "--format", "json"])
        self.assertNotIn("duplicate", self.codes_for(json.loads(out)))

    def test_different_titles_do_not_warn(self):
        self.write_register(GOOD)
        vault = self.write_vault([("RES-02", "retry loop never ends")])
        rc, out, _ = _run(["--register", self.register,
                           "--vault", vault, "--format", "json"])
        self.assertNotIn("duplicate", self.codes_for(json.loads(out)))


class TestMetadataHeadings(_LintCase):
    def test_missing_status_and_severity_both_warn(self):
        self.write_register("## RES-01 — totals ignore tax\n"
                            "`compute_total` drops tax.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        self.assertEqual(rc, 1)
        codes = self.codes_for(json.loads(out), "RES-01")
        self.assertIn("missing-status", codes)
        self.assertIn("missing-severity", codes)

    def test_bracket_severity_in_the_heading_suffices(self):
        self.write_register(GOOD)  # [HIGH] tag, ### STATUS: line
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        codes = self.codes_for(json.loads(out))
        self.assertNotIn("missing-status", codes)
        self.assertNotIn("missing-severity", codes)

    def test_explicit_severity_heading_suffices(self):
        self.write_register("## RES-01 — totals ignore tax\n"
                            "### STATUS: OPEN — SEVERITY: HIGH\n"
                            "`compute_total` drops tax.\n")
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        codes = self.codes_for(json.loads(out))
        self.assertNotIn("missing-status", codes)
        self.assertNotIn("missing-severity", codes)


class TestNewOnly(_LintCase):
    def test_only_unsplit_findings_are_checked(self):
        self.write_register(PROSE_ONLY +
                            "\n## RES-02 — also just prose\n"
                            "Nothing concrete here either.\n")
        vault = self.write_vault([("RES-01", "negations mishandled")])
        rc, out, _ = _run(["--register", self.register, "--vault", vault,
                           "--new-only", "--format", "json"])
        report = json.loads(out)
        self.assertEqual(report["findings_checked"], 1)
        self.assertEqual({w["id"] for w in report["warnings"]}, {"RES-02"})

    def test_new_only_requires_a_vault(self):
        self.write_register(GOOD)
        rc, out, err = _run(["--register", self.register, "--new-only"])
        self.assertEqual(rc, 2)
        self.assertIn("--vault", err)


class TestPrefixResolution(_LintCase):
    def test_prefix_comes_from_the_vault_manifest(self):
        self.write_register("## BUG-1 — totals ignore tax [HIGH]\n"
                            "### STATUS: OPEN\n`compute_total` drops tax.\n")
        vault = self.write_vault([], prefix="BUG")
        rc, out, _ = _run(["--register", self.register,
                           "--vault", vault, "--format", "json"])
        self.assertEqual(json.loads(out)["findings_checked"], 1)

    def test_explicit_prefix_wins(self):
        self.write_register("## BUG-1 — totals ignore tax [HIGH]\n"
                            "### STATUS: OPEN\n`compute_total` drops tax.\n")
        rc, out, _ = _run(["--register", self.register,
                           "--prefix", "BUG", "--format", "json"])
        self.assertEqual(json.loads(out)["findings_checked"], 1)


class TestOutputAndExit(_LintCase):
    def test_json_is_pure_and_shaped(self):
        self.write_register(PROSE_ONLY)
        rc, out, _ = _run(["--register", self.register, "--format", "json"])
        report = json.loads(out)  # the WHOLE stdout must parse
        self.assertEqual(set(report), {"findings_checked", "warnings",
                                       "infos"})
        self.assertEqual(report["findings_checked"], 1)
        for w in report["warnings"] + report["infos"]:
            self.assertEqual(set(w), {"id", "code", "detail"})

    def test_text_prints_one_line_per_warning(self):
        self.write_register(PROSE_ONLY)
        rc, out, _ = _run(["--register", self.register])
        self.assertEqual(rc, 1)
        warn_lines = [l for l in out.splitlines() if l.startswith("WARN ")]
        self.assertEqual(len(warn_lines), 1)
        self.assertIn("RES-01", warn_lines[0])
        self.assertIn("prose-only", warn_lines[0])

    def test_zero_warnings_exit_zero(self):
        self.write_register(GOOD)
        rc, _, _ = _run(["--register", self.register])
        self.assertEqual(rc, 0)

    def test_missing_register_exits_two(self):
        rc, out, err = _run(["--register",
                             os.path.join(self.base, "nope.md")])
        self.assertEqual(rc, 2)
        self.assertIn("nope.md", err)


class TestLintNeverWrites(_LintCase):
    def test_register_and_vault_bytes_are_untouched(self):
        self.write_register(PROSE_ONLY)
        vault = self.write_vault([("RES-09", "negations mishandled")])
        before = {self.register: read_bytes(self.register)}
        for name in os.listdir(vault):
            p = os.path.join(vault, name)
            before[p] = read_bytes(p)
        symbols = self.write_symbols(["compute_total"])
        _run(["--register", self.register, "--vault", vault,
              "--symbols", symbols, "--format", "json"])
        after_names = {self.register} | {os.path.join(vault, n)
                                         for n in os.listdir(vault)}
        self.assertEqual(after_names, set(before), "lint created files")
        for path, blob in before.items():
            self.assertEqual(read_bytes(path), blob, path)


class TestCliIntegration(_LintCase):
    def test_tracelink_lint_is_a_command(self):
        self.write_register(GOOD)
        from tracelink import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["lint", "--register", self.register])
        self.assertEqual(rc, 0, out.getvalue() + err.getvalue())

    def test_lint_appears_in_the_usage(self):
        from tracelink import cli
        self.assertIn("lint", cli._COMMANDS)
        self.assertIn("lint", cli._USAGE)


if __name__ == "__main__":
    unittest.main()
