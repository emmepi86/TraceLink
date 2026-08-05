"""0.8.0 — file anchors: notes anchor to the FILES they name, not only symbols.

The knowledge with the highest measured value is ops knowledge — nginx
configs, compose files, deploy scripts — exactly the notes that used to link
nothing: consult stayed mute when an agent edited compose.yml, lint called
them prose-only, CODE-INDEX never saw them. A file reference resolves by path
SUFFIX against the real tree (whole components, never substrings), a unique
match becomes an anchor, a multiple match is reported ambiguous and never
guessed, and the tool's own artifacts — register, vault, .tracelink/** — are
never anchor targets, even when cited textually.

    python3 -m unittest discover tests -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

STATE = ".tracelink-link-state.json"


def _stat(r, key):
    m = re.search(key + r":\s+(\d+)", r.stdout)
    assert m, f"{key} not reported:\n{r.stdout}"
    return int(m.group(1))


class _AnchorCase(unittest.TestCase):
    """A vault, a symbols file, and a small repository with infra files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = os.path.join(self._tmp.name, "v")
        os.mkdir(self.vault)
        self.repo = os.path.join(self._tmp.name, "repo")
        os.mkdir(self.repo)
        self.syms = os.path.join(self._tmp.name, "s.json")
        self.symbols({"parse_payload": "src/parser.py:L4"})
        self.addCleanup(self._tmp.cleanup)

    def repo_file(self, rel, content="key: value\n"):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path) or path, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)

    def note(self, stem, prose, frontmatter=""):
        with open(os.path.join(self.vault, f"{stem}.md"), "w") as fh:
            fh.write(f"---\ntracelink_schema: 1\nid: {stem}\n{frontmatter}"
                     f"---\n\n# {stem}\n\n{prose}\n")

    def symbols(self, mapping):
        with open(self.syms, "w") as fh:
            json.dump({"backend": "test", "symbols": mapping}, fh)

    def link(self, *extra):
        return subprocess.run(
            [sys.executable, f"{ROOT}/scripts/link.py",
             "--vault", self.vault, "--symbols", self.syms,
             "--repo", self.repo, *extra],
            capture_output=True, text=True)

    def read(self, name):
        return open(os.path.join(self.vault, name)).read()

    def block(self, name):
        text = self.read(name)
        if "tracelink:linked-code:start" not in text:
            return ""
        return text.split("tracelink:linked-code:start -->")[1].split(
            "<!-- tracelink:linked-code:end")[0]


class BacktickedPathsAnchor(_AnchorCase):
    def test_backtick_with_slash_resolves_to_the_block_and_the_index(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "Ports drift between environments — see "
                            "`infra/docker/compose.yml` before deploying.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("- infra/docker/compose.yml", self.block("RES-01.md"))
        index = self.read("CODE-INDEX.md")
        self.assertIn("## Files", index)
        self.assertIn("| infra/docker/compose.yml | [[RES-01]] |", index)

    def test_backtick_without_slash_resolves_when_unique(self):
        self.repo_file("ops/deploy-stage.sh", "#!/bin/sh\n")
        self.note("RES-01", "`deploy-stage.sh` is destructive on stage.")
        self.link()
        self.assertIn("- ops/deploy-stage.sh", self.block("RES-01.md"))

    def test_the_file_line_is_a_pure_path_without_symbol_or_line(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        self.link()
        block = self.block("RES-01.md")
        self.assertIn("\n- infra/docker/compose.yml\n", block)
        self.assertNotIn("`infra/docker/compose.yml`", block)
        self.assertNotIn(":L", block)

    def test_symbols_come_first_then_files_in_the_block(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "`parse_payload` reads `infra/docker/compose.yml`.")
        self.link()
        block = self.block("RES-01.md")
        self.assertLess(block.index("`parse_payload`"),
                        block.index("infra/docker/compose.yml"))


class BarePathsNeedASlash(_AnchorCase):
    def test_bare_with_slash_resolves(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "the deploy rewrites infra/docker/compose.yml "
                            "on every run.")
        self.link()
        self.assertIn("- infra/docker/compose.yml", self.block("RES-01.md"))

    def test_bare_without_slash_never_anchors_even_when_unique(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "remember to edit compose.yml before deploying.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.block("RES-01.md"), "")
        self.assertNotIn("## Files", self.read("CODE-INDEX.md"))


class SuffixMeansWholeComponents(_AnchorCase):
    def test_compose_prod_does_not_satisfy_the_compose_suffix(self):
        self.repo_file("infra/compose.yml")
        self.repo_file("infra/compose.prod.yml")
        self.note("RES-01", "see `compose.yml`.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("- infra/compose.yml", self.block("RES-01.md"))
        self.assertNotIn("AMBIGUOUS", r.stdout)

    def test_two_files_of_the_same_name_are_ambiguous_not_guessed(self):
        self.repo_file("a/compose.yml")
        self.repo_file("b/compose.yml")
        self.note("RES-01", "see `compose.yml`.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.block("RES-01.md"), "")
        self.assertIn("AMBIGUOUS compose.yml", r.stdout)
        index = self.read("CODE-INDEX.md")
        self.assertIn("## Ambiguous references", index)
        self.assertIn("`compose.yml`", index)
        self.assertIn("- a/compose.yml", index)
        self.assertIn("- b/compose.yml", index)

    def test_a_longer_suffix_disambiguates(self):
        self.repo_file("a/compose.yml")
        self.repo_file("b/compose.yml")
        self.note("RES-01", "see `a/compose.yml`.")
        r = self.link()
        self.assertIn("- a/compose.yml", self.block("RES-01.md"))
        self.assertNotIn("AMBIGUOUS", r.stdout)


class NonexistentPathsAnchorNothing(_AnchorCase):
    def test_a_path_the_repo_does_not_hold_is_silence(self):
        self.note("RES-01", "see `infra/missing.yml` for the ports.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.block("RES-01.md"), "")
        self.assertNotIn("## Files", self.read("CODE-INDEX.md"))


class TheToolNeverAnchorsToItself(_AnchorCase):
    """RES-OWNERSHIP for anchors: register, vault, .tracelink/**, INDEX and
    CODE-INDEX are never anchor targets, even when cited textually."""

    def _vault_in_repo(self):
        """Rebuild the case with the vault INSIDE the repo, the way the
        thirty-second demo and any non-hidden vault directory would be."""
        self.vault = os.path.join(self.repo, "vault")
        os.mkdir(self.vault)
        with open(os.path.join(self.vault, ".tracelink-manifest.json"),
                  "w") as fh:
            json.dump({"schema_version": 2,
                       "register": {"prefix": "RES",
                                    "source": "FINDINGS.md"},
                       "generated_notes": ["RES-01.md"]}, fh)

    def test_register_vault_and_indexes_are_not_targets(self):
        self._vault_in_repo()
        self.repo_file("FINDINGS.md", "# register\n")
        with open(os.path.join(self.vault, "CODE-INDEX.md"), "w") as fh:
            fh.write("stub\n")
        with open(os.path.join(self.vault, "INDEX.md"), "w") as fh:
            fh.write("stub\n")
        self.note("RES-01", "`FINDINGS.md` feeds `CODE-INDEX.md` and "
                            "`INDEX.md`; see also `RES-01.md`.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.block("RES-01.md"), "")

    def test_tracelink_directory_is_not_a_target(self):
        os.makedirs(os.path.join(self.repo, ".tracelink"))
        with open(os.path.join(self.repo, ".tracelink", "config.json"),
                  "w") as fh:
            fh.write("{}")
        self.note("RES-01", "consult is gated by `config.json`.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.block("RES-01.md"), "")

    def test_the_managed_block_is_never_reread_as_a_citation(self):
        """A file line written by the last run must not keep the anchor
        alive after the prose stops citing the file."""
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        self.link()
        self.assertIn("compose.yml", self.block("RES-01.md"))
        self.note("RES-01", "nothing to cite any more.")
        self.link()
        self.assertEqual(self.block("RES-01.md"), "")


class MaxLinksCapsTheTotal(_AnchorCase):
    def test_symbols_win_the_budget_files_take_the_rest(self):
        self.repo_file("infra/a.yml")
        self.repo_file("infra/b.yml")
        self.symbols({"parse_payload": "src/parser.py:L4",
                      "ingest_batch": "src/ingest.py:L9"})
        self.note("RES-01", "`parse_payload` and `ingest_batch` read "
                            "`infra/a.yml` and `infra/b.yml`.")
        self.link("--max-links", "3")
        block = self.block("RES-01.md")
        self.assertIn("`parse_payload`", block)
        self.assertIn("`ingest_batch`", block)
        self.assertIn("- infra/a.yml", block)
        self.assertNotIn("infra/b.yml", block)


class TheStateCarriesFileAnchors(_AnchorCase):
    def test_v3_state_records_the_files_array(self):
        from tracelink import linker
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        self.link()
        state = json.loads(self.read(STATE))
        self.assertEqual(state["schema_version"], linker._STATE_SCHEMA)
        entry = state["notes"]["RES-01.md"]
        self.assertEqual(entry["files"], ["infra/docker/compose.yml"])
        self.assertTrue(entry["files_fingerprint"].startswith("sha256:"))

    def test_a_v2_state_on_disk_forces_a_full_relink_and_is_rewritten_v3(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        self.link()
        state = json.loads(self.read(STATE))
        state["schema_version"] = 2
        for entry in state["notes"].values():
            entry.pop("files", None)
            entry.pop("files_fingerprint", None)
        with open(os.path.join(self.vault, STATE), "w") as fh:
            json.dump(state, fh)
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_stat(r, "notes_skipped_unchanged"), 0)
        healed = json.loads(self.read(STATE))
        self.assertEqual(healed["schema_version"], 3)
        self.assertIn("files", healed["notes"]["RES-01.md"])


class TheRepoTreeIsAnInputTheHashCannotSee(_AnchorCase):
    """Incremental correctness: a file appearing or disappearing changes the
    resolution of an untouched note, so the note must be relinked — the
    per-note fingerprint of the resolved reference list is what proves it."""

    def two_notes(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        self.note("RES-02", "`parse_payload` returns {} for an empty body.")

    def test_unchanged_repo_skips_both_notes(self):
        self.two_notes()
        self.link()
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 2)
        self.assertIn("- infra/docker/compose.yml", self.block("RES-01.md"))
        self.assertIn("| infra/docker/compose.yml | [[RES-01]] |",
                      self.read("CODE-INDEX.md"))

    def test_a_cited_file_deleted_from_the_repo_relinks_the_note(self):
        self.two_notes()
        self.link()
        os.remove(os.path.join(self.repo, "infra", "docker", "compose.yml"))
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self.block("RES-01.md"), "")
        self.assertNotIn("## Files", self.read("CODE-INDEX.md"))
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1,
                         "the unrelated note must still be skipped")

    def test_a_cited_file_appearing_in_the_repo_relinks_the_note(self):
        self.note("RES-01", "see `infra/new-thing.yml`.")
        self.link()
        self.assertEqual(self.block("RES-01.md"), "")
        self.repo_file("infra/new-thing.yml")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("- infra/new-thing.yml", self.block("RES-01.md"))

    def test_new_ambiguity_is_never_hidden_by_the_skip(self):
        """A second file of the same name turns a unique anchor into an
        ambiguity: the anchor must go AND the warning must appear."""
        self.note("RES-01", "see `unique.yml`.")
        self.repo_file("a/unique.yml")
        self.link()
        self.assertIn("- a/unique.yml", self.block("RES-01.md"))
        self.repo_file("b/unique.yml")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(self.block("RES-01.md"), "")
        self.assertIn("AMBIGUOUS unique.yml", r2.stdout)
        self.assertIn("## Ambiguous references", self.read("CODE-INDEX.md"))


class FullAndIncrementalAgreeByteForByte(_AnchorCase):
    def test_every_artifact_is_identical_between_the_two_modes(self):
        self.repo_file("infra/docker/compose.yml")
        self.repo_file("ops/deploy-stage.sh", "#!/bin/sh\n")
        self.note("RES-01", "`parse_payload` reads `infra/docker/compose.yml`.")
        self.note("RES-02", "ops/deploy-stage.sh drops the database.")
        self.note("RES-03", "prose that anchors nothing at all.")
        self.link()
        r_inc = self.link()
        self.assertEqual(_stat(r_inc, "notes_skipped_unchanged"), 3)
        snapshot = {n: self.read(n) for n in sorted(os.listdir(self.vault))}
        r_full = self.link("--full")
        self.assertEqual(r_full.returncode, 0, r_full.stdout + r_full.stderr)
        self.assertEqual(_stat(r_full, "notes_skipped_unchanged"), 0)
        for name, text in snapshot.items():
            self.assertEqual(self.read(name), text,
                             f"{name} differs between --full and incremental")


class ReportingCountsFileAnchors(_AnchorCase):
    def test_a_file_only_note_is_not_unlinked_and_counts_as_a_match(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        r = self.link("--require-linked")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_stat(r, "notes_with_matches"), 1)
        self.assertEqual(_stat(r, "unlinked_notes"), 0)
        self.assertEqual(_stat(r, "files_linked"), 1)

    def test_the_json_report_carries_files_linked(self):
        self.repo_file("infra/docker/compose.yml")
        self.note("RES-01", "see `infra/docker/compose.yml`.")
        r = self.link("--format", "json")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["linking"]["files_linked"], 1)

    def test_a_vault_without_file_anchors_reads_as_it_always_did(self):
        """The thirty-second demo's console output is a documented artifact:
        no file anchors, no files_linked line."""
        self.note("RES-01", "`parse_payload` returns {} for an empty body.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("files_linked", r.stdout)
        self.assertNotIn("## Files", self.read("CODE-INDEX.md"))


class StatusStaysCompatible(_AnchorCase):
    def test_a_file_only_note_is_neither_unlinked_nor_unverified(self):
        self.repo_file("infra/docker/compose.yml")
        register = os.path.join(self._tmp.name, "FINDINGS.md")
        with open(register, "w") as fh:
            fh.write("# Findings\n\n## RES-01 — compose drift [HIGH]\n"
                     "### STATUS: OPEN\nsee `infra/docker/compose.yml`.\n")
        subprocess.run(
            [sys.executable, f"{ROOT}/scripts/split.py", "--register",
             register, "--out", self.vault, "--prefix", "RES"],
            capture_output=True, text=True)
        self.link()
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, sys.argv[4]); "
             "from tracelink.status import main; "
             "sys.argv = ['status', '--register', sys.argv[1], '--vault', "
             "sys.argv[2], '--symbols', sys.argv[3], '--format', 'json']; "
             "raise SystemExit(main())",
             register, self.vault, self.syms,
             os.path.join(ROOT, "src")],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["links"]["notes_unverified"], 0)
        self.assertEqual(payload["links"]["unlinked_count"], 0)
        self.assertNotIn("unlinked-notes: 1", payload["problems"])


if __name__ == "__main__":
    unittest.main()
