"""`tracelink hook` manages a git post-commit hook, and only its own lines.

The contract under test: the installed block lives between the exact markers
`# >>> tracelink hook v1 >>>` and `# <<< tracelink hook <<<`, installing is
idempotent, a pre-existing hook's own content survives both install and
remove byte for byte, the block is valid /bin/sh, and outside a git
repository the command fails politely with exit 2, never a traceback.

    python3 -m unittest discover tests -v
"""

import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

START = "# >>> tracelink hook v1 >>>"
END = "# <<< tracelink hook <<<"

CUSTOM = "#!/bin/sh\n# hand-written\necho custom-hook-ran\n"


def _run(module_main, argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", list(argv)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = module_main()
    return rc, out.getvalue(), err.getvalue()


class _HookCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "-C", self.repo, "init", "-q"],
                       check=True, capture_output=True)
        self.hook_path = os.path.join(self.repo, ".git", "hooks", "post-commit")

    def hook(self, *argv):
        from tracelink import hook
        return _run(hook.main, ["tracelink hook", *argv])

    def install(self, *extra):
        return self.hook("install", "--repo", self.repo, *extra)

    def read(self):
        return open(self.hook_path).read()


class InstallOnACleanRepo(_HookCase):
    def test_creates_an_executable_hook_with_markers(self):
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(os.path.isfile(self.hook_path))
        self.assertTrue(os.stat(self.hook_path).st_mode & stat.S_IXUSR)
        text = self.read()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn(START, text)
        self.assertIn(END, text)

    def test_the_block_names_the_paths_given_at_install_time(self):
        vault = os.path.join(self.repo, "notes")
        syms = os.path.join(self.repo, "sym.json")
        rc, out, err = self.install("--vault", vault, "--symbols", syms)
        self.assertEqual(rc, 0, out + err)
        text = self.read()
        self.assertIn(vault, text)
        self.assertIn(syms, text)

    def test_the_generated_hook_is_valid_sh(self):
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        r = subprocess.run(["sh", "-n", self.hook_path],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_awkward_paths_are_quoted_so_the_hook_stays_valid_sh(self):
        vault = os.path.join(self.repo, "my vault's $notes")
        rc, out, err = self.install("--vault", vault)
        self.assertEqual(rc, 0, out + err)
        r = subprocess.run(["sh", "-n", self.hook_path],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_block_never_blocks_the_commit(self):
        """Every command line in the block must end in `|| true` and send
        stdout to /dev/null — a refresh failure is not a commit failure."""
        self.install()
        text = self.read()
        block = text[text.index(START):text.index(END)]
        for line in block.splitlines():
            if line.strip().startswith("tracelink "):
                self.assertIn("|| true", line)
                self.assertIn(">/dev/null", line)


class InstallIsIdempotent(_HookCase):
    def test_two_installs_produce_identical_bytes(self):
        self.install()
        first = self.read()
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.read(), first)

    def test_reinstall_with_new_paths_rewrites_the_block_not_adds_one(self):
        self.install()
        vault = os.path.join(self.repo, "elsewhere")
        rc, out, err = self.install("--vault", vault)
        self.assertEqual(rc, 0, out + err)
        text = self.read()
        self.assertEqual(text.count(START), 1)
        self.assertEqual(text.count(END), 1)
        self.assertIn(vault, text)


class SomebodyElsesHookLinesAreNotOurs(_HookCase):
    def setUp(self):
        super().setUp()
        with open(self.hook_path, "w") as fh:
            fh.write(CUSTOM)
        os.chmod(self.hook_path, 0o700)

    def test_custom_content_survives_install(self):
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        text = self.read()
        self.assertTrue(text.startswith(CUSTOM),
                        "custom lines must stay first and untouched")
        self.assertIn(START, text)

    def test_install_preserves_the_existing_permissions(self):
        self.install()
        self.assertEqual(stat.S_IMODE(os.stat(self.hook_path).st_mode), 0o700)

    def test_custom_content_survives_install_then_remove_byte_for_byte(self):
        self.install()
        rc, out, err = self.hook("remove", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.read(), CUSTOM)
        self.assertTrue(os.path.isfile(self.hook_path))


class RemoveIsANoOpWhenThereIsNothingToRemove(_HookCase):
    def test_no_hook_file_at_all(self):
        rc, out, err = self.hook("remove", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("nothing to remove", out + err)

    def test_hook_file_without_our_markers(self):
        with open(self.hook_path, "w") as fh:
            fh.write(CUSTOM)
        rc, out, err = self.hook("remove", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("nothing to remove", out + err)
        self.assertEqual(self.read(), CUSTOM)


class OutsideAGitRepositoryTheAnswerIsTwo(_HookCase):
    def test_install_fails_politely(self):
        plain = os.path.join(self._tmp.name, "not-a-repo")
        os.makedirs(plain)
        rc, out, err = self.hook("install", "--repo", plain)
        self.assertEqual(rc, 2)
        self.assertIn("not a git repository", out + err)
        self.assertNotIn("Traceback", out + err)

    def test_status_fails_politely_too(self):
        plain = os.path.join(self._tmp.name, "not-a-repo")
        os.makedirs(plain)
        rc, out, err = self.hook("status", "--repo", plain)
        self.assertEqual(rc, 2)
        self.assertIn("not a git repository", out + err)
        self.assertNotIn("Traceback", out + err)


class StatusTellsTheTruthAboutTheHookFile(_HookCase):
    def test_not_installed(self):
        rc, out, err = self.hook("status", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("installed:", out)
        self.assertIn("no", out)

    def test_installed_reports_the_interpolated_paths(self):
        vault = os.path.join(self.repo, "my notes")
        self.install("--vault", vault)
        rc, out, err = self.hook("status", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("yes", out)
        self.assertIn(vault, out)

    def test_foreign_content_is_pointed_out(self):
        with open(self.hook_path, "w") as fh:
            fh.write(CUSTOM)
        self.install()
        rc, out, err = self.hook("status", "--repo", self.repo)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("non-tracelink", out)


class TheInstalledHookActuallyRuns(_HookCase):
    def test_post_commit_executes_the_block_without_breaking_the_commit(self):
        """A commit in a repo with the hook installed but no `tracelink` on
        PATH and no vault must succeed silently — the no-op guarantees."""
        self.install()
        subprocess.run(["git", "-C", self.repo, "config", "user.email", "t@t"],
                       check=True)
        subprocess.run(["git", "-C", self.repo, "config", "user.name", "t"],
                       check=True)
        open(os.path.join(self.repo, "f.txt"), "w").write("x\n")
        subprocess.run(["git", "-C", self.repo, "add", "f.txt"], check=True,
                       capture_output=True)
        env = dict(os.environ, PATH="/usr/bin:/bin")
        r = subprocess.run(["git", "-C", self.repo, "commit", "-qm", "x"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TheCliDispatchesHook(_HookCase):
    def test_tracelink_hook_reaches_the_module(self):
        from tracelink import cli
        out = io.StringIO()
        with mock.patch.object(sys, "argv", list(sys.argv)), \
                contextlib.redirect_stdout(out):
            rc = cli.main(["hook", "install", "--repo", self.repo])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self.hook_path))


if __name__ == "__main__":
    unittest.main()
