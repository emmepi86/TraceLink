"""ms-11e (O2): with `--json`, stdout is one JSON document. Every command.

ms-4 fixed this for `consult`: the first consumer of these commands is
another program, so a line of prose on stdout is a parse error rather than a
message. Writing the ms-10 fixtures turned up `link --format json` printing
`no notes in <path>` on an empty vault — the rule had been applied where it
was written, not everywhere it was promised.

So this asserts it across every command that offers a machine-readable mode,
on the success path *and* on the failure paths, because the failure path is
the one a CI job is reading when something has gone wrong. Human words go to
stderr; anything the caller needs goes inside the document.
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

from tracelink import cli, splitter, symbol_index  # noqa: E402

REGISTER = ("# Findings\n\n## J-01 — totals ignore tax [HIGH]\n"
            "### STATUS: OPEN\n\n`compute_total` never applies tax.\n")


def project(tmp, register=REGISTER, with_vault=True, with_index=True):
    root = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "app.py"), "w") as fh:
        fh.write("def compute_total(items):\n    return sum(items)\n")
    with open(os.path.join(root, "FINDINGS.md"), "w") as fh:
        fh.write(register)
    tl = os.path.join(root, ".tracelink")
    os.makedirs(tl, exist_ok=True)
    symbols = os.path.join(tl, "symbols.json")
    vault = os.path.join(tl, "vault")
    quiet = contextlib.redirect_stdout(io.StringIO())
    if with_index:
        with quiet, contextlib.redirect_stderr(io.StringIO()):
            symbol_index.main(["--repo", root, "--backend", "scan",
                               "--out", symbols])
    if with_vault:
        with contextlib.redirect_stdout(io.StringIO()):
            splitter.main(["--register", os.path.join(root, "FINDINGS.md"),
                           "--out", vault, "--prefix", "J"])
    return root, vault, symbols


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class StdoutIsOneDocument(unittest.TestCase):
    """One assertion, applied to every command and every path."""

    def assert_json_only(self, argv, label):
        code, out, _err = run(argv)
        with self.subTest(case=label):
            self.assertTrue(out.strip(),
                            f"{label}: nothing on stdout at all")
            try:
                json.loads(out)
            except ValueError as exc:
                self.fail(f"{label} (exit {code}) put non-JSON on stdout: "
                          f"{exc}\n{out[:300]}")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_success_path_of_every_command(self):
        root, vault, symbols = project(self.tmp.name)
        run(["link", "--vault", vault, "--symbols", symbols, "--repo", root])
        register = os.path.join(root, "FINDINGS.md")
        for label, argv in (
                ("link", ["link", "--vault", vault, "--symbols", symbols,
                          "--repo", root, "--format", "json"]),
                ("status", ["status", "--register", register, "--vault",
                            vault, "--symbols", symbols, "--repo", root,
                            "--format", "json"]),
                ("lint", ["lint", "--register", register, "--format",
                          "json"]),
                ("consult", ["consult", "src/app.py", "--repo", root,
                             "--vault", vault, "--json"]),
                ("explain", ["explain", "J-01", "--repo", root, "--vault",
                             vault, "--json"]),
                ("sync", ["sync", "--repo", root, "--json"]),
                ("init", ["init", "--repo", root, "--json"]),
                ("doctor", ["doctor", "--repo", root, "--json"])):
            self.assert_json_only(argv, label)

    def test_an_empty_vault_is_still_a_document(self):
        """The case that started this: `no notes in <path>` on stdout."""
        root, _vault, symbols = project(self.tmp.name, with_vault=False)
        empty = os.path.join(root, ".tracelink", "empty-vault")
        os.makedirs(empty)
        self.assert_json_only(["link", "--vault", empty, "--symbols", symbols,
                               "--repo", root, "--format", "json"],
                              "link on an empty vault")

    def test_the_failure_paths(self):
        root, vault, symbols = project(self.tmp.name)
        run(["link", "--vault", vault, "--symbols", symbols, "--repo", root])
        register = os.path.join(root, "FINDINGS.md")
        missing = os.path.join(root, "nowhere.json")
        for label, argv in (
                ("consult, no such file", ["consult", "src/ghost.py",
                                           "--repo", root, "--vault", vault,
                                           "--json"]),
                ("consult, no state", ["consult", "src/app.py", "--repo",
                                       root, "--vault", missing, "--json"]),
                ("explain, unknown finding", ["explain", "J-99", "--repo",
                                              root, "--vault", vault,
                                              "--json"]),
                ("explain, no state", ["explain", "J-01", "--repo", root,
                                       "--vault", missing, "--json"]),
                ("sync, check out of date", ["sync", "--repo", root,
                                             "--check", "--json"]),
                ("doctor, broken config", ["doctor", "--repo", root,
                                           "--json"])):
            self.assert_json_only(argv, label)

    def test_link_refusing_an_index_still_answers_in_json(self):
        root, vault, symbols = project(self.tmp.name)
        with open(symbols, "w") as fh:
            fh.write("{}")
        self.assert_json_only(["link", "--vault", vault, "--symbols", symbols,
                               "--repo", root, "--format", "json"],
                              "link with an unusable index")


class HumanWordsGoToStderr(unittest.TestCase):
    def test_the_empty_vault_message_is_not_lost_only_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _vault, symbols = project(tmp, with_vault=False)
            empty = os.path.join(root, ".tracelink", "empty-vault")
            os.makedirs(empty)
            code, out, err = run(["link", "--vault", empty, "--symbols",
                                  symbols, "--repo", root, "--format",
                                  "json"])
            self.assertEqual(1, code)
            self.assertIn("no notes", err)
            document = json.loads(out)
            self.assertFalse(document["ok"])
            self.assertEqual("no-notes", document["exit_reason"])

    def test_text_mode_keeps_saying_it_on_stdout(self):
        """Nothing was taken away from the human form."""
        with tempfile.TemporaryDirectory() as tmp:
            root, _vault, symbols = project(tmp, with_vault=False)
            empty = os.path.join(root, ".tracelink", "empty-vault")
            os.makedirs(empty)
            _code, out, _err = run(["link", "--vault", empty, "--symbols",
                                    symbols, "--repo", root])
            self.assertIn("no notes", out)


if __name__ == "__main__":
    unittest.main()
