"""0.6.0 — the plugin refresh hooks: mark-stale on edit, refresh at end of turn.

Two moments, two very different budgets. `mark` runs after EVERY edit, so it
must do nothing but a directory test and a touch — no tracelink import, no
walking. `refresh` runs once per turn, only when the marker says something
changed, and it rebuilds the index and relinks the vault in-process.

The one non-negotiable property is that neither mode may ever break Claude:
exit 0 on success, exit 0 on failure, exit 0 on a repository too large to
refresh — with the marker removed in every case, because a marker that
survives a skipped refresh retries the same skip on every following turn.

    python3 -m unittest discover tests -v
"""

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
HOOKS = os.path.join(ROOT, "hooks")
REFRESH = os.path.join(SCRIPTS, "plugin_refresh.py")
SPLIT = os.path.join(SCRIPTS, "split.py")

sys.path.insert(0, SCRIPTS)

import plugin_refresh  # noqa: E402


def run_hook(mode, project, env_extra=None):
    """Invoke plugin_refresh.py the way the wrapper scripts do."""
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("TRACELINK_PROJECT_DIR", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, REFRESH, mode, project],
        capture_output=True, text=True, env=env, timeout=120)


def make_toy_project(tmp):
    """A minimal project in the examples/ mould: one source file, one finding
    that names a symbol defined in it, split into a real vault."""
    proj = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "app.py"), "w") as fh:
        fh.write("def compute_total(items):\n"
                 "    \"\"\"Sum line totals. Ignores tax — see RES-01.\"\"\"\n"
                 "    return sum(items)\n")
    register = os.path.join(proj, "FINDINGS.md")
    with open(register, "w") as fh:
        fh.write("# Findings register\n\n"
                 "## RES-01 — totals ignore tax [HIGH]\n"
                 "### STATUS: OPEN\n"
                 "`compute_total` sums line items but never applies tax.\n")
    vault = os.path.join(proj, ".tracelink", "vault")
    r = subprocess.run(
        [sys.executable, SPLIT, "--register", register, "--out", vault,
         "--prefix", "RES"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"split failed:\n{r.stdout}\n{r.stderr}"
    return proj


def touch_marker(proj):
    path = os.path.join(proj, ".tracelink", ".stale")
    open(path, "w").close()
    return path


class TestMark(unittest.TestCase):
    """After every edit; the budget is a stat and a touch."""

    def test_without_tracelink_dir_is_a_silent_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_hook("mark", tmp)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")
        # and it must not have CREATED .tracelink in a project not using it
        self.assertFalse(os.path.exists(os.path.join(tmp, ".tracelink")))

    def test_with_tracelink_dir_touches_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".tracelink"))
            r = run_hook("mark", tmp)
            marker = os.path.join(tmp, ".tracelink", ".stale")
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.exists(marker))
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")

    def test_project_dir_read_from_claude_env(self):
        """The hooks pass no argument; CLAUDE_PROJECT_DIR is the contract."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".tracelink"))
            env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run([sys.executable, REFRESH, "mark"],
                               capture_output=True, text=True, env=env,
                               timeout=120)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(
                os.path.exists(os.path.join(tmp, ".tracelink", ".stale")))

    def test_mark_never_imports_tracelink(self):
        """The <10ms promise is really 'no import'; test the cause, not the
        wall clock, which measures the machine and not the code."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".tracelink"))
            probe = ("import sys; sys.path.insert(0, sys.argv[1]); "
                     "import plugin_refresh; "
                     "plugin_refresh.main(['mark', sys.argv[2]]); "
                     "print(any(m == 'tracelink' or m.startswith('tracelink.') "
                     "for m in sys.modules))")
            r = subprocess.run([sys.executable, "-c", probe, SCRIPTS, tmp],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "False",
                             "mark must not pay for a tracelink import")


class TestRefresh(unittest.TestCase):
    """Once per turn, and only when the marker says an edit happened."""

    def test_without_marker_is_a_silent_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            r = run_hook("refresh", proj)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")
            self.assertEqual(r.stderr, "")
            self.assertFalse(os.path.exists(
                os.path.join(proj, ".tracelink", "symbols.json")),
                "no marker means no work, not cheap-looking work")

    def test_with_marker_indexes_links_and_removes_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            marker = touch_marker(proj)
            r = run_hook("refresh", proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(marker), "marker must be consumed")
            symbols = os.path.join(proj, ".tracelink", "symbols.json")
            self.assertTrue(os.path.exists(symbols), "index did not run")
            with open(symbols) as fh:
                payload = json.load(fh)
            self.assertIn("compute_total", payload.get("symbols", {}))
            # link ran: the note now points at the definition
            vault = os.path.join(proj, ".tracelink", "vault")
            note = [p for p in os.listdir(vault)
                    if p.endswith(".md") and p not in ("INDEX.md",
                                                       "CODE-INDEX.md")]
            self.assertTrue(note)
            with open(os.path.join(vault, note[0])) as fh:
                body = fh.read()
            self.assertIn("src/app.py", body, "linker did not run on the note")

    def test_second_refresh_after_an_edit_updates_the_index(self):
        """The whole point of the hook: an edit must not leave stale links."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            touch_marker(proj)
            run_hook("refresh", proj)
            with open(os.path.join(proj, "src", "app.py"), "a") as fh:
                fh.write("\n\ndef apply_tax(total):\n    return total\n")
            touch_marker(proj)
            r = run_hook("refresh", proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(proj, ".tracelink", "symbols.json")) as fh:
                payload = json.load(fh)
            self.assertIn("apply_tax", payload.get("symbols", {}))

    def test_marker_without_vault_is_consumed_quietly(self):
        """tracelink dir exists but split has not run yet: nothing to refresh,
        but the marker must still go, or every turn re-discovers the nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".tracelink"))
            marker = touch_marker(tmp)
            r = run_hook("refresh", tmp)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, "")
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(
                os.path.join(tmp, ".tracelink", "symbols.json")))

    def test_over_threshold_skips_with_hint_but_removes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            marker = touch_marker(proj)
            old = plugin_refresh.MAX_FILES
            err = io.StringIO()
            try:
                plugin_refresh.MAX_FILES = 0
                with contextlib.redirect_stderr(err):
                    rc = plugin_refresh.main(["refresh", proj])
            finally:
                plugin_refresh.MAX_FILES = old
            self.assertEqual(rc, 0)
            self.assertFalse(os.path.exists(marker),
                             "an un-consumed marker retries the skip forever")
            self.assertFalse(os.path.exists(
                os.path.join(proj, ".tracelink", "symbols.json")))
            self.assertIn("source files", err.getvalue(),
                          "the skip must say why, once")

    def test_a_failing_refresh_still_exits_zero(self):
        """Vault present, nothing indexable: whatever goes wrong inside stays
        inside. A hook that returns non-zero blocks Claude — never."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            os.remove(os.path.join(proj, "src", "app.py"))  # index will fail
            marker = touch_marker(proj)
            r = run_hook("refresh", proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(marker))

    def test_unreadable_project_never_propagates(self):
        r = run_hook("refresh", os.path.join(os.sep, "nonexistent", "nowhere"))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestHookManifest(unittest.TestCase):
    """hooks/hooks.json is auto-discovered; its shape IS the interface."""

    def setUp(self):
        with open(os.path.join(HOOKS, "hooks.json")) as fh:
            self.data = json.load(fh)

    def test_top_level_shape(self):
        self.assertIn("hooks", self.data)
        self.assertIn("PostToolUse", self.data["hooks"])
        self.assertIn("Stop", self.data["hooks"])

    def test_post_tool_use_watches_the_three_edit_tools(self):
        entry = self.data["hooks"]["PostToolUse"][0]
        self.assertEqual(entry["matcher"], "Edit|Write|MultiEdit")

    def test_commands_are_exec_form_with_timeouts(self):
        """${CLAUDE_PLUGIN_ROOT} only expands in command+args exec form, so
        anything else here would silently run nothing."""
        for event, wrapper, timeout in (("PostToolUse", "on-edit.sh", 10),
                                        ("Stop", "on-stop.sh", 120)):
            hook = self.data["hooks"][event][0]["hooks"][0]
            self.assertEqual(hook["type"], "command")
            self.assertEqual(
                hook["command"],
                "${CLAUDE_PLUGIN_ROOT}/hooks/" + wrapper)
            self.assertEqual(hook["args"], [])
            self.assertEqual(hook["timeout"], timeout)


class TestWrapperScripts(unittest.TestCase):
    def test_wrappers_are_executable_posix_sh(self):
        for name in ("on-edit.sh", "on-stop.sh"):
            path = os.path.join(HOOKS, name)
            self.assertTrue(os.path.exists(path), path)
            mode = os.stat(path).st_mode
            self.assertTrue(mode & stat.S_IXUSR, f"{name} is not executable")
            with open(path) as fh:
                first = fh.readline()
            self.assertTrue(first.startswith("#!/bin/sh"), name)
            r = subprocess.run(["sh", "-n", path], capture_output=True,
                               text=True, timeout=120)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_wrappers_drain_stdin_and_mark_marks(self):
        """Run on-edit.sh exactly as Claude Code would: JSON on stdin,
        CLAUDE_PROJECT_DIR in the environment."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".tracelink"))
            env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run([os.path.join(HOOKS, "on-edit.sh")],
                               input='{"tool_name": "Edit"}',
                               capture_output=True, text=True, env=env,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(
                os.path.exists(os.path.join(tmp, ".tracelink", ".stale")))

    def test_on_stop_refreshes_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_toy_project(tmp)
            touch_marker(proj)
            env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run([os.path.join(HOOKS, "on-stop.sh")],
                               input="{}", capture_output=True, text=True,
                               env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(
                os.path.join(proj, ".tracelink", "symbols.json")))
            self.assertFalse(os.path.exists(
                os.path.join(proj, ".tracelink", ".stale")))


if __name__ == "__main__":
    unittest.main()
