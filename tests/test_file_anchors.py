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


class AmbiguityNoLongerImpliesAbsence(_AnchorCase):
    """0.8.0 fix round 1, Bug A: EVERY scanned note gets a state entry. An
    incidental symbol ambiguity (`up` on a migrations tree) used to discard
    the whole entry — including the note's clean file anchors — leaving
    consult blind on exactly the infra notes the feature exists for."""

    TWO_VALIDATE = {"validate": [
        {"path": "src/users.py", "line": 3, "kind": "py",
         "qualified_name": None},
        {"path": "src/payments.py", "line": 7, "kind": "py",
         "qualified_name": None}]}

    def test_an_ambiguous_symbol_does_not_evict_the_files_from_the_state(self):
        self.repo_file("infra/docker/compose.yml")
        self.symbols(self.TWO_VALIDATE)
        self.note("RES-01", "`validate` breaks when "
                            "`infra/docker/compose.yml` changes ports.")
        r = self.link()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AMBIGUOUS validate", r.stdout)
        entry = json.loads(self.read(STATE))["notes"]["RES-01.md"]
        self.assertEqual(entry["files"], ["infra/docker/compose.yml"])
        self.assertEqual(entry["ambiguous"], [["validate", "ambiguous"]])
        self.assertIn("- infra/docker/compose.yml", self.block("RES-01.md"))

    def test_consult_sees_the_files_of_a_note_with_incidental_ambiguity(self):
        """The end-to-end that Bug A broke on the real repo: TODI-9 cites
        compose.yml AND the ubiquitous `up`; editing compose.yml must still
        inject the note."""
        self.repo_file("infra/docker/compose.yml")
        self.symbols(self.TWO_VALIDATE)
        self.note("RES-01", "`validate` breaks when "
                            "`infra/docker/compose.yml` changes ports.")
        self.link()
        proj = self._tmp.name
        tl = os.path.join(proj, ".tracelink")
        os.makedirs(tl, exist_ok=True)
        os.symlink(self.vault, os.path.join(tl, "vault"))
        with open(os.path.join(tl, "config.json"), "w") as fh:
            json.dump({"consult": True}, fh)
        os.makedirs(os.path.join(proj, "infra", "docker"), exist_ok=True)
        open(os.path.join(proj, "infra", "docker", "compose.yml"),
             "w").close()
        out = plugin_refresh.consult(proj, _payload(
            os.path.join(proj, "infra", "docker", "compose.yml")))
        self.assertTrue(out, "consult is still blind on the ambiguous note")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("RES-01", ctx)
        self.assertIn("file: infra/docker/compose.yml", ctx)

    def test_a_pure_ambiguous_note_is_skipped_without_losing_its_warning(self):
        self.symbols(self.TWO_VALIDATE)
        self.note("RES-01", "`validate` is wrong.")
        self.link()
        index_before = self.read("CODE-INDEX.md")
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(_stat(r2, "notes_skipped_unchanged"), 1)
        self.assertIn("AMBIGUOUS validate", r2.stdout,
                      "the skip must not swallow the warning")
        self.assertEqual(self.read("CODE-INDEX.md"), index_before)
        self.assertIn("## Ambiguous references", self.read("CODE-INDEX.md"))
        self.assertEqual(self.block("RES-01.md"), "")

    def test_a_cached_ambiguity_is_recomputed_when_the_symbol_moves(self):
        """The cached warning is only as good as its proof: resolve the
        ambiguity in the index and the note must be relinked, not replayed."""
        self.symbols(self.TWO_VALIDATE)
        self.note("RES-01", "`validate` is wrong.")
        self.link()
        self.symbols({"validate": [
            {"path": "src/users.py", "line": 3, "kind": "py",
             "qualified_name": None}]})
        r2 = self.link()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("AMBIGUOUS", r2.stdout)
        self.assertIn("`validate` — src/users.py:L3", self.block("RES-01.md"))
        self.assertNotIn("## Ambiguous references", self.read("CODE-INDEX.md"))

    def test_status_classifies_ambiguity_from_the_index_not_from_absence(self):
        """(c): unverified means a real mismatch on disk; a recorded note
        that CODE-INDEX declares ambiguous is ambiguous-by-design, verified,
        and never an unlinked problem."""
        self.repo_file("infra/docker/compose.yml")
        register = os.path.join(self._tmp.name, "FINDINGS.md")
        with open(register, "w") as fh:
            fh.write("# Findings\n\n## RES-01 — infra drift [HIGH]\n"
                     "### STATUS: OPEN\n"
                     "`validate` breaks when `infra/docker/compose.yml` "
                     "changes ports.\n")
        subprocess.run(
            [sys.executable, f"{ROOT}/scripts/split.py", "--register",
             register, "--out", self.vault, "--prefix", "RES"],
            capture_output=True, text=True)
        self.symbols(self.TWO_VALIDATE)
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
        self.assertEqual(payload["links"]["notes_ambiguous"], 1)
        self.assertEqual(payload["links"]["notes_unverified"], 0)
        self.assertEqual(payload["links"]["unlinked_count"], 0)
        # The test symbols file has no provenance, so freshness is honestly
        # unknown — but nothing about LINKS may be a problem.
        self.assertFalse([p for p in payload["problems"] if "link" in p],
                         payload["problems"])


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


SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import plugin_refresh  # noqa: E402


def _consult_project(tmp, entry_files, linked=(), status="open",
                     severity="medium", schema=3):
    """A project with one hand-built note + v3 link-state, the same shape
    the linker now writes: `files` next to linked/locations."""
    proj = os.path.join(tmp, "proj")
    vault = os.path.join(proj, ".tracelink", "vault")
    os.makedirs(vault)
    os.makedirs(os.path.join(proj, "infra", "docker"))
    with open(os.path.join(proj, "infra", "docker", "compose.yml"), "w") as fh:
        fh.write("services: {}\n")
    with open(os.path.join(vault, "TODI-9.md"), "w") as fh:
        fh.write(f"---\ntracelink_schema: 1\ntracelink_id: TODI-9\n"
                 f"id: TODI-9\nstatus: {status}\nseverity: {severity}\n---\n\n"
                 f"# TODI-9 — compose ports drift [{severity.upper()}]\n\n"
                 "Body consult must never read.\n")
    state = {"schema_version": schema,
             "symbols_fingerprint": "sha256:0",
             "options_fingerprint": "sha256:0",
             "symbol_locations": {},
             "notes": {"TODI-9.md": {
                 "content_hash": "sha256:0",
                 "linked": [s[0] for s in linked],
                 "locations": [{"path": s[1], "line": s[2]} for s in linked],
                 "files": list(entry_files),
                 "files_fingerprint": "sha256:0"}}}
    with open(os.path.join(vault, STATE), "w") as fh:
        json.dump(state, fh)
    with open(os.path.join(proj, ".tracelink", "config.json"), "w") as fh:
        json.dump({"consult": True}, fh)
    return proj


def _payload(path):
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})


class ConsultSpeaksForAnchoredFiles(unittest.TestCase):
    def test_editing_an_anchored_file_injects_the_note_with_file_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = _consult_project(tmp, ["infra/docker/compose.yml"])
            out = plugin_refresh.consult(proj, _payload(
                os.path.join(proj, "infra", "docker", "compose.yml")))
        self.assertTrue(out, "consult stayed mute on an anchored file")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("infra/docker/compose.yml", ctx)
        self.assertIn("- TODI-9 [open/medium] compose ports drift "
                      "— file: infra/docker/compose.yml", ctx)
        self.assertNotIn("symbols:", ctx)

    def test_a_symbol_match_keeps_its_format_untouched(self):
        """Regression guard: file anchors extend consult, they do not
        reformat the symbol path."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = _consult_project(
                tmp, [], linked=[("compose_ports", "src/app.py", 7)])
            src = os.path.join(proj, "src")
            os.makedirs(src)
            open(os.path.join(src, "app.py"), "w").close()
            out = plugin_refresh.consult(proj, _payload(
                os.path.join(src, "app.py")))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("symbols: compose_ports (L7)", ctx)
        self.assertNotIn("file:", ctx)

    def test_an_unanchored_file_stays_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = _consult_project(tmp, ["infra/docker/compose.yml"])
            other = os.path.join(proj, "infra", "other.yml")
            open(other, "w").close()
            out = plugin_refresh.consult(proj, _payload(other))
        self.assertEqual(out, "")

    def test_a_v2_state_on_disk_is_silence_not_a_guess(self):
        """The consult path reads only the state; a pre-0.8 state has no
        files array and a shape this code never audited."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = _consult_project(tmp, ["infra/docker/compose.yml"],
                                    schema=2)
            out = plugin_refresh.consult(proj, _payload(
                os.path.join(proj, "infra", "docker", "compose.yml")))
        self.assertEqual(out, "")


class ConsultEndToEndThroughTheLinker(unittest.TestCase):
    """No hand-built state: split, link with --repo, then consult — the
    whole promise, an agent editing compose.yml gets the infra note."""

    def test_the_loop_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "proj")
            os.makedirs(os.path.join(proj, "infra", "docker"))
            with open(os.path.join(proj, "infra", "docker",
                                   "compose.yml"), "w") as fh:
                fh.write("services: {}\n")
            register = os.path.join(proj, "FINDINGS.md")
            with open(register, "w") as fh:
                fh.write("# Findings\n\n"
                         "## RES-01 — compose ports drift [HIGH]\n"
                         "### STATUS: OPEN\n"
                         "the stage stack breaks when "
                         "`infra/docker/compose.yml` changes ports.\n")
            vault = os.path.join(proj, ".tracelink", "vault")
            subprocess.run(
                [sys.executable, f"{ROOT}/scripts/split.py", "--register",
                 register, "--out", vault, "--prefix", "RES"],
                capture_output=True, text=True)
            syms = os.path.join(proj, ".tracelink", "symbols.json")
            with open(syms, "w") as fh:
                json.dump({"backend": "test",
                           "symbols": {"unrelated": "src/x.py:L1"}}, fh)
            r = subprocess.run(
                [sys.executable, f"{ROOT}/scripts/link.py", "--vault", vault,
                 "--symbols", syms, "--repo", proj],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(proj, ".tracelink", "config.json"),
                      "w") as fh:
                json.dump({"consult": True}, fh)
            out = plugin_refresh.consult(proj, _payload(
                os.path.join(proj, "infra", "docker", "compose.yml")))
        self.assertTrue(out, "the linker's own state did not feed consult")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("RES-01", ctx)
        self.assertIn("file: infra/docker/compose.yml", ctx)


class LintTreatsResolvedPathsAsAnchors(unittest.TestCase):
    """Point 8: a resolved file anchor is RELIABLE — neither stopwords nor
    ubiquity apply to a path. An infra note citing compose.yml is memory,
    not prose."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = self._tmp.name
        self.register = os.path.join(self.base, "FINDINGS.md")
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.repo, "infra", "docker"))
        with open(os.path.join(self.repo, "infra", "docker",
                               "compose.yml"), "w") as fh:
            fh.write("services: {}\n")

    def write_register(self, body):
        with open(self.register, "w") as fh:
            fh.write("# Findings\n\n## RES-01 — infra note [HIGH]\n"
                     "### STATUS: OPEN\n" + body + "\n")

    def lint(self, *extra):
        import contextlib
        import io
        from unittest import mock
        from tracelink import lint as lint_mod
        out = io.StringIO()
        argv = ["tracelink-lint", "--register", self.register,
                "--format", "json", *extra]
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out):
            rc = lint_mod.main()
        return rc, json.loads(out.getvalue())

    def codes(self, payload):
        return [w["code"] for w in payload["warnings"]]

    def test_an_infra_only_note_with_a_resolved_path_is_not_prose(self):
        self.write_register("the stage stack breaks when "
                            "`infra/docker/compose.yml` changes ports.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), [], payload)
        self.assertEqual(rc, 0)

    def test_without_repo_the_same_note_still_warns_prose_only(self):
        """Resolution needs a tree to resolve against: no --repo, no
        file anchors — the 0.7.1 behaviour is untouched."""
        self.write_register("the stage stack breaks when "
                            "`infra/docker/compose.yml` changes ports.")
        rc, payload = self.lint()
        self.assertEqual(self.codes(payload), ["prose-only"])
        self.assertEqual(rc, 1)

    def test_a_backticked_path_that_resolves_nothing_is_unknown(self):
        self.write_register("see `infra/missing.yml` for the ports.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), ["unknown-symbols"])
        self.assertIn("infra/missing.yml", payload["warnings"][0]["detail"])
        self.assertEqual(rc, 1)

    def test_an_unresolved_path_beside_a_resolved_one_demotes_to_info(self):
        self.write_register("`infra/docker/compose.yml` drives the ports; "
                            "`infra/missing.yml` was removed.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), [])
        self.assertEqual([i["code"] for i in payload["infos"]],
                         ["unknown-symbols"])
        self.assertEqual(rc, 0)

    def test_a_bare_unresolved_path_is_prose_not_a_citation(self):
        self.write_register("someone should look at infra/missing.yml "
                            "one of these days.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), ["prose-only"])

    def test_an_ambiguous_path_is_neither_anchor_nor_unknown(self):
        """The file exists — twice. The linker reports the ambiguity; lint
        neither calls the citation unknown nor the finding prose."""
        with open(os.path.join(self.repo, "infra", "compose.yml"),
                  "w") as fh:
            fh.write("services: {}\n")
        self.write_register("see `compose.yml`.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), [], payload)
        self.assertEqual(payload["infos"], [])
        self.assertEqual(rc, 0)

    def test_a_file_anchor_vouches_for_unknown_symbols(self):
        symbols = os.path.join(self.base, "symbols.json")
        with open(symbols, "w") as fh:
            json.dump({"symbols": {"known_helper": [
                {"path": "src/app.py", "line": 1, "kind": "py",
                 "qualified_name": None}]}}, fh)
        self.write_register("`frobnicate_widget` misreads "
                            "`infra/docker/compose.yml`.")
        rc, payload = self.lint("--repo", self.repo, "--symbols", symbols)
        self.assertEqual(self.codes(payload), [])
        self.assertEqual([i["code"] for i in payload["infos"]],
                         ["unknown-symbols"])
        self.assertEqual(rc, 0)

    def test_a_known_dotted_symbol_is_never_a_missing_file(self):
        """`payments.validate` is extension-shaped too. When the index
        knows the tail and no such file exists, the symbol reading wins:
        no unknown, no warning."""
        symbols = os.path.join(self.base, "symbols.json")
        with open(symbols, "w") as fh:
            json.dump({"symbols": {"validate_totals": [
                {"path": "src/payments.py", "line": 3, "kind": "py",
                 "qualified_name": "payments.validate_totals"}]}}, fh)
        self.write_register("`payments.validate_totals` rejects zero-line "
                            "orders.")
        rc, payload = self.lint("--repo", self.repo, "--symbols", symbols)
        self.assertEqual(self.codes(payload), [], payload)
        self.assertEqual(payload["infos"], [])
        self.assertEqual(rc, 0)

    def test_an_unresolved_stopword_chain_cannot_rescue_prose(self):
        """`data.value` is extension-shaped but identifier-shaped too, and
        its tail is a stopword: resolving to nothing must not turn it into
        file evidence against prose-only."""
        symbols = os.path.join(self.base, "symbols.json")
        with open(symbols, "w") as fh:
            json.dump({"symbols": {"unrelated_helper": [
                {"path": "src/app.py", "line": 1, "kind": "py",
                 "qualified_name": None}]}}, fh)
        self.write_register("the pipeline mangles `data.value` sometimes.")
        rc, payload = self.lint("--repo", self.repo, "--symbols", symbols)
        self.assertIn("prose-only", self.codes(payload))
        self.assertEqual(rc, 1)

    def test_the_register_is_never_its_own_anchor(self):
        """The register basename is excluded from resolution: a finding
        citing FINDINGS.md has cited the tool, not the code."""
        with open(os.path.join(self.repo, "FINDINGS.md"), "w") as fh:
            fh.write("# a register in the repo\n")
        self.write_register("see `FINDINGS.md`.")
        rc, payload = self.lint("--repo", self.repo)
        self.assertEqual(self.codes(payload), ["unknown-symbols"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
