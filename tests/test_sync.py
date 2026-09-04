"""ms-6: `sync` — one command, three steps, and the same bytes twice.

`sync` orchestrates and nothing else: `index`, `split`, `link`, in that
order, with the project's configured paths, stopping at the first failure.
Any rule about *linking* that appeared in this module would be a second
copy of a rule that already exists, and the copy is the one that rots — so
the tests here are about orchestration properties, not about linking.

The property worth the most is determinism: same repository, same register,
same configuration, same bytes. It is checked the way a user would check
it, by running the command twice and comparing checksums of everything it
wrote.

The second property is the one that made this ministep find a real defect.
`split` regenerates a note from the register, and the register knows
nothing about links — so it used to delete the managed block `link` had
written. The vault ended up correct because `link` ran afterwards and put
it back, which meant the documented pipeline rewrote every note twice on
every run and the incremental skip could never fire. A test now pins the
block's survival, and a second `sync` is required to change nothing at all.
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import cli, config, consult, splitter, sync  # noqa: E402

REGISTER = ("# Findings\n\n"
            "## RES-01 — totals ignore tax [HIGH]\n"
            "### STATUS: OPEN\n"
            "`compute_total` in `src/app.py` never applies tax.\n\n"
            "## RES-02 — retries are not idempotent [MEDIUM]\n"
            "### STATUS: OPEN\n"
            "`retry_send` can deliver twice.\n")

CODE = {"src/app.py": "def compute_total(items):\n    return sum(items)\n",
        "src/send.py": "def retry_send(msg):\n    return msg\n"}


def project(tmp, register=REGISTER, code=CODE, cfg=None):
    proj = os.path.join(tmp, "proj")
    for rel, text in code.items():
        path = os.path.join(proj, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
    with open(os.path.join(proj, "FINDINGS.md"), "w") as fh:
        fh.write(register)
    if cfg is not None:
        os.makedirs(os.path.join(proj, ".tracelink"), exist_ok=True)
        with open(config.path(proj), "w") as fh:
            json.dump(cfg, fh)
    return proj


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["sync"] + list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def checksums(root):
    """Every file tracelink wrote, by content — not by mtime."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(
                    fh.read()).hexdigest()
    return out


class SyncRunsTheWholePipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = project(self.tmp.name)

    def test_one_command_produces_a_linked_vault(self):
        code, out, _ = run("--repo", self.proj)
        self.assertEqual(0, code, out)
        vault = os.path.join(self.proj, ".tracelink", "vault")
        self.assertTrue(os.path.exists(os.path.join(vault, "RES-01.md")))
        self.assertTrue(os.path.exists(os.path.join(vault, "CODE-INDEX.md")))
        self.assertTrue(os.path.exists(os.path.join(vault, consult.STATE_FILE)))
        with open(os.path.join(vault, "RES-01.md")) as fh:
            self.assertIn("src/app.py", fh.read())

    def test_it_says_what_each_step_did(self):
        _c, out, _ = run("--repo", self.proj)
        for step in sync.STEPS:
            self.assertIn(step, out)
        self.assertIn("memory consistent", out)

    def test_the_json_document_reports_the_same_thing(self):
        code, out, _ = run("--repo", self.proj, "--json")
        doc = json.loads(out)
        self.assertEqual(0, code)
        self.assertTrue(doc["ok"])
        self.assertEqual(1, doc["schema_version"])
        self.assertEqual(2, doc["summary"]["notes"])
        self.assertEqual(consult.STATE_SCHEMA, doc["summary"]["state_schema"])
        self.assertEqual({"index": 0, "split": 0, "link": 0},
                         doc["summary"]["steps"])


class SyncIsDeterministicAndIdempotent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = project(self.tmp.name)
        self.tl = os.path.join(self.proj, ".tracelink")

    def test_the_same_inputs_produce_the_same_bytes(self):
        run("--repo", self.proj)
        first = checksums(self.tl)
        run("--repo", self.proj)
        self.assertEqual(first, checksums(self.tl))

    def test_a_second_run_changes_no_note_at_all(self):
        """Not merely 'ends up the same' — nothing is rewritten. This is the
        regression guard for split deleting the managed block."""
        run("--repo", self.proj)
        _c, out, _ = run("--repo", self.proj, "--json")
        linking = json.loads(out)["summary"]["linking"]
        self.assertEqual(0, linking["notes_modified"])
        self.assertEqual(2, linking["notes_skipped_unchanged"])

    def test_check_is_quiet_when_the_memory_is_current(self):
        run("--repo", self.proj)
        before = checksums(self.tl)
        code, _out, _ = run("--repo", self.proj, "--check")
        self.assertEqual(0, code)
        self.assertEqual(before, checksums(self.tl), "--check wrote something")

    def test_check_writes_nothing_even_when_it_is_out_of_date(self):
        """The failure mode this replaced: --check ran split in place, so
        the check itself performed the change it was reporting."""
        run("--repo", self.proj)
        before = checksums(self.tl)
        with open(os.path.join(self.proj, "FINDINGS.md"), "a") as fh:
            fh.write("\n## RES-03 — a third one [LOW]\n### STATUS: OPEN\n"
                     "`compute_total` again.\n")
        code, _out, _ = run("--repo", self.proj, "--check")
        self.assertEqual(sync.EXIT_FAILED, code)
        self.assertEqual(before, checksums(self.tl), "--check wrote something")

    def test_check_names_the_files_a_sync_would_touch(self):
        run("--repo", self.proj)
        with open(os.path.join(self.proj, "FINDINGS.md"), "a") as fh:
            fh.write("\n## RES-03 — a third one [LOW]\n### STATUS: OPEN\n"
                     "`compute_total` again.\n")
        _c, out, _ = run("--repo", self.proj, "--check", "--json")
        changed = json.loads(out)["changed"]
        self.assertIn("vault/RES-03.md", changed)

    def test_check_fails_when_the_register_moved_ahead(self):
        run("--repo", self.proj)
        with open(os.path.join(self.proj, "FINDINGS.md"), "a") as fh:
            fh.write("\n## RES-03 — a third one [LOW]\n### STATUS: OPEN\n"
                     "`compute_total` again.\n")
        code, out, _ = run("--repo", self.proj, "--check")
        self.assertEqual(sync.EXIT_FAILED, code)
        self.assertIn("out of date", out)


class SplitKeepsWhatLinkWrote(unittest.TestCase):
    """The defect this ministep uncovered, pinned at its source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = project(self.tmp.name)
        run("--repo", self.proj)
        self.note = os.path.join(self.proj, ".tracelink", "vault",
                                 "RES-01.md")

    def read(self):
        with open(self.note) as fh:
            return fh.read()

    def test_a_plain_split_no_longer_unlinks_the_vault(self):
        before = self.read()
        self.assertIn(consult.BLOCK_START, before)
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            splitter.main(["--register", os.path.join(self.proj,
                                                      "FINDINGS.md"),
                           "--out", os.path.join(self.proj, ".tracelink",
                                                 "vault"),
                           "--prefix", "RES"])
        self.assertEqual(before, self.read(),
                         "split rewrote a note it did not need to touch")

    def test_a_note_with_no_block_yet_is_written_normally(self):
        os.remove(self.note)
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            splitter.main(["--register", os.path.join(self.proj,
                                                      "FINDINGS.md"),
                           "--out", os.path.join(self.proj, ".tracelink",
                                                 "vault"),
                           "--prefix", "RES"])
        text = self.read()
        self.assertNotIn(consult.BLOCK_START, text)
        self.assertIn("RES-01", text)


class SyncFailsHonestly(unittest.TestCase):
    def test_a_missing_register_is_a_usage_error_with_a_way_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp)
            os.remove(os.path.join(proj, "FINDINGS.md"))
            code, _out, err = run("--repo", proj)
            self.assertEqual(2, code)
            self.assertIn("tracelink init", err)

    def test_a_failing_step_stops_the_ones_after_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp)
            code, out, _err = run("--repo", proj, "--backend", "ctags")
            # ctags is not installed in CI; whatever the cause, the contract
            # is that a failure is named and nothing later ran on it.
            if code != 0:
                self.assertIn("index", out)
                self.assertNotIn("memory consistent", out)
                _c, doc, _ = run("--repo", proj, "--backend", "ctags",
                                 "--json")
                steps = json.loads(doc)["summary"]["steps"]
                self.assertNotIn("link", steps)

    def test_configuration_supplies_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = project(tmp, cfg={"register": "NOTES.md",
                                     "prefix": "BUG"})
            os.rename(os.path.join(proj, "FINDINGS.md"),
                      os.path.join(proj, "NOTES.md"))
            with open(os.path.join(proj, "NOTES.md"), "w") as fh:
                fh.write(REGISTER.replace("RES-", "BUG-"))
            code, _out, err = run("--repo", proj)
            self.assertEqual(0, code, err)
            self.assertTrue(os.path.exists(os.path.join(
                proj, ".tracelink", "vault", "BUG-01.md")))


if __name__ == "__main__":
    unittest.main()
