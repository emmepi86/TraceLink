"""0.7.0 — capture: the agent that edited code is sent back to the register.

The Stop hook grows a memory-loop conscience: when a session edited code and
the findings register did not grow, the stop is blocked ONCE with an
instruction to distill durable discoveries — `{"decision": "block", "reason"}`
on stdout, exit 0, exactly the command-hook contract Claude Code documents
for Stop. Everything else is silence.

The moving parts are three markers in `.tracelink/`, all distinct from
`.stale` (which refresh consumes every turn):

  .session-edits     an edit happened while capture was on
  .capture-baseline  size+mtime+sha of the register BEFORE the session's
                     first edit — written by mark, so growth is measured
                     against the pre-session register, not a mid-session one
  .capture-prompted  the one prompt was already spent

Non-negotiables, same as every other hook path: exit 0 always, the register
is never written, `stop_hook_active` short-circuits everything (anti-loop),
and a project that never opted in pays nothing.

    python3 -m unittest discover tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
HOOKS = os.path.join(ROOT, "hooks")
REFRESH = os.path.join(SCRIPTS, "plugin_refresh.py")

sys.path.insert(0, SCRIPTS)

import plugin_refresh  # noqa: E402  (imported for constants, same as siblings)

REGISTER_TEXT = ("# Findings register\n\n"
                 "## RES-01 — totals ignore tax [HIGH]\n"
                 "### STATUS: OPEN\n"
                 "`compute_total` sums line items but never applies tax.\n")

NEW_FINDING = ("\n## RES-02 — tax rate is hardcoded\n"
               "### STATUS: OPEN\n"
               "`apply_tax` hardcodes 22% instead of reading config.\n")


def make_project(tmp, config={"capture": True}, register=REGISTER_TEXT,
                 register_name="FINDINGS.md"):
    """A project with `.tracelink/` and (usually) a register.

    `config`: dict written as json, or None for no config file.
    `register`: text, or None for a project whose register does not exist yet.
    """
    proj = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(proj, ".tracelink"))
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "app.py"), "w") as fh:
        fh.write("def compute_total(items):\n    return sum(items)\n")
    if register is not None:
        reg_path = os.path.join(proj, register_name)
        os.makedirs(os.path.dirname(reg_path), exist_ok=True)
        with open(reg_path, "w") as fh:
            fh.write(register)
    if config is not None:
        with open(os.path.join(proj, ".tracelink", "config.json"), "w") as fh:
            json.dump(config, fh)
    return proj


def run_mode(mode, proj, stdin_text=""):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("TRACELINK_PROJECT_DIR", None)
    return subprocess.run(
        [sys.executable, REFRESH, mode, proj],
        input=stdin_text, capture_output=True, text=True, env=env,
        timeout=120)


def stop_payload(active=False):
    return json.dumps({"session_id": "abc123", "hook_event_name": "Stop",
                       "stop_hook_active": active})


def marker(proj, name):
    return os.path.join(proj, ".tracelink", name)


SESSION = ".session-edits"
BASELINE = ".capture-baseline"
PROMPTED = ".capture-prompted"


class TestMarkWritesTheSessionState(unittest.TestCase):
    """mark is where the baseline is frozen — before the session changes
    anything, on the FIRST edit only."""

    def test_first_mark_touches_session_and_freezes_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            r = run_mode("mark", proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertEqual(r.stderr, "")
            self.assertTrue(os.path.exists(marker(proj, ".stale")))
            self.assertTrue(os.path.exists(marker(proj, SESSION)))
            with open(marker(proj, BASELINE)) as fh:
                base = json.load(fh)
            self.assertIs(base.get("exists"), True)
            self.assertEqual(base.get("size"),
                             len(REGISTER_TEXT.encode("utf-8")))
            self.assertIn("sha256", str(base.get("sha256")))

    def test_second_mark_keeps_the_pre_session_baseline(self):
        """The baseline must describe the register BEFORE the session — a
        later edit of the register must not move the yardstick."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            run_mode("mark", proj)
            with open(marker(proj, BASELINE)) as fh:
                first = json.load(fh)
            with open(os.path.join(proj, "FINDINGS.md"), "a") as fh:
                fh.write(NEW_FINDING)
            run_mode("mark", proj)
            with open(marker(proj, BASELINE)) as fh:
                second = json.load(fh)
            self.assertEqual(first, second)

    def test_capture_off_leaves_no_session_state(self):
        for config in ({"capture": False}, {}, None):
            with tempfile.TemporaryDirectory() as tmp:
                proj = make_project(tmp, config=config)
                r = run_mode("mark", proj)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(os.path.exists(marker(proj, ".stale")))
                self.assertFalse(os.path.exists(marker(proj, SESSION)))
                self.assertFalse(os.path.exists(marker(proj, BASELINE)))

    def test_absent_register_baselines_as_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, register=None)
            run_mode("mark", proj)
            with open(marker(proj, BASELINE)) as fh:
                base = json.load(fh)
            self.assertIs(base.get("exists"), False)

    def test_custom_register_path_is_baselined(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(
                tmp, config={"capture": True, "register": "docs/NOTES.md"},
                register_name="docs/NOTES.md")
            run_mode("mark", proj)
            with open(marker(proj, BASELINE)) as fh:
                base = json.load(fh)
            self.assertIs(base.get("exists"), True)
            self.assertEqual(base.get("size"),
                             len(REGISTER_TEXT.encode("utf-8")))


def edited_session(proj):
    """Simulate the session's edits the way Claude Code causes them."""
    r = run_mode("mark", proj)
    assert r.returncode == 0, r.stderr
    return proj


class TestCaptureCheckBlocks(unittest.TestCase):
    """Edits happened, the register did not grow: block once, with the
    distillation instruction as the reason."""

    def check(self, proj, active=False):
        return run_mode("capture-check", proj, stop_payload(active))

    def test_unchanged_register_blocks_with_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            r = self.check(proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["decision"], "block")
            reason = out["reason"]
            self.assertIn("TraceLink capture", reason)
            self.assertIn("FINDINGS.md", reason)
            self.assertIn("### RES-", reason)
            self.assertIn("STATUS", reason)
            self.assertIn("tracelink lint --register FINDINGS.md", reason)
            self.assertTrue(os.path.exists(marker(proj, PROMPTED)))

    def test_absent_register_counts_as_not_grown(self):
        """No register at all is the strongest case FOR the prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp, register=None))
            r = self.check(proj)
            out = json.loads(r.stdout)
            self.assertEqual(out["decision"], "block")

    def test_one_prompt_per_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            first = self.check(proj)
            self.assertEqual(json.loads(first.stdout)["decision"], "block")
            second = self.check(proj)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout, "")

    def test_reason_names_the_configured_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(
                tmp, config={"capture": True, "register": "docs/NOTES.md"},
                register_name="docs/NOTES.md")
            edited_session(proj)
            r = self.check(proj)
            reason = json.loads(r.stdout)["reason"]
            self.assertIn("docs/NOTES.md", reason)
            self.assertIn("tracelink lint --register docs/NOTES.md", reason)

    def test_reason_uses_the_vault_manifest_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            vault = os.path.join(proj, ".tracelink", "vault")
            os.makedirs(vault)
            with open(os.path.join(vault, ".tracelink-manifest.json"),
                      "w") as fh:
                json.dump({"schema_version": 2,
                           "register": {"prefix": "BUG",
                                        "source": "FINDINGS.md"},
                           "generated_notes": []}, fh)
            edited_session(proj)
            reason = json.loads(self.check(proj).stdout)["reason"]
            self.assertIn("### BUG-", reason)
            self.assertIn("--vault .tracelink/vault", reason)


class TestCaptureCheckStaysSilent(unittest.TestCase):
    """Every gate that must NOT block: empty stdout, exit 0, no traceback."""

    def assert_silent(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("Traceback", r.stderr)

    def check(self, proj, stdin_text=None):
        return run_mode("capture-check", proj,
                        stop_payload() if stdin_text is None else stdin_text)

    def test_capture_off_or_absent_passes(self):
        for config in ({"capture": False}, {}, None):
            with tempfile.TemporaryDirectory() as tmp:
                proj = make_project(tmp, config=config)
                # markers present but the gate is closed: nothing may happen
                open(marker(proj, SESSION), "w").close()
                self.assert_silent(self.check(proj))
                self.assertFalse(os.path.exists(marker(proj, PROMPTED)))

    def test_stop_hook_active_always_passes(self):
        """The anti-loop rule beats every other consideration."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            r = run_mode("capture-check", proj, stop_payload(active=True))
            self.assert_silent(r)
            self.assertFalse(os.path.exists(marker(proj, PROMPTED)),
                             "an active stop hook must not even spend "
                             "the session's one prompt")

    def test_no_session_edits_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            self.assert_silent(self.check(proj))
            self.assertFalse(os.path.exists(marker(proj, PROMPTED)))

    def test_grown_register_passes_and_cleans_the_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            with open(os.path.join(proj, "FINDINGS.md"), "a") as fh:
                fh.write(NEW_FINDING)
            self.assert_silent(self.check(proj))
            for name in (SESSION, BASELINE, PROMPTED):
                self.assertFalse(os.path.exists(marker(proj, name)),
                                 f"{name} must not survive a grown register")

    def test_register_created_during_session_counts_as_grown(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp, register=None))
            with open(os.path.join(proj, "FINDINGS.md"), "w") as fh:
                fh.write(REGISTER_TEXT)
            self.assert_silent(self.check(proj))
            self.assertFalse(os.path.exists(marker(proj, SESSION)))

    def test_growth_after_the_prompt_cleans_up_too(self):
        """Prompt, distill, stop again (a later turn): the cycle closes."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            self.assertIn("block", self.check(proj, stop_payload()).stdout)
            with open(os.path.join(proj, "FINDINGS.md"), "a") as fh:
                fh.write(NEW_FINDING)
            self.assert_silent(self.check(proj))
            for name in (SESSION, BASELINE, PROMPTED):
                self.assertFalse(os.path.exists(marker(proj, name)))

    def test_next_session_gets_a_fresh_prompt_after_a_closed_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            self.check(proj)  # prompt spent
            with open(os.path.join(proj, "FINDINGS.md"), "a") as fh:
                fh.write(NEW_FINDING)
            self.check(proj)  # growth observed, markers cleaned
            edited_session(proj)  # a new session edits again
            r = self.check(proj)
            self.assertEqual(json.loads(r.stdout)["decision"], "block")

    def test_project_without_tracelink_dir_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_silent(self.check(tmp))

    def test_corrupt_stdin_never_breaks_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            r = self.check(proj, "this is not json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_corrupt_baseline_never_breaks_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            with open(marker(proj, BASELINE), "w") as fh:
                fh.write("{{ not json")
            r = self.check(proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)


class TestCaptureNeverWritesTheRegister(unittest.TestCase):
    def test_register_bytes_are_untouched_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp)
            reg = os.path.join(proj, "FINDINGS.md")
            with open(reg, "rb") as fh:
                before = fh.read()
            run_mode("mark", proj)
            run_mode("capture-check", proj, stop_payload())
            run_mode("capture-check", proj, stop_payload())
            with open(reg, "rb") as fh:
                self.assertEqual(fh.read(), before)


class TestCaptureCheckStillRefreshes(unittest.TestCase):
    """The Stop hook's original duty rides in the same mode: the stale
    marker is consumed exactly as `refresh` alone would consume it."""

    def test_stale_marker_is_consumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(tmp, config=None)
            open(marker(proj, ".stale"), "w").close()
            r = run_mode("capture-check", proj, stop_payload())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(marker(proj, ".stale")))


class TestSessionClear(unittest.TestCase):
    """A new session must not inherit the previous one's spent prompt or
    stale baseline. SessionStart (matcher startup|clear) calls
    `session-clear`, which drops the three capture markers — otherwise ONE
    ignored prompt silences capture on the project forever: the next prompt
    would only come when the register grows, and growing the register is
    exactly what the missing prompt was supposed to cause."""

    def test_ignored_prompt_does_not_silence_the_next_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            first = run_mode("capture-check", proj, stop_payload())
            self.assertEqual(json.loads(first.stdout)["decision"], "block")
            # the agent ignores the prompt; the session ends; a new one starts
            r = run_mode("session-clear", proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertEqual(r.stderr, "")
            for name in (SESSION, BASELINE, PROMPTED):
                self.assertFalse(os.path.exists(marker(proj, name)),
                                 f"{name} survived the session boundary")
            edited_session(proj)  # session 2 edits and records nothing
            again = run_mode("capture-check", proj, stop_payload())
            self.assertEqual(json.loads(again.stdout)["decision"], "block",
                             "session 2 must get its own prompt")

    def test_noop_without_tracelink_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_mode("session-clear", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertEqual(r.stderr, "")
            self.assertFalse(os.path.exists(os.path.join(tmp, ".tracelink")))

    def test_stale_marker_is_not_session_clears_business(self):
        """`.stale` belongs to refresh; a session boundary says nothing
        about the freshness of the index."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            self.assertTrue(os.path.exists(marker(proj, ".stale")))
            run_mode("session-clear", proj)
            self.assertTrue(os.path.exists(marker(proj, ".stale")))

    def test_new_session_refreezes_the_baseline_after_late_growth(self):
        """The lazy-cleanup gap: session 1 is prompted, appends, but its
        growth is never observed (the continuation stop has
        stop_hook_active=true and no natural stop follows). Without the
        session boundary, session 2's first stop would spend the cleanup on
        the OLD session's growth and consume session 2's own edit marker.
        With it, session 2 starts clean, its baseline freezes the grown
        register, and its own silence earns its own prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            run_mode("capture-check", proj, stop_payload())  # prompt spent
            with open(os.path.join(proj, "FINDINGS.md"), "a") as fh:
                fh.write(NEW_FINDING)
            # continuation stop: anti-loop passes, growth NOT observed
            run_mode("capture-check", proj, stop_payload(active=True))
            run_mode("session-clear", proj)  # session ends here
            edited_session(proj)  # session 2 edits
            with open(marker(proj, BASELINE)) as fh:
                base = json.load(fh)
            grown = (REGISTER_TEXT + NEW_FINDING).encode("utf-8")
            self.assertEqual(base.get("size"), len(grown),
                             "the new baseline must describe the register "
                             "as session 2 found it, growth included")
            r = run_mode("capture-check", proj, stop_payload())
            self.assertEqual(json.loads(r.stdout)["decision"], "block",
                             "session 2 recorded nothing and must be told")


class TestSessionStartWiring(unittest.TestCase):
    """The manifest entry and the wrapper that make session-clear happen."""

    def test_manifest_has_session_start_for_new_sessions_only(self):
        with open(os.path.join(HOOKS, "hooks.json")) as fh:
            data = json.load(fh)
        entry = data["hooks"]["SessionStart"][0]
        self.assertEqual(entry["matcher"], "startup|clear",
                         "resume/compact are the SAME logical session — "
                         "clearing there would break one-prompt-per-session")
        hook = entry["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["command"],
                         "${CLAUDE_PLUGIN_ROOT}/hooks/on-session-start.sh")
        self.assertEqual(hook["args"], [])
        self.assertLessEqual(hook["timeout"], 10)

    def test_wrapper_is_executable_posix_sh(self):
        import stat
        path = os.path.join(HOOKS, "on-session-start.sh")
        self.assertTrue(os.path.exists(path), path)
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)
        with open(path) as fh:
            self.assertTrue(fh.readline().startswith("#!/bin/sh"))
        r = subprocess.run(["sh", "-n", path], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wrapper_clears_markers_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            run_mode("capture-check", proj, stop_payload())
            env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
            env.pop("TRACELINK_PROJECT_DIR", None)
            r = subprocess.run([os.path.join(HOOKS, "on-session-start.sh")],
                               input='{"hook_event_name": "SessionStart"}',
                               capture_output=True, text=True, env=env,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            for name in (SESSION, BASELINE, PROMPTED):
                self.assertFalse(os.path.exists(marker(proj, name)), name)


class TestOnStopWrapper(unittest.TestCase):
    """on-stop.sh used to drain stdin; now the payload must reach Python."""

    def run_wrapper(self, proj, stdin_text):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=proj)
        env.pop("TRACELINK_PROJECT_DIR", None)
        return subprocess.run([os.path.join(HOOKS, "on-stop.sh")],
                              input=stdin_text, capture_output=True,
                              text=True, env=env, timeout=120)

    def test_blocks_end_to_end_when_nothing_was_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            r = self.run_wrapper(proj, stop_payload())
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["decision"], "block")
            self.assertIn("FINDINGS.md", out["reason"])

    def test_stop_hook_active_passes_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = edited_session(make_project(tmp))
            r = self.run_wrapper(proj, stop_payload(active=True))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
