"""ms-7 and ms-8: `init` creates, `doctor` diagnoses, neither does the other.

`init` has one rule — it creates what is missing and touches nothing else —
and the test that matters is the second run: byte for byte, nothing changes.
A command that quietly repairs is a command nobody can run twice without
reading its source first, and the second run is exactly the one people
reach for when something already looks wrong.

`doctor` has the complementary rule: it reports and never writes. It also
has to stay distinguishable from `status` — memory health there, setup
health here — so its checks are about configuration, paths, backends and
schemas, and none of them is about whether a finding is stale.

Neither command has a `--fix`. That is deliberate and is asserted here, so
adding one is a decision somebody makes on purpose rather than a feature
that drifts in.
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

from tracelink import cli, config, doctor, init  # noqa: E402


def run(command, *argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main([command] + list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def checksums(root):
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = hashlib.sha256(
                    fh.read()).hexdigest()
    return out


class InitCreates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = self.tmp.name
        os.makedirs(os.path.join(self.proj, "src"))
        with open(os.path.join(self.proj, "src", "a.py"), "w") as fh:
            fh.write("def f():\n    return 1\n")

    def test_it_creates_the_minimum(self):
        code, out, _ = run("init", "--repo", self.proj)
        self.assertEqual(0, code)
        self.assertTrue(os.path.isdir(os.path.join(self.proj, ".tracelink")))
        self.assertTrue(os.path.exists(config.path(self.proj)))
        self.assertTrue(os.path.exists(os.path.join(self.proj,
                                                    "FINDINGS.md")))
        self.assertIn("created", out)
        self.assertIn("tracelink sync", out)

    def test_the_second_run_changes_nothing(self):
        run("init", "--repo", self.proj)
        before = checksums(self.proj)
        code, out, _ = run("init", "--repo", self.proj)
        self.assertEqual(0, code)
        self.assertEqual(before, checksums(self.proj))
        self.assertIn("kept", out)
        self.assertNotIn("created", out)

    def test_an_existing_config_is_never_merged_or_rewritten(self):
        os.makedirs(os.path.join(self.proj, ".tracelink"))
        mine = {"register": "NOTES.md", "prefix": "BUG", "mine": True}
        with open(config.path(self.proj), "w") as fh:
            json.dump(mine, fh)
        run("init", "--repo", self.proj)
        with open(config.path(self.proj)) as fh:
            self.assertEqual(mine, json.load(fh))

    def test_an_existing_register_is_never_touched(self):
        with open(os.path.join(self.proj, "FINDINGS.md"), "w") as fh:
            fh.write("# my own register\n")
        run("init", "--repo", self.proj)
        with open(os.path.join(self.proj, "FINDINGS.md")) as fh:
            self.assertEqual("# my own register\n", fh.read())

    def test_the_backend_is_detected_from_what_is_on_disk(self):
        self.assertEqual(("scan", init.detect_backend(self.proj)[1]),
                         init.detect_backend(self.proj))
        with open(os.path.join(self.proj, "tags"), "w") as fh:
            fh.write("!_TAG_FILE_FORMAT\t2\n")
        self.assertEqual("ctags", init.detect_backend(self.proj)[0])
        os.makedirs(os.path.join(self.proj, "graphify-out"))
        with open(os.path.join(self.proj, "graphify-out", "graph.json"),
                  "w") as fh:
            fh.write("{}")
        self.assertEqual("graphify", init.detect_backend(self.proj)[0])

    def test_the_detected_backend_is_written_into_the_config(self):
        with open(os.path.join(self.proj, "tags"), "w") as fh:
            fh.write("!_TAG_FILE_FORMAT\t2\n")
        run("init", "--repo", self.proj)
        self.assertEqual("ctags", config.read(self.proj)["backend"])

    def test_the_starter_register_survives_a_sync(self):
        run("init", "--repo", self.proj)
        code, out, err = run("sync", "--repo", self.proj)
        self.assertEqual(0, code, out + err)

    def test_the_json_document_lists_the_steps(self):
        _c, out, _ = run("init", "--repo", self.proj, "--json")
        doc = json.loads(out)
        self.assertEqual(1, doc["schema_version"])
        actions = {step["action"] for step in doc["steps"]}
        self.assertTrue(actions <= {"created", "kept", "detected"})

    def test_it_does_not_offer_to_repair(self):
        """Repair belongs to a decision, not to a flag that drifts in."""
        _c, out, _ = run("init", "--help")
        self.assertNotIn("--fix", out.partition("options:")[2])
        self.assertIn("Never overwrites", out)


class DoctorDiagnoses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = self.tmp.name
        os.makedirs(os.path.join(self.proj, "src"))
        with open(os.path.join(self.proj, "src", "a.py"), "w") as fh:
            fh.write("def compute_total():\n    return 1\n")
        run("init", "--repo", self.proj)
        run("sync", "--repo", self.proj)

    def states(self, *argv):
        code, out, _ = run("doctor", "--repo", self.proj, "--json", *argv)
        doc = json.loads(out)
        return code, doc, {c["check"]: c["state"] for c in doc["checks"]}

    def test_a_healthy_project_passes(self):
        code, doc, _s = self.states()
        self.assertEqual(0, code)
        self.assertIn(doc["state"], ("ok", "warn"))
        self.assertNotIn("fail", [c["state"] for c in doc["checks"]])

    def test_it_reports_the_link_state_schema_it_found(self):
        from tracelink.consult import STATE_SCHEMA
        _c, _doc, states = self.states()
        self.assertIn(f"link state schema {STATE_SCHEMA}", states)

    def test_a_backend_without_its_input_is_a_failure_with_a_remedy(self):
        with open(config.path(self.proj), "w") as fh:
            json.dump({"backend": "ctags"}, fh)
        code, doc, _s = self.states()
        self.assertEqual(doctor.EXIT_UNHEALTHY, code)
        ctags = [c for c in doc["checks"] if "ctags" in c["check"]][0]
        self.assertEqual("fail", ctags["state"])
        self.assertIn("ctags -R", ctags["remedy"])

    def test_a_key_nobody_reads_is_a_warning_not_a_failure(self):
        with open(config.path(self.proj), "w") as fh:
            json.dump({"backend": "scan", "invented": 1}, fh)
        code, doc, _s = self.states()
        self.assertEqual(0, code)
        keys = [c for c in doc["checks"] if "config keys" in c["check"]][0]
        self.assertEqual("warn", keys["state"])
        self.assertIn("invented", keys["detail"])

    def test_strict_makes_a_warning_count(self):
        with open(config.path(self.proj), "w") as fh:
            json.dump({"backend": "scan", "invented": 1}, fh)
        code, _out, _ = run("doctor", "--repo", self.proj, "--strict")
        self.assertEqual(doctor.EXIT_UNHEALTHY, code)

    def test_broken_json_is_a_failure(self):
        with open(config.path(self.proj), "w") as fh:
            fh.write("{not json")
        code, doc, _s = self.states()
        self.assertEqual(doctor.EXIT_UNHEALTHY, code)
        self.assertIn("fail", [c["state"] for c in doc["checks"]])

    def test_a_missing_register_is_a_failure(self):
        os.remove(os.path.join(self.proj, "FINDINGS.md"))
        code, doc, _s = self.states()
        self.assertEqual(doctor.EXIT_UNHEALTHY, code)
        register = [c for c in doc["checks"] if "register" in c["check"]][0]
        self.assertEqual("fail", register["state"])

    def test_a_state_from_another_schema_is_a_warning_with_the_way_out(self):
        vault = os.path.join(self.proj, ".tracelink", "vault")
        from tracelink.consult import STATE_FILE
        path = os.path.join(vault, STATE_FILE)
        with open(path) as fh:
            state = json.load(fh)
        state["schema_version"] = 3
        with open(path, "w") as fh:
            json.dump(state, fh)
        _c, doc, _s = self.states()
        stale = [c for c in doc["checks"] if "schema" in c["check"]][0]
        self.assertEqual("warn", stale["state"])
        self.assertIn("tracelink sync", stale["remedy"])

    def test_doctor_never_writes(self):
        before = checksums(self.proj)
        run("doctor", "--repo", self.proj)
        run("doctor", "--repo", self.proj, "--json")
        self.assertEqual(before, checksums(self.proj))

    def test_there_is_no_fix_flag(self):
        """The prose says there is none; the options list must agree."""
        _c, out, _ = run("doctor", "--help")
        options = out.partition("options:")[2]
        self.assertNotIn("--fix", options)
        self.assertIn("no --fix", out)


if __name__ == "__main__":
    unittest.main()
