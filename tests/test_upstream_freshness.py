"""ms-11a: an index cannot be fresher than the evidence it was built from.

Benchmark 01 found a symbol graph generated a month before the code it
described, producing an index TraceLink called `fresh` with 42% of its line
numbers wrong. The freshness check was not broken — its *scope* was. It
answers "does this index still describe this repository?", and an index
built a minute ago from a month-old artefact honestly answers yes.

So there are two questions now, and they are kept apart:

    index_freshness      is the index still about this repository?
    upstream_freshness   is the evidence it was built FROM current?
    effective_freshness  the less certain of the two — what to act on

The combination is monotone: `fresh + unknown` is `unknown`, never `fresh`.
Rounding an unverified claim up to a verified one is the single failure this
whole ministep exists to prevent, so it is tested as a property over the
entire table rather than as three examples.

The fail-closed direction matters too. A backend that records nothing about
which repository state it describes is `unknown`, not `verified`: an index
may be perfectly usable and still not be evidence that it is current, and
`usable` and `fresh` are different words.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import doctor, linker, splitter, status, symbol_index  # noqa
from tracelink.linker import combine_freshness  # noqa: E402

CODE = "def alpha(value):\n    return value\n\n\ndef beta(value):\n    return value\n"


def make_repo(tmp, git=False):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo, exist_ok=True)
    with open(os.path.join(repo, "mod.py"), "w") as fh:
        fh.write(CODE)
    if git:
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for args in (["init", "-q"], ["add", "-A"],
                     ["commit", "-qm", "one"]):
            subprocess.run(["git", "-C", repo] + args, env=env, check=True,
                           capture_output=True)
    return repo


def head(repo):
    out = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip()


def write_graph(repo, metadata=None):
    """A graphify-shaped artefact, optionally carrying provenance."""
    directory = os.path.join(repo, "graphify-out")
    os.makedirs(directory, exist_ok=True)
    nodes = [{"label": name, "source_file": "mod.py", "source_location": line,
              "file_type": "py"} for name, line in (("alpha", 1), ("beta", 5))]
    with open(os.path.join(directory, "graph.json"), "w") as fh:
        json.dump({"nodes": nodes, "graph": metadata or {}}, fh)


def index(repo, backend, out):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        symbol_index.main(["--repo", repo, "--backend", backend, "--out", out])
    with open(out) as fh:
        return json.load(fh)


def freshness_of(repo, symbols_path, tmp):
    """`link`'s verdict, through the real code path."""
    register = os.path.join(tmp, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write("# Findings\n\n## U-01 — a finding [MEDIUM]\n"
                 "### STATUS: OPEN\n\n`alpha` is named here.\n")
    vault = os.path.join(tmp, "vault")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        splitter.main(["--register", register, "--out", vault, "--prefix", "U"])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        linker.main(["--vault", vault, "--symbols", symbols_path,
                     "--repo", repo, "--format", "json"])
    return json.loads(buffer.getvalue())["freshness"], vault


class TheCombinationIsMonotone(unittest.TestCase):
    CERTAINTY = {"stale": 0, "unknown": 1, "fresh": 2}
    UPSTREAM = {"verified": "fresh", "stale": "stale", "unknown": "unknown"}

    def test_the_table_the_ministep_was_specified_with(self):
        self.assertEqual("fresh", combine_freshness("fresh", "verified"))
        self.assertEqual("unknown", combine_freshness("fresh", "unknown"))
        self.assertEqual("stale", combine_freshness("fresh", "stale"))
        for upstream in ("verified", "unknown", "stale"):
            self.assertEqual("stale", combine_freshness("stale", upstream))

    def test_the_effective_answer_never_claims_more_than_either_half(self):
        """The sentinel: certainty(effective) <= min(certainty of the parts).

        Checked over the whole table, because the one way this feature can
        fail is by rounding a single cell up.
        """
        for index_status in ("fresh", "unknown", "stale"):
            for upstream in ("verified", "unknown", "stale"):
                with self.subTest(index=index_status, upstream=upstream):
                    effective = combine_freshness(index_status, upstream)
                    self.assertLessEqual(
                        self.CERTAINTY[effective],
                        min(self.CERTAINTY[index_status],
                            self.CERTAINTY[self.UPSTREAM[upstream]]))

    def test_an_invalid_index_stays_invalid(self):
        self.assertEqual("invalid", combine_freshness("invalid", "verified"))

    def test_an_unknown_upstream_word_is_read_as_unknown(self):
        """A backend from the future must not be believed by default."""
        self.assertEqual("unknown", combine_freshness("fresh", "excellent"))


class ScanReadsTheTreeAndSaysSo(unittest.TestCase):
    def test_upstream_is_verified_by_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            payload = index(repo, "scan", os.path.join(tmp, "s.json"))
            upstream = payload["indexing"]["upstream"]
            self.assertEqual("verified", upstream["state"])
            self.assertEqual("working-tree", upstream["kind"])

    def test_effective_freshness_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            symbols = os.path.join(tmp, "s.json")
            index(repo, "scan", symbols)
            fresh, _vault = freshness_of(repo, symbols, tmp)
            self.assertEqual("fresh", fresh["status"])
            self.assertEqual("verified", fresh["upstream"]["state"])
            self.assertEqual("fresh", fresh["effective"])


class AnArtefactWithoutProvenanceIsUnknown(unittest.TestCase):
    """The b01 case, and the reason this ministep exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name)
        write_graph(self.repo)                      # graph: {} — says nothing
        self.symbols = os.path.join(self.tmp.name, "s.json")
        self.payload = index(self.repo, "graphify", self.symbols)

    def test_the_index_records_that_it_cannot_tell(self):
        upstream = self.payload["indexing"]["upstream"]
        self.assertEqual("unknown", upstream["state"])
        self.assertEqual("artifact-records-no-repository-provenance",
                         upstream["reason"])
        self.assertTrue(upstream["artifact"].endswith("graph.json"))

    def test_a_new_index_from_an_unverifiable_artefact_is_not_fresh(self):
        fresh, _vault = freshness_of(self.repo, self.symbols, self.tmp.name)
        self.assertEqual("fresh", fresh["status"])        # the index IS new
        self.assertEqual("unknown", fresh["upstream"]["state"])
        self.assertEqual("unknown", fresh["effective"])   # and unproven

    def test_a_timestamp_alone_still_does_not_verify_anything(self):
        write_graph(self.repo, {"generated_at": "2026-07-28T13:33:00Z"})
        payload = index(self.repo, "graphify", self.symbols)
        upstream = payload["indexing"]["upstream"]
        self.assertEqual("unknown", upstream["state"])
        self.assertEqual("2026-07-28T13:33:00Z", upstream["generated_at"])

    def test_the_require_gate_refuses_an_unverified_index(self):
        register = os.path.join(self.tmp.name, "FINDINGS.md")
        with open(register, "w") as fh:
            fh.write("# Findings\n\n## U-01 — a finding [MEDIUM]\n"
                     "### STATUS: OPEN\n\n`alpha` is named here.\n")
        vault = os.path.join(self.tmp.name, "vault")
        with contextlib.redirect_stdout(io.StringIO()):
            splitter.main(["--register", register, "--out", vault,
                           "--prefix", "U"])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(io.StringIO()):
            code = linker.main(["--vault", vault, "--symbols", self.symbols,
                                "--repo", self.repo, "--freshness", "require",
                                "--format", "json"])
        self.assertEqual(1, code)
        self.assertEqual("freshness-unknown",
                         json.loads(buffer.getvalue())["exit_reason"])


class AnArtefactThatNamesItsCommitCanBeChecked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name, git=True)
        self.symbols = os.path.join(self.tmp.name, "s.json")

    def ignore_the_artefact(self):
        """The realistic setup: a generated artefact is not committed.

        It matters for the verdict — an untracked artefact leaves the tree
        dirty, and a dirty tree cannot confirm anything. Ignoring it is what
        projects actually do with generated output, and it is what makes the
        commit comparison decisive.
        """
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("graphify-out/\n")
        for args in (["add", "-A"], ["commit", "-qm", "ignore artefacts"]):
            subprocess.run(["git", "-C", self.repo] + args, env=env,
                           check=True, capture_output=True)

    def test_a_matching_commit_verifies(self):
        self.ignore_the_artefact()
        write_graph(self.repo, {"source_commit": head(self.repo)})
        payload = index(self.repo, "graphify", self.symbols)
        upstream = payload["indexing"]["upstream"]
        self.assertEqual("verified", upstream["state"])
        self.assertEqual("source-commit-matches", upstream["reason"])

    def test_a_different_commit_is_stale_and_carries_the_answer_down(self):
        self.ignore_the_artefact()
        write_graph(self.repo, {"source_commit": "0" * 40})
        index(self.repo, "graphify", self.symbols)
        fresh, _vault = freshness_of(self.repo, self.symbols, self.tmp.name)
        self.assertEqual("stale", fresh["upstream"]["state"])
        self.assertEqual("stale", fresh["effective"])

    def test_an_uncommitted_change_since_that_commit_is_unknown(self):
        """Not `stale`: the artefact names the commit we are on, and the
        edits it could not have seen are absence of evidence rather than
        evidence of divergence. `stale` is a claim this cannot support."""
        self.ignore_the_artefact()
        write_graph(self.repo, {"source_commit": head(self.repo)})
        with open(os.path.join(self.repo, "mod.py"), "a") as fh:
            fh.write("\n\ndef gamma(value):\n    return value\n")
        payload = index(self.repo, "graphify", self.symbols)
        upstream = payload["indexing"]["upstream"]
        self.assertEqual("unknown", upstream["state"])
        self.assertEqual("working-tree-modified-since-that-commit",
                         upstream["reason"])


class AnIndexFromBeforeThisVersion(unittest.TestCase):
    def test_a_missing_upstream_block_reads_as_unknown_not_fresh(self):
        """Fail closed on our own history too: an index written by 0.8 has
        no upstream block, and must not be promoted to verified by silence."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            symbols = os.path.join(tmp, "s.json")
            payload = index(repo, "scan", symbols)
            del payload["indexing"]["upstream"]
            with open(symbols, "w") as fh:
                json.dump(payload, fh)
            fresh, _vault = freshness_of(repo, symbols, tmp)
            self.assertEqual("unknown", fresh["upstream"]["state"])
            self.assertEqual("index-predates-upstream-provenance",
                             fresh["upstream"]["reason"])
            self.assertEqual("unknown", fresh["effective"])


class StatusAndDoctorSayItToo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name)
        write_graph(self.repo)
        with open(os.path.join(self.repo, "FINDINGS.md"), "w") as fh:
            fh.write("# Findings\n\n## U-01 — a finding [MEDIUM]\n"
                     "### STATUS: OPEN\n\n`alpha` is named here.\n")
        self.symbols = os.path.join(self.repo, ".tracelink", "symbols.json")
        os.makedirs(os.path.dirname(self.symbols), exist_ok=True)
        index(self.repo, "graphify", self.symbols)
        _fresh, self.vault = freshness_of(self.repo, self.symbols,
                                          self.tmp.name)

    def test_status_reports_both_and_flags_the_gap(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status.main(["--register", os.path.join(self.tmp.name,
                                                    "FINDINGS.md"),
                         "--vault", self.vault, "--symbols", self.symbols,
                         "--repo", self.repo, "--format", "json"])
        report = json.loads(buffer.getvalue())
        self.assertEqual("fresh", report["index"]["freshness"])
        self.assertEqual("unknown", report["index"]["upstream_freshness"])
        self.assertEqual("unknown", report["index"]["effective_freshness"])
        self.assertTrue(any("upstream-unknown" in p
                            for p in report["problems"]))

    def test_doctor_warns_but_does_not_fail_on_an_unverifiable_artefact(self):
        with open(os.path.join(self.repo, ".tracelink", "config.json"),
                  "w") as fh:
            json.dump({"backend": "graphify",
                       "symbols": ".tracelink/symbols.json"}, fh)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = doctor.main(["--repo", self.repo, "--json"])
        report = json.loads(buffer.getvalue())
        evidence = [c for c in report["checks"]
                    if "evidence" in c["check"]][0]
        self.assertEqual("warn", evidence["state"])
        self.assertEqual(0, code, "unverifiable is not unusable")


if __name__ == "__main__":
    unittest.main()
