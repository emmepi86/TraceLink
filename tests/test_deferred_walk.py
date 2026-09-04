"""ms-11d: the repository walk happens when it is needed, and not before.

Benchmark 02 (F2) measured `link`'s floor: 0.375 s at 2 700 files, 7.06 s at
50 000, *before a single note was considered*, and paid identically by
vaults that anchor nothing to a path. The walk builds the file map that file
references resolve against — necessary work when a note names a file, and
pure waste when none does.

So it is deferred, not removed and not approximated. The map is built on the
first reference in a run, from the real tree, with the same exclusions; a
vault with one reference pays exactly what it paid before.

**What was deliberately not done.** A reference is ambiguous when the
repository holds two files matching it, and finding that out is what the
full map is for. Checking one likely path instead — `infra/config.yml`
exists, therefore it is the match — would turn an `AMBIGUOUS` into a
confident `MATCH`, which is a worse failure than being slow. There is no
fast path here that skips looking for duplicates, no `git ls-files`
narrowing the candidate set, and no mtime cache: those change what
resolution *means*, and this ministep is about when work happens, not what
it concludes.
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
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import consult as consult_mod  # noqa: E402
from tracelink import linker, splitter, symbol_index  # noqa: E402

SYMBOL_ONLY = ("# Findings\n\n"
               "## W-01 — totals ignore tax [HIGH]\n### STATUS: OPEN\n\n"
               "`compute_total` never applies tax.\n\n"
               "## W-02 — retries duplicate [MEDIUM]\n### STATUS: OPEN\n\n"
               "`retry_send` can deliver twice.\n")

WITH_FILE_REF = SYMBOL_ONLY + (
    "\n## W-03 — the compose file wedges [MEDIUM]\n### STATUS: OPEN\n\n"
    "`infra/compose.yml` starts migrate before the database is up.\n")

CODE = {"src/app.py": "def compute_total(items):\n    return sum(items)\n",
        "src/send.py": "def retry_send(msg):\n    return msg\n",
        "infra/compose.yml": "services: {}\n"}


class Project:
    def __init__(self, tmp, register=SYMBOL_ONLY, code=CODE):
        self.root = os.path.join(tmp, "proj")
        for rel, text in code.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
        self.register = os.path.join(self.root, "FINDINGS.md")
        with open(self.register, "w") as fh:
            fh.write(register)
        self.vault = os.path.join(self.root, ".tracelink", "vault")
        self.symbols = os.path.join(self.root, ".tracelink", "symbols.json")
        os.makedirs(os.path.dirname(self.symbols), exist_ok=True)
        self.quiet(lambda: symbol_index.main(
            ["--repo", self.root, "--backend", "scan", "--out", self.symbols]))
        self.quiet(lambda: splitter.main(
            ["--register", self.register, "--out", self.vault,
             "--prefix", "W"]))

    def quiet(self, fn):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return fn()

    def link(self, *extra):
        return self.quiet(lambda: linker.main(
            ["--vault", self.vault, "--symbols", self.symbols,
             "--repo", self.root] + list(extra)))

    def state(self):
        with open(os.path.join(self.vault, consult_mod.STATE_FILE)) as fh:
            return json.load(fh)


class TheWalkIsProvablyNotTaken(unittest.TestCase):
    """A sentinel, not an argument: `repo_file_map` is replaced by a counter
    and the test fails if it is ever entered."""

    def counted_link(self, project, *extra):
        real = linker.repo_file_map
        calls = []

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        with mock.patch.object(linker, "repo_file_map", counting):
            code = project.link(*extra)
        return code, len(calls)

    def test_no_note_names_a_file_so_the_tree_is_never_walked(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=SYMBOL_ONLY)
            code, walks = self.counted_link(project)
            self.assertEqual(0, code)
            self.assertEqual(0, walks, "the repository was walked for nothing")
            self.assertEqual(2, len(project.state()["notes"]))

    def test_one_note_naming_a_file_builds_the_map_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=WITH_FILE_REF)
            code, walks = self.counted_link(project, "--full")
            self.assertEqual(0, code)
            self.assertEqual(1, walks, "the map must be built once, not per "
                                       "note")

    def test_the_anchor_is_still_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=WITH_FILE_REF)
            project.link()
            entry = project.state()["notes"]["W-03.md"]
            self.assertEqual(["infra/compose.yml"], entry["files"])


class ResolutionIsUnchanged(unittest.TestCase):
    """Deferring must not become approximating."""

    def test_two_files_matching_one_reference_are_still_ambiguous(self):
        """A reference is matched by path suffix, so two trees carrying the
        same tail are two candidates — the thing a "does this path exist"
        shortcut would have collapsed into a confident match."""
        code = dict(CODE)
        code["a/docker/compose.yml"] = "services: {}\n"
        code["b/docker/compose.yml"] = "services: {}\n"
        register = SYMBOL_ONLY + (
            "\n## W-03 — compose drift [MEDIUM]\n### STATUS: OPEN\n\n"
            "`docker/compose.yml` disagrees with itself.\n")
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=register, code=code)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                linker.main(["--vault", project.vault, "--symbols",
                             project.symbols, "--repo", project.root,
                             "--full"])
            entry = project.state()["notes"]["W-03.md"]
            self.assertEqual([], entry["files"], "an ambiguity became a match")
            self.assertIn("AMBIGUOUS docker/compose.yml", buffer.getvalue())
            # Recorded here as it is, not as it should be: a file ambiguity
            # is reported on stdout and in CODE-INDEX.md but leaves no trace
            # in the note's own state, so `explain` cannot show it the way it
            # shows an ambiguous symbol. Found while writing this test,
            # registered as F5, and out of scope for a ministep about when
            # the walk happens.
            self.assertEqual([], entry["ambiguous"])

    def test_a_new_file_that_creates_an_ambiguity_invalidates_the_skip(self):
        """The file map is an input the content hash cannot see, so a note
        that has not changed still has to be re-examined against today's
        tree. Deferring the walk must not weaken that."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=WITH_FILE_REF)
            project.link()
            self.assertEqual(["infra/compose.yml"],
                             project.state()["notes"]["W-03.md"]["files"])
            second = os.path.join(project.root, "deploy", "infra",
                                  "compose.yml")
            os.makedirs(os.path.dirname(second))
            with open(second, "w") as fh:
                fh.write("services: {}\n")
            project.link()
            entry = project.state()["notes"]["W-03.md"]
            self.assertEqual([], entry["files"],
                             "the anchor survived a new ambiguity")

    def test_a_deleted_file_removes_the_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=WITH_FILE_REF)
            project.link()
            os.remove(os.path.join(project.root, "infra", "compose.yml"))
            project.link()
            self.assertEqual([], project.state()["notes"]["W-03.md"]["files"])

    def test_symbol_linking_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=SYMBOL_ONLY)
            project.link()
            entry = project.state()["notes"]["W-01.md"]
            self.assertEqual(["compute_total"], entry["linked"])

    def test_a_second_run_still_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, register=WITH_FILE_REF)
            project.link()
            with open(os.path.join(project.vault,
                                   consult_mod.STATE_FILE), "rb") as fh:
                before = fh.read()
            project.link()
            with open(os.path.join(project.vault,
                                   consult_mod.STATE_FILE), "rb") as fh:
                self.assertEqual(before, fh.read())


if __name__ == "__main__":
    unittest.main()
