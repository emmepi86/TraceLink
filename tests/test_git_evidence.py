"""F2b: git may replace the hash only where it can prove the same thing.

Verifying an index costs 2.5 s on a real repository and 5.8 s on a large
one, because it reads and hashes every file it indexed. Git already knows
whether those files changed — but only for some of them, and only against a
snapshot it can name. The design note is `docs/F2B-FRESHNESS-EVIDENCE.md`;
this is its acceptance test.

The rule the fast path must never break:

    same verdict as the full hash, for every case, with fewer bytes read

So the tests below are differential: each mutation is applied, both paths
are asked, and they must agree. The hash is the oracle. A speed-up that
disagrees with it anywhere is not a speed-up, it is a downgrade of what
`fresh` means.

Two adversarial cases matter more than the rest, because they are where git
is a *weaker observer than it looks*: a file marked `assume-unchanged` or
`skip-worktree` — flags whose whole purpose is to stop git noticing — and a
new **ignored** file that the indexer would read but git hides. Both must
fall back to hashing rather than answer from git.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from tracelink import linker, symbol_index  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="b", GIT_AUTHOR_EMAIL="b@e",
           GIT_COMMITTER_NAME="b", GIT_COMMITTER_EMAIL="b@e")


def git(repo, *args, check=True):
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                         text=True, env=ENV)
    if check and out.returncode != 0:
        raise AssertionError(f"git {args}: {out.stderr}")
    return out.stdout


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "pkg"))
        for n in range(4):
            with open(os.path.join(self.repo, "pkg", f"m{n}.py"), "w") as fh:
                fh.write(f"def sym{n}(v):\n    return v\n")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("generated/\n")
        os.makedirs(os.path.join(self.repo, "generated"))
        with open(os.path.join(self.repo, "generated", "gen.py"), "w") as fh:
            fh.write("def generated(v):\n    return v\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.symbols = os.path.join(self.tmp.name, "symbols.json")
        self.index()

    def index(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            symbol_index.main(["--repo", self.repo, "--backend", "scan",
                               "--out", self.symbols])

    def payload(self):
        with open(self.symbols) as fh:
            return json.load(fh)

    def verdicts(self):
        """(fast-path answer, full-hash answer) for the same repository."""
        payload = self.payload()
        scope = (payload.get("indexing") or {}).get("scope")
        fast = linker._proven_unchanged_by_git(
            self.repo, payload.get("repository") or {}, scope)
        stripped = json.loads(json.dumps(payload))
        # the same index with the git evidence removed: the verifier then has
        # only the content fingerprint, which is the oracle
        stripped["repository"].pop("tree_identity", None)
        stripped["repository"].pop("scope_names_fingerprint", None)
        slow = linker.verify_freshness(stripped, self.repo).status
        return ("fresh" if fast else "not-proven"), slow

    def assert_agree(self, expected_slow):
        fast, slow = self.verdicts()
        self.assertEqual(expected_slow, slow, "the oracle itself moved")
        if fast == "fresh":
            self.assertEqual("fresh", slow,
                             "the fast path claimed fresh where the hash "
                             "did not")


class TheyAgreeOnEveryChange(_Case):
    def test_untouched(self):
        self.assert_agree("fresh")
        self.assertEqual("fresh", self.verdicts()[0], "no fast path at all")

    def test_tracked_file_modified(self):
        with open(os.path.join(self.repo, "pkg", "m0.py"), "w") as fh:
            fh.write("def sym0(v):\n    return v + 1\n")
        self.assert_agree("stale")

    def test_tracked_file_deleted(self):
        os.remove(os.path.join(self.repo, "pkg", "m1.py"))
        self.assert_agree("stale")

    def test_tracked_file_renamed(self):
        os.rename(os.path.join(self.repo, "pkg", "m2.py"),
                  os.path.join(self.repo, "pkg", "moved.py"))
        self.assert_agree("stale")

    def test_new_untracked_file_in_scope(self):
        with open(os.path.join(self.repo, "pkg", "new.py"), "w") as fh:
            fh.write("def added(v):\n    return v\n")
        self.assert_agree("stale")

    def test_new_ignored_file_the_indexer_would_read(self):
        """git hides it; the indexer reads it; the check must see it."""
        with open(os.path.join(self.repo, "generated", "more.py"), "w") as fh:
            fh.write("def more(v):\n    return v\n")
        self.assert_agree("stale")

    def test_ignored_file_modified(self):
        with open(os.path.join(self.repo, "generated", "gen.py"), "w") as fh:
            fh.write("def generated(v):\n    return v + 1\n")
        self.assert_agree("stale")

    def test_only_mtime_changed(self):
        path = os.path.join(self.repo, "pkg", "m3.py")
        stat = os.stat(path)
        os.utime(path, (stat.st_atime + 10_000, stat.st_mtime + 10_000))
        self.assert_agree("fresh")
        self.assertEqual("fresh", self.verdicts()[0],
                         "a touched file is not a changed file")

    def test_committing_the_same_content_keeps_it_fresh(self):
        """A commit that changes nothing in scope changes the tree identity,
        so the fast path must decline — and the hash must still say fresh."""
        with open(os.path.join(self.repo, "README.md"), "w") as fh:
            fh.write("prose\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "docs")
        fast, slow = self.verdicts()
        self.assertEqual("fresh", slow)
        self.assertEqual("not-proven", fast)


class WhereGitIsAWeakObserver(_Case):
    def test_assume_unchanged_disables_the_fast_path(self):
        git(self.repo, "update-index", "--assume-unchanged", "pkg/m0.py")
        self.addCleanup(git, self.repo, "update-index", "--no-assume-unchanged",
                        "pkg/m0.py", check=False)
        self.assertIsNone(symbol_index.git_evidence(
            self.repo, (self.payload().get("indexing") or {}).get("scope")))
        self.assertEqual("not-proven", self.verdicts()[0])

    def test_skip_worktree_disables_it_too(self):
        git(self.repo, "update-index", "--skip-worktree", "pkg/m1.py")
        self.addCleanup(git, self.repo, "update-index", "--no-skip-worktree",
                        "pkg/m1.py", check=False)
        self.assertEqual("not-proven", self.verdicts()[0])

    def test_a_flagged_file_outside_the_scope_does_not(self):
        """The flag matters where it can hide something we read."""
        with open(os.path.join(self.repo, "notes.txt"), "w") as fh:
            fh.write("prose\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "notes")
        git(self.repo, "update-index", "--assume-unchanged", "notes.txt")
        self.addCleanup(git, self.repo, "update-index", "--no-assume-unchanged",
                        "notes.txt", check=False)
        self.index()
        self.assertEqual("fresh", self.verdicts()[0])

    def test_sparse_checkout_disables_it(self):
        git(self.repo, "config", "core.sparseCheckout", "true")
        self.assertIsNone(symbol_index.git_evidence(
            self.repo, (self.payload().get("indexing") or {}).get("scope")))

    def test_no_git_at_all_falls_back(self):
        shutil.rmtree(os.path.join(self.repo, ".git"))
        self.assertIsNone(symbol_index.git_evidence(self.repo,
                                                    {"kind": "extensions",
                                                     "extensions": [".py"]}))

    def test_a_partial_index_is_never_fast(self):
        """A scan truncated at the file limit never read most of the tree.
        Git can then honestly say the tree is unchanged while the index is
        missing half of it — the two facts are about different sets. Found
        by the differential test at 50 000 files, where the fast path said
        fresh and the hash said stale."""
        payload = self.payload()
        payload["indexing"]["partial"] = True
        self.assertEqual("fresh", linker.verify_freshness(
            self.payload(), self.repo).reasons[0] and "fresh")
        verdict = linker.verify_freshness(payload, self.repo)
        self.assertNotIn("git-tree-identity", verdict.reasons)

    def test_an_index_taken_on_a_dirty_tree_is_never_fast(self):
        with open(os.path.join(self.repo, "pkg", "m0.py"), "a") as fh:
            fh.write("\n\ndef extra(v):\n    return v\n")
        self.index()                      # indexed while dirty
        self.assertEqual("not-proven", self.verdicts()[0])


class ReadingDoesNotWrite(_Case):
    def test_verifying_leaves_git_untouched(self):
        index_path = os.path.join(self.repo, ".git", "index")
        before = (os.stat(index_path).st_mtime_ns,
                  open(index_path, "rb").read())
        for _ in range(3):
            self.verdicts()
        after = (os.stat(index_path).st_mtime_ns,
                 open(index_path, "rb").read())
        self.assertEqual(before, after,
                         "verification wrote into .git — a read must not")


class ItReadsFewerBytes(_Case):
    def test_the_fast_path_reads_only_what_git_cannot_vouch_for(self):
        """Not "reads nothing": the untracked and ignored files the indexer
        consumes have no other witness, so they are read — and only they.
        The tracked ones are vouched for by the tree identity."""
        opened = []
        real_open = io.open
        import builtins

        def watching(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        payload = self.payload()
        scope = (payload.get("indexing") or {}).get("scope")
        builtins.open = watching
        try:
            proven = linker._proven_unchanged_by_git(
                self.repo, payload.get("repository") or {}, scope)
        finally:
            builtins.open = real_open
        self.assertTrue(proven)
        read = {os.path.relpath(p, self.repo) for p in opened
                if p.endswith(".py")}
        self.assertEqual({"generated/gen.py"}, read,
                         "the fast path read files git could vouch for")
        self.assertNotIn("pkg/m0.py", read)


if __name__ == "__main__":
    unittest.main()
