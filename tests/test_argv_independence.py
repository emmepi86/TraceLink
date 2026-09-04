"""ms-1: the CLI must not depend on — or touch — process global state.

Three properties, each one a way the old dispatch could come back:

  1. no module writes `sys.argv`, ever, not even temporarily (static check
     over the shipped sources, so a future `saved = sys.argv` is caught in
     the file that introduces it, not in the behaviour of some caller);
  2. every module's `main(argv)` parses the list it is given and leaves the
     process's own argv byte-for-byte identical;
  3. a module is really callable in-process — `mod.main([...])` works on its
     own, not only through the dispatcher — and the `prog` shown in usage is
     the one the caller asked for, so `tracelink lint --help` keeps saying
     "tracelink lint".
"""

import contextlib
import io
import os
import re
import sys
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from tracelink import (cli, hook, lint, linker, splitter,  # noqa: E402
                       status, symbol_index)

MODULES = {
    "split": splitter,
    "index": symbol_index,
    "link": linker,
    "status": status,
    "lint": lint,
    "hook": hook,
}

#: A value nothing in the tree has any business reading, let alone writing.
SENTINEL = ["SENTINEL-argv-0", "--sentinel-flag", "sentinel-positional"]

ASSIGNS_ARGV = re.compile(r"^\s*sys\.argv\s*(=[^=]|\+=)", re.M)


def _sources():
    for base in (os.path.join(ROOT, "src", "tracelink"),
                 os.path.join(ROOT, "scripts")):
        for name in sorted(os.listdir(base)):
            if name.endswith(".py"):
                yield os.path.join(base, name)


class NoGlobalArgvWrites(unittest.TestCase):
    def test_no_source_file_assigns_sys_argv(self):
        offenders = []
        for path in _sources():
            with open(path, encoding="utf-8") as fh:
                if ASSIGNS_ARGV.search(fh.read()):
                    offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual([], offenders,
                         "these files still write the process argv")


class ArgvIsUntouched(unittest.TestCase):
    """Every entry point leaves `sys.argv` exactly as it found it."""

    def setUp(self):
        self._saved = sys.argv
        sys.argv = list(SENTINEL)

    def tearDown(self):
        sys.argv = self._saved

    def _help(self, call):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                call()
        self.assertEqual(0, raised.exception.code)
        return out.getvalue()

    def test_every_module_main_leaves_argv_alone(self):
        for name, module in MODULES.items():
            with self.subTest(command=name):
                before = sys.argv
                text = self._help(lambda m=module: m.main(["--help"]))
                self.assertIn("usage:", text)
                self.assertIs(before, sys.argv, "argv object was replaced")
                self.assertEqual(SENTINEL, sys.argv)

    def test_dispatcher_leaves_argv_alone(self):
        for name in MODULES:
            with self.subTest(command=name):
                before = sys.argv
                self._help(lambda n=name: cli.main([n, "--help"]))
                self.assertIs(before, sys.argv, "argv object was replaced")
                self.assertEqual(SENTINEL, sys.argv)

    def test_top_level_help_and_version_leave_argv_alone(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, cli.main([]))
            self.assertEqual(0, cli.main(["--version"]))
        self.assertEqual(SENTINEL, sys.argv)


class CallableInProcess(unittest.TestCase):
    """A module is usable as a library, not only behind the dispatcher."""

    def _usage(self, call):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                call()
        return out.getvalue()

    def test_main_parses_the_list_it_is_given_not_the_process_argv(self):
        # A process argv that WOULD be rejected by the parser: if any module
        # read it instead of its argument, argparse would exit non-zero.
        with unittest.mock.patch.object(
                sys, "argv", ["x", "--this-flag-does-not-exist"]):
            for name, module in MODULES.items():
                with self.subTest(command=name):
                    text = self._usage(lambda m=module: m.main(["--help"]))
                    self.assertIn("usage:", text)

    def test_an_empty_argv_is_not_a_missing_one(self):
        """`main([])` must parse nothing — not fall back to the process argv.

        The None-versus-empty confusion is the classic way this refactor
        regresses: a truthiness test instead of an `is None` test makes an
        explicit empty list silently mean "read sys.argv", which is exactly
        the coupling this ministep removes. `lint` requires --register, so
        an empty argv must fail with argparse's own exit 2 even when the
        process argv would have satisfied it.
        """
        with unittest.mock.patch.object(
                sys, "argv", ["x", "--register", "whatever.md"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as raised:
                    lint.main([])
            self.assertEqual(2, raised.exception.code)
            self.assertIn("--register", err.getvalue())

    def test_prog_is_the_callers_choice(self):
        for name, module in MODULES.items():
            with self.subTest(command=name):
                text = self._usage(
                    lambda m=module, n=name: m.main(["--help"],
                                                    prog=f"tracelink {n}"))
                self.assertTrue(text.startswith(f"usage: tracelink {name}"),
                                f"usage line was {text.splitlines()[0]!r}")

    def test_dispatcher_still_names_the_subcommand_in_usage(self):
        for name in MODULES:
            with self.subTest(command=name):
                text = self._usage(lambda n=name: cli.main([n, "--help"]))
                self.assertTrue(text.startswith(f"usage: tracelink {name}"),
                                f"usage line was {text.splitlines()[0]!r}")


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
