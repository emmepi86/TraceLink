"""ms-11b: no anchor may point at a path TraceLink cannot find.

Benchmark 01 (F4) linked 104 symbols to locations that resolved to nothing.
The artefact recorded paths relative to the repository root while its own
position forced `--repo` one level below; every path was therefore wrong by
one segment, 104 links were asserted anyway, and the only signal was
`index_completeness: partial`, which is about something else entirely.

Locations are now validated where they enter — at indexing — against one
deterministic rule:

    relative      resolved against --repo
    absolute      accepted only if it resolves inside --repo
    missing file  invalid
    escapes root  invalid (symlinks included: realpath decides)

**Nothing is guessed.** A path that would resolve against the parent
directory, or the artefact's own directory, or by matching a suffix, stays
invalid. Repairing a coordinate error into a coordinate heuristic is how the
next mismatch resolves silently to the wrong file — so the fix for a
mismatch is to point `--repo` at the base the backend actually uses, and
this module has a test that says so.

Rejection is per location, not per index: one bad path does not throw away
ten thousand good ones. All of them being bad is a configuration answer, and
the index is refused rather than written.

`path_integrity` is reported separately from `partial`, because "how much of
the repository did we cover" and "do these coordinates point at it at all"
are different questions, and F4 existed because one was absorbing the other.
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

from tracelink import doctor, status, symbol_index  # noqa: E402
from tracelink.symbol_index import validate_locations  # noqa: E402


def make_repo(tmp):
    """`repo/app/pkg/mod.py`, the shape that produced the original defect."""
    repo = os.path.join(tmp, "repo")
    source = os.path.join(repo, "app", "pkg")
    os.makedirs(source)
    with open(os.path.join(source, "mod.py"), "w") as fh:
        fh.write("def alpha(v):\n    return v\n\n\ndef beta(v):\n    return v\n")
    return repo


def graph(directory, entries):
    out = os.path.join(directory, "graphify-out")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "graph.json"), "w") as fh:
        json.dump({"nodes": [{"label": name, "source_file": path,
                              "source_location": line, "file_type": "py"}
                             for name, path, line in entries]}, fh)


def index(repo, out, backend="graphify"):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(err):
        code = symbol_index.main(["--repo", repo, "--backend", backend,
                                  "--out", out])
    payload = None
    if os.path.exists(out):
        with open(out) as fh:
            payload = json.load(fh)
    return code, payload, err.getvalue()


class TheRuleIsCheckedNotGuessed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name)
        self.app = os.path.join(self.repo, "app")
        self.out = os.path.join(self.tmp.name, "symbols.json")

    def test_relative_paths_resolve_against_repo(self):
        graph(self.app, [("alpha", "pkg/mod.py", 1)])
        code, payload, _err = index(self.app, self.out)
        self.assertEqual(0, code)
        self.assertEqual("ok", payload["indexing"]["path_integrity"]["state"])

    def test_absolute_paths_inside_the_repo_are_accepted(self):
        inside = os.path.join(self.app, "pkg", "mod.py")
        graph(self.app, [("alpha", inside, 1)])
        code, payload, _err = index(self.app, self.out)
        self.assertEqual(0, code)
        self.assertIn("alpha", payload["symbols"])

    def test_absolute_paths_outside_the_repo_are_rejected(self):
        outside = os.path.join(self.tmp.name, "elsewhere.py")
        with open(outside, "w") as fh:
            fh.write("def gamma(v):\n    return v\n")
        graph(self.app, [("alpha", "pkg/mod.py", 1),
                         ("gamma", outside, 1)])
        _code, payload, _err = index(self.app, self.out)
        self.assertIn("alpha", payload["symbols"])
        self.assertNotIn("gamma", payload["symbols"])
        self.assertEqual(1, payload["indexing"]["path_integrity"]
                         ["invalid_locations"])

    def test_a_symlink_out_of_the_repository_is_rejected(self):
        """realpath decides, so a link is not a way around the boundary."""
        outside = os.path.join(self.tmp.name, "secret.py")
        with open(outside, "w") as fh:
            fh.write("def secret(v):\n    return v\n")
        link = os.path.join(self.app, "pkg", "linked.py")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable here")
        graph(self.app, [("alpha", "pkg/mod.py", 1),
                         ("secret", "pkg/linked.py", 1)])
        _code, payload, _err = index(self.app, self.out)
        self.assertIn("alpha", payload["symbols"])
        self.assertNotIn("secret", payload["symbols"])

    def test_a_missing_file_is_invalid_even_with_a_plausible_name(self):
        graph(self.app, [("alpha", "pkg/mod.py", 1),
                         ("ghost", "pkg/deleted.py", 1)])
        _code, payload, _err = index(self.app, self.out)
        self.assertNotIn("ghost", payload["symbols"])

    def test_the_wrong_base_is_refused_never_repaired(self):
        """The b01 case. `app/pkg/mod.py` WOULD resolve one level up — and
        that is exactly the rescue this must not perform."""
        graph(self.app, [("alpha", "app/pkg/mod.py", 1),
                         ("beta", "app/pkg/mod.py", 5)])
        code, payload, err = index(self.app, self.out)
        self.assertEqual(1, code)
        self.assertIsNone(payload, "no index should be written")
        self.assertIn("--repo names the base", err)

    def test_the_same_paths_are_fine_when_repo_names_their_base(self):
        """The fix is the argument, not a heuristic: same artefact, right
        --repo, everything resolves."""
        graph(self.repo, [("alpha", "app/pkg/mod.py", 1)])
        code, payload, _err = index(self.repo, self.out)
        self.assertEqual(0, code)
        self.assertEqual("ok", payload["indexing"]["path_integrity"]["state"])


class RejectionIsPerLocation(unittest.TestCase):
    def test_one_bad_path_does_not_discard_the_good_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            app = os.path.join(repo, "app")
            graph(app, [("alpha", "pkg/mod.py", 1),
                        ("beta", "pkg/mod.py", 5),
                        ("ghost", "nowhere/at/all.py", 3)])
            code, payload, _err = index(app, os.path.join(tmp, "s.json"))
            self.assertEqual(0, code)
            self.assertEqual({"alpha", "beta"}, set(payload["symbols"]))
            integrity = payload["indexing"]["path_integrity"]
            self.assertEqual("invalid", integrity["state"])
            self.assertEqual(1, integrity["invalid_locations"])
            self.assertEqual(3, integrity["checked_locations"])

    def test_the_count_is_of_locations_not_of_symbols(self):
        symbols = {"a": [{"path": "gone.py"}, {"path": "also-gone.py"}]}
        with tempfile.TemporaryDirectory() as tmp:
            kept, _considered, report = validate_locations(symbols, tmp)
            self.assertEqual({}, kept)
            self.assertEqual(2, report["invalid_locations"])

    def test_scan_validates_clean_by_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            _code, payload, _err = index(repo, os.path.join(tmp, "s.json"),
                                         backend="scan")
            self.assertEqual("ok",
                             payload["indexing"]["path_integrity"]["state"])
            self.assertEqual(0, payload["indexing"]["path_integrity"]
                             ["invalid_locations"])


class ItIsNotCompleteness(unittest.TestCase):
    """F4 existed because `partial` was absorbing a different question."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name)
        self.app = os.path.join(self.repo, "app")
        graph(self.app, [("alpha", "pkg/mod.py", 1),
                         ("ghost", "nowhere.py", 1)])
        self.symbols = os.path.join(self.repo, ".tracelink", "symbols.json")
        os.makedirs(os.path.dirname(self.symbols))
        index(self.app, self.symbols)

    def test_status_reports_it_separately(self):
        register = os.path.join(self.tmp.name, "FINDINGS.md")
        with open(register, "w") as fh:
            fh.write("# Findings\n\n## P-01 — a finding [LOW]\n"
                     "### STATUS: OPEN\n\n`alpha` is named here.\n")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            status.main(["--register", register, "--vault",
                         os.path.join(self.tmp.name, "vault"),
                         "--symbols", self.symbols, "--repo", self.app,
                         "--format", "json"])
        report = json.loads(buffer.getvalue())
        self.assertEqual("invalid", report["index"]["path_integrity"])
        self.assertEqual(1, report["index"]["invalid_locations"])
        self.assertTrue(any("index-paths-invalid" in p
                            for p in report["problems"]))

    def test_doctor_names_the_remedy_without_performing_it(self):
        with open(os.path.join(self.repo, "FINDINGS.md"), "w") as fh:
            fh.write("# Findings\n\n## P-01 — a finding [LOW]\n"
                     "### STATUS: OPEN\n\n`alpha` is named here.\n")
        with open(os.path.join(self.repo, ".tracelink", "config.json"),
                  "w") as fh:
            json.dump({"backend": "graphify",
                       "symbols": ".tracelink/symbols.json"}, fh)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            doctor.main(["--repo", self.repo, "--json"])
        checks = {c["check"]: c for c in json.loads(buffer.getvalue())["checks"]}
        coordinates = checks["index coordinates"]
        self.assertEqual("warn", coordinates["state"])
        self.assertIn("--repo names the base", coordinates["remedy"])


if __name__ == "__main__":
    unittest.main()
