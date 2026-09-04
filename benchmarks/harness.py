#!/usr/bin/env python3
"""Measure TraceLink against a real repository, without touching it.

Benchmark 01 (`benchmarks/crm-ecm/`) runs this against a large private
codebase; the harness itself is generic and knows nothing about it, so the
same numbers can be reproduced on any repository.

Two rules make the results worth reading.

**The target repository is read only.** Every artefact TraceLink produces —
symbols, vault, link state — is written outside the tree, and the run ends
with a check that nothing under the target was created or modified since it
started. A benchmark that quietly edits its subject measures the wrong
thing twice.

**Nothing is tuned to make a number look good.** The register used for
timing is a *fixture*: findings generated from symbols the index already
found, so that split and link have realistic work to do. It is deliberately
not a quality dataset — accuracy needs findings a human wrote about code
they know, which is a later step. Timings from a fixture are honest;
precision numbers from one would not be.

    python3 benchmarks/harness.py --repo /path/to/code --out results/

Everything lands in `<out>/inventory.json`, `<out>/timings.json` and a
readable `<out>/report.md`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from tracelink import consult as consult_mod  # noqa: E402
from tracelink import explain as explain_mod  # noqa: E402
from tracelink import doctor, linker, splitter, symbol_index  # noqa: E402
from tracelink.symbol_index import _DEF_PATTERNS, _SKIP_DIRS  # noqa: E402

INDEXABLE = {ext for ext, _rx in _DEF_PATTERNS}


# --------------------------------------------------------------------------- #
# the target is read only, and the run proves it
# --------------------------------------------------------------------------- #

def touched_since(repo, when):
    """Files under `repo` created or modified since `when`, .git aside."""
    out = subprocess.run(
        ["find", repo, "-newermt", f"@{when}", "-not", "-path", "*/.git/*",
         "-type", "f", "-print"],
        capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()][:20]


def git_facts(repo):
    def git(*args):
        out = subprocess.run(["git", "-C", repo, *args],
                             capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"),
            "short": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "committed_at": git("log", "-1", "--format=%ad", "--date=iso"),
            "dirty_files": len((git("status", "--porcelain") or "").splitlines())}


# --------------------------------------------------------------------------- #
# what the repository is
# --------------------------------------------------------------------------- #

def inventory(repo):
    """Counted with the indexer's own skip rules, so the numbers describe
    what TraceLink actually sees rather than what `find` would."""
    by_ext, lines_by_ext = {}, {}
    files_seen = skipped_dirs = 0
    for root, dirs, files in os.walk(repo):
        before = len(dirs)
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_DIRS and not d.startswith(".")]
        skipped_dirs += before - len(dirs)
        for name in files:
            files_seen += 1
            ext = os.path.splitext(name)[1]
            if ext not in INDEXABLE:
                continue
            by_ext[ext] = by_ext.get(ext, 0) + 1
            path = os.path.join(root, name)
            try:
                with open(path, "rb") as fh:
                    lines_by_ext[ext] = lines_by_ext.get(ext, 0) + sum(
                        1 for _ in fh)
            except OSError:
                pass
    return {"files_walked": files_seen,
            "directories_skipped": skipped_dirs,
            "indexable_files": sum(by_ext.values()),
            "indexable_lines": sum(lines_by_ext.values()),
            "by_extension": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
            "lines_by_extension": dict(sorted(lines_by_ext.items(),
                                              key=lambda kv: -kv[1]))}


def disk(repo):
    def du(path):
        if not os.path.exists(path):
            return None
        out = subprocess.run(["du", "-sb", path], capture_output=True,
                             text=True)
        try:
            return int(out.stdout.split()[0])
        except (ValueError, IndexError):
            return None
    return {"repo_bytes": du(repo), "git_bytes": du(os.path.join(repo, ".git"))}


# --------------------------------------------------------------------------- #
# timing
# --------------------------------------------------------------------------- #

def timed(fn, repeats=3):
    """(result, {cold, warm_median, warm_min, runs}) — cold is the first run,
    which is the one a user feels; the warm runs say how much of it was the
    page cache rather than the work."""
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - start)
    return result, {"cold_s": round(samples[0], 4),
                    "warm_median_s": round(statistics.median(samples[1:])
                                           if len(samples) > 1 else samples[0], 4),
                    "warm_min_s": round(min(samples[1:]) if len(samples) > 1
                                        else samples[0], 4),
                    "runs": len(samples)}


def quiet(fn):
    def wrapped():
        with contextlib.redirect_stdout(io.StringIO()):
            return fn()
    return wrapped


def percentiles(samples):
    ordered = sorted(samples)
    if not ordered:
        return {}
    def pick(p):
        index = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
        return round(ordered[index] * 1000, 3)
    return {"n": len(ordered), "p50_ms": pick(0.50), "p95_ms": pick(0.95),
            "max_ms": round(ordered[-1] * 1000, 3),
            "total_s": round(sum(ordered), 3)}


# --------------------------------------------------------------------------- #
# the fixture register — for timing only, never for accuracy
# --------------------------------------------------------------------------- #

FIXTURE_HEADER = """# Findings — PERFORMANCE FIXTURE

Generated by benchmarks/harness.py from symbols the index already found.
These are not real findings and say nothing true about the code: they exist
so that `split` and `link` have realistic work to time. Accuracy is measured
against findings a human wrote, elsewhere.

"""


def write_fixture(symbols_path, out_path, count, prefix="PERF"):
    with open(symbols_path, encoding="utf-8") as fh:
        symbols = json.load(fh).get("symbols") or {}
    names = sorted(symbols)
    if not names:
        raise SystemExit("the index found no symbols — nothing to time")
    stride = max(1, len(names) // count)
    chosen = names[::stride][:count]
    blocks = [FIXTURE_HEADER]
    for number, name in enumerate(chosen, start=1):
        where = symbols[name][0] if isinstance(symbols[name], list) else {}
        path = where.get("path", "") if isinstance(where, dict) else ""
        blocks.append(
            f"## {prefix}-{number:03d} — fixture finding for `{name}` [MEDIUM]\n"
            f"### STATUS: OPEN\n\n"
            f"`{name}` in `{path}` is named here so that link has work to do.\n")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(blocks))
    return len(chosen)


# --------------------------------------------------------------------------- #

def sample_targets(state_path, limit=200):
    """(files, symbols) the vault actually links, for consult timing."""
    try:
        with open(state_path, encoding="utf-8") as fh:
            notes = json.load(fh).get("notes") or {}
    except OSError:
        return [], []
    files, symbols = [], []
    for entry in notes.values():
        for location in entry.get("locations") or []:
            if isinstance(location, dict) and location.get("path"):
                files.append(location["path"])
        symbols.extend(entry.get("linked") or [])
        for anchor in entry.get("files") or []:
            files.append(anchor)
    return (sorted(set(files))[:limit], sorted(set(symbols))[:limit])


def run(repo, out_dir, backend, findings, repeats):
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    symbols_path = os.path.join(work, "symbols.json")
    vault = os.path.join(work, "vault")
    register = os.path.join(work, "FIXTURE_FINDINGS.md")
    started = time.time() - 1  # a second of slack for clock granularity

    facts = {"tracelink": {
                 "commit": subprocess.run(
                     ["git", "-C", os.path.join(HERE, ".."), "rev-parse",
                      "--short", "HEAD"], capture_output=True,
                     text=True).stdout.strip(),
                 "version": __import__("tracelink").__version__},
             "target": {"path": repo, **git_facts(repo)},
             "environment": {"python": platform.python_version(),
                             "platform": platform.platform(),
                             "processor": platform.machine()},
             "backend": backend}

    print("inventory…", flush=True)
    facts["inventory"] = inventory(repo)
    facts["inventory"].update(disk(repo))

    timings = {}
    print("index…", flush=True)
    _r, timings["index"] = timed(quiet(lambda: symbol_index.main(
        ["--repo", repo, "--backend", backend, "--out", symbols_path])),
        repeats)
    with open(symbols_path, encoding="utf-8") as fh:
        index = json.load(fh)
    facts["index"] = {"symbols": len(index.get("symbols") or {}),
                      "backend": (index.get("indexing") or {}).get("backend"),
                      "bytes": os.path.getsize(symbols_path),
                      "freshness_scope": (index.get("repository") or {}).get(
                          "scope")}

    count = write_fixture(symbols_path, register, findings)
    facts["fixture"] = {"findings": count, "note": "timing only, not a gold set"}

    print("split…", flush=True)
    _r, timings["split"] = timed(quiet(lambda: splitter.main(
        ["--register", register, "--out", vault, "--prefix", "PERF"])),
        repeats)

    print("link (cold, then incremental)…", flush=True)
    _r, timings["link_full"] = timed(quiet(lambda: linker.main(
        ["--vault", vault, "--symbols", symbols_path, "--repo", repo,
         "--full"])), repeats)
    _r, timings["link_incremental"] = timed(quiet(lambda: linker.main(
        ["--vault", vault, "--symbols", symbols_path, "--repo", repo])),
        repeats)

    state_path = os.path.join(vault, consult_mod.STATE_FILE)
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    linked = sum(len(e.get("linked") or []) for e in state["notes"].values())
    unresolved = sum(len(e.get("ambiguous") or [])
                     for e in state["notes"].values())
    facts["linking"] = {"notes": len(state["notes"]), "links": linked,
                        "unresolved": unresolved,
                        "state_bytes": os.path.getsize(state_path),
                        "state_bytes_per_note": round(
                            os.path.getsize(state_path) /
                            max(1, len(state["notes"])), 1)}

    files, symbols = sample_targets(state_path)
    print(f"consult ({len(files)} files, {len(symbols)} symbols)…", flush=True)
    file_samples = []
    for path in files:
        start = time.perf_counter()
        consult_mod.consult(repo, path, kind=consult_mod.FILE, vault=vault)
        file_samples.append(time.perf_counter() - start)
    symbol_samples = []
    for name in symbols:
        start = time.perf_counter()
        consult_mod.consult(repo, name, kind=consult_mod.SYMBOL, vault=vault)
        symbol_samples.append(time.perf_counter() - start)
    timings["consult_file"] = percentiles(file_samples)
    timings["consult_symbol"] = percentiles(symbol_samples)

    print("explain…", flush=True)
    explain_samples = []
    for note in sorted(state["notes"])[:200]:
        finding_id = os.path.splitext(note)[0]
        start = time.perf_counter()
        explain_mod.explain(repo, finding_id, vault=vault)
        explain_samples.append(time.perf_counter() - start)
    timings["explain"] = percentiles(explain_samples)

    print("doctor…", flush=True)
    _r, timings["doctor"] = timed(quiet(lambda: doctor.main(
        ["--repo", repo, "--json"])), repeats)

    facts["timings"] = timings
    facts["read_only"] = {"target_files_touched": touched_since(repo, started)}
    facts["read_only"]["clean"] = not facts["read_only"]["target_files_touched"]
    facts["target"]["dirty_files_after"] = git_facts(repo)["dirty_files"]

    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as fh:
        json.dump(facts, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default="scan")
    ap.add_argument("--findings", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args(argv)
    facts = run(os.path.abspath(args.repo), os.path.abspath(args.out),
                args.backend, args.findings, args.repeats)
    print(json.dumps({"read_only_clean": facts["read_only"]["clean"],
                      "symbols": facts["index"]["symbols"],
                      "links": facts["linking"]["links"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
