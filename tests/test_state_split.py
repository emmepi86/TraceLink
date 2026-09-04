"""ms-11c: compiling the memory and serving it are different jobs.

Benchmark 02 measured `consult` going from 9.8ms at 14k symbols to 548ms at
400k — with the vault held at 100 notes throughout. The cost was not the
memory being served; it was `symbol_locations`, the linker's cache of the
index, sitting in the same file and making up 94–98% of it. `consult` never
read a byte of it and parsed all of it, on every edit.

So the state was split by owner rather than by size:

    link      consumes the index, writes  .tracelink-symbol-state.json
              (its own cache of it) and   .tracelink-link-state.json
              (the knowledge it compiled)

    consult   reads the link state. Only. It does not know the other file
    explain   exists, and the import-graph test below keeps it that way.

The proof that this is a separation and not just a smaller JSON is the
adversarial test at the bottom: change the index, do NOT relink, and consult
must still serve the last complete snapshot — unchanged. Serving context and
compiling it are different jobs, and only one of them is allowed to be
affected by the index moving.
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

from tracelink import consult as consult_mod  # noqa: E402
from tracelink import linker, splitter, symbol_index  # noqa: E402

REGISTER = ("# Findings\n\n"
            "## S-01 — totals ignore tax [HIGH]\n### STATUS: OPEN\n\n"
            "`compute_total` in `src/app.py` never applies tax.\n\n"
            "## S-02 — retries duplicate [MEDIUM]\n### STATUS: OPEN\n\n"
            "`retry_send` can deliver twice.\n")

CODE = {"src/app.py": "def compute_total(items):\n    return sum(items)\n",
        "src/send.py": "def retry_send(msg):\n    return msg\n"}


class Project:
    def __init__(self, tmp, code=CODE, register=REGISTER):
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

    def quiet(self, fn):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return fn()

    def index(self):
        return self.quiet(lambda: symbol_index.main(
            ["--repo", self.root, "--backend", "scan", "--out", self.symbols]))

    def split(self):
        return self.quiet(lambda: splitter.main(
            ["--register", self.register, "--out", self.vault,
             "--prefix", "S"]))

    def link(self, *extra):
        return self.quiet(lambda: linker.main(
            ["--vault", self.vault, "--symbols", self.symbols,
             "--repo", self.root] + list(extra)))

    def sync(self):
        self.index()
        self.split()
        self.link()

    @property
    def link_state(self):
        return os.path.join(self.vault, consult_mod.STATE_FILE)

    @property
    def symbol_state(self):
        return os.path.join(self.vault, linker.SYMBOL_STATE_FILE)

    def read(self, path):
        with open(path) as fh:
            return json.load(fh)


class TheHeavyMapLeftTheServingPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Project(self.tmp.name)
        self.project.sync()

    def test_the_link_state_no_longer_carries_the_symbol_map(self):
        state = self.project.read(self.project.link_state)
        self.assertNotIn("symbol_locations", state)
        self.assertEqual(consult_mod.STATE_SCHEMA, state["schema_version"])
        self.assertIn("symbol_state_fingerprint", state)

    def test_the_symbol_map_lives_in_its_own_file(self):
        heavy = self.project.read(self.project.symbol_state)
        self.assertEqual(linker.SYMBOL_STATE_SCHEMA, heavy["schema_version"])
        self.assertIn("compute_total", heavy["symbol_locations"])
        self.assertIn("retry_send", heavy["symbol_locations"])

    def test_the_two_files_say_which_index_they_came_from(self):
        light = self.project.read(self.project.link_state)
        heavy = self.project.read(self.project.symbol_state)
        self.assertEqual(light["symbol_state_fingerprint"],
                         heavy["symbol_state_fingerprint"])

    def test_consult_still_answers(self):
        result = consult_mod.consult(self.project.root, "src/app.py",
                                     vault=self.project.vault)
        self.assertEqual(["S-01"], [n.note_id for n in result.notes])
        self.assertEqual("match", result.notes[0].symbols[0].provenance.state)


class ConsultDoesNotKnowTheHeavyFileExists(unittest.TestCase):
    def test_the_name_appears_nowhere_in_the_serving_module(self):
        with open(os.path.join(SRC, "tracelink", "consult.py"),
                  encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("symbol-state", source)
        self.assertNotIn("SYMBOL_STATE", source)

    def test_consult_never_opens_it(self):
        """Watched at the syscall boundary rather than argued about: every
        path consult opens is recorded, and the heavy file is not among
        them."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp)
            project.sync()
            opened = []
            real_open = io.open

            def watching_open(path, *args, **kwargs):
                opened.append(str(path))
                return real_open(path, *args, **kwargs)

            import builtins
            builtins.open = watching_open
            try:
                consult_mod.consult(project.root, "src/app.py",
                                    vault=project.vault)
            finally:
                builtins.open = real_open
            self.assertTrue(opened, "consult opened nothing at all?")
            self.assertFalse(
                [p for p in opened if linker.SYMBOL_STATE_FILE in p],
                f"consult opened the heavy sidecar: {opened}")

    def test_it_is_not_merely_deleted_but_unneeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp)
            project.sync()
            os.remove(project.symbol_state)
            result = consult_mod.consult(project.root, "src/app.py",
                                         vault=project.vault)
            self.assertEqual(["S-01"], [n.note_id for n in result.notes])


class CompilingAndServingAreSeparate(unittest.TestCase):
    """The adversarial case: a newer index must not change what is served.

    A `sync` is three steps, so there is always a moment where the symbol
    state is new and the link state is the previous one. That is not a
    broken state to be detected and refused — it is the normal condition
    between two steps, and `consult` must keep serving the last complete
    snapshot rather than going quiet because the index moved half a step
    ahead of it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Project(self.tmp.name)
        self.project.sync()

    def briefing(self):
        return consult_mod.render_text(
            consult_mod.consult(self.project.root, "src/app.py",
                                vault=self.project.vault))

    def test_a_newer_index_alone_changes_nothing_that_is_served(self):
        before = self.briefing()
        self.assertIn("S-01", before)

        # the code moves: compute_total is now on a different line
        with open(os.path.join(self.project.root, "src", "app.py"), "w") as fh:
            fh.write("# a new first line\n\n\ndef compute_total(items):\n"
                     "    return sum(items)\n")
        self.project.index()          # symbol state ahead, link state behind
        light = self.project.read(self.project.link_state)
        heavy = self.project.read(self.project.symbol_state)
        self.assertEqual(light["symbol_state_fingerprint"],
                         heavy["symbol_state_fingerprint"],
                         "index alone must not rewrite either sidecar")

        self.assertEqual(before, self.briefing(),
                         "consult served something the linker never compiled")

    def test_and_after_link_the_new_snapshot_is_served(self):
        with open(os.path.join(self.project.root, "src", "app.py"), "w") as fh:
            fh.write("# a new first line\n\n\ndef compute_total(items):\n"
                     "    return sum(items)\n")
        self.project.index()
        self.project.link()
        self.assertIn("compute_total (L4)", self.briefing())

    def test_a_symbol_state_from_another_index_forces_a_full_relink(self):
        """Disagreement between the two files is never repaired."""
        heavy = self.project.read(self.project.symbol_state)
        heavy["symbol_state_fingerprint"] = "sha256:something-else"
        with open(self.project.symbol_state, "w") as fh:
            json.dump(heavy, fh)
        # the vault must still be linkable, by relinking rather than by
        # trusting a cache that does not belong to this index
        self.assertEqual(0, self.project.link())
        self.assertIn("S-01", self.briefing())

    def test_an_interrupted_run_leaves_the_previous_snapshot_readable(self):
        """The heavy file is written first, so a crash between the two
        writes leaves the link state whole."""
        before = self.briefing()
        heavy = self.project.read(self.project.symbol_state)
        heavy["symbol_locations"]["invented"] = "sha256:0"
        heavy["symbol_state_fingerprint"] = "sha256:half-written"
        with open(self.project.symbol_state, "w") as fh:
            json.dump(heavy, fh)          # as if link died right here
        self.assertEqual(before, self.briefing())


class NoMigrationFromTheOldShape(unittest.TestCase):
    def test_a_v4_state_is_not_mined_for_its_symbol_map(self):
        """If the index is derivable, rebuild it from the source. A migrator
        that copied the map out of the old file would be trusting a cache
        whose provenance nobody checked."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp)
            project.sync()
            state = project.read(project.link_state)
            state["schema_version"] = 4
            state["symbol_locations"] = {"compute_total": "sha256:0"}
            del state["symbol_state_fingerprint"]
            with open(project.link_state, "w") as fh:
                json.dump(state, fh)
            os.remove(project.symbol_state)

            self.assertIsNone(linker.load_state(project.link_state))
            result = consult_mod.consult(project.root, "src/app.py",
                                         vault=project.vault)
            self.assertEqual("state-schema", result.silence)

            project.link()               # rebuilt, not migrated
            self.assertEqual(consult_mod.STATE_SCHEMA,
                             project.read(project.link_state)["schema_version"])
            self.assertNotIn("symbol_locations",
                             project.read(project.link_state))


class TheSecondSyncStillChangesNothing(unittest.TestCase):
    def test_both_sidecars_are_byte_identical_on_a_second_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp)
            project.sync()
            first = {path: open(path, "rb").read()
                     for path in (project.link_state, project.symbol_state)}
            project.sync()
            for path, blob in first.items():
                with open(path, "rb") as fh:
                    self.assertEqual(blob, fh.read(), path)


if __name__ == "__main__":
    unittest.main()
