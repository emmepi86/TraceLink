#!/usr/bin/env python3
"""Where does TraceLink stop being cheap? Synthetic, reproducible curves.

Benchmark 01 gave one point on the curve: a real repository with ~14k
symbols, where `consult` cost ~10ms per call and almost all of it was
parsing `symbol_locations`, a structure only the linker reads. One point is
an anecdote. This generates the rest of the curve, on repositories anyone
can rebuild from this file.

Two axes, because b01 suggested the two costs have different drivers:

  **F1 — index size.**  Vault held constant, symbol count grown. If the
  per-edit cost really tracks the link state rather than the vault, consult
  should scale with symbols while the notes stay put.

  **F2 — files against notes.**  `link`'s fixed cost is a tree walk, so the
  matrix separates repository size from vault size and asks which one the
  time follows, and where the incremental skip stops being worth anything.

Every configuration runs in its own process, so the peak RSS reported is
that configuration's and not the sum of everything before it. Nothing here
touches a real repository; the generated ones live under --scratch and are
deleted with it.

    python3 benchmarks/scaling.py --scratch /tmp/scale --out results/
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)


# --------------------------------------------------------------------------- #
# synthetic repositories
# --------------------------------------------------------------------------- #

FUNCS_PER_FILE = 20


def make_repo(root, files, funcs=FUNCS_PER_FILE):
    """`files` python modules, each with `funcs` uniquely named functions.

    Names are unique across the whole repository on purpose: this axis is
    about *size*, and duplicate names would quietly turn it into an axis
    about ambiguity as well.
    """
    if os.path.isdir(root):
        return root
    os.makedirs(root)
    for index in range(files):
        directory = os.path.join(root, f"pkg{index // 100:04d}")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"mod{index:06d}.py"), "w") as fh:
            for number in range(funcs):
                name = f"sym_{index:06d}_{number:03d}"
                fh.write(f"def {name}(value):\n"
                         f"    return value + {number}\n\n\n")
    return root


def make_register(symbols_path, path, notes, prefix="PERF"):
    with open(symbols_path, encoding="utf-8") as fh:
        symbols = sorted((json.load(fh).get("symbols") or {}))
    if notes == 0:
        with open(path, "w") as fh:
            fh.write("# Findings\n\n## PERF-001 — placeholder [LOW]\n"
                     "### STATUS: CLOSED\n\nNo code is named here.\n")
        return 0
    stride = max(1, len(symbols) // notes)
    chosen = symbols[::stride][:notes]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Findings — SYNTHETIC PERFORMANCE FIXTURE\n\n")
        for number, name in enumerate(chosen, start=1):
            fh.write(f"## {prefix}-{number:05d} — fixture for `{name}` "
                     f"[MEDIUM]\n### STATUS: OPEN\n\n"
                     f"`{name}` is named so that link has work to do.\n\n")
    return len(chosen)


# --------------------------------------------------------------------------- #
# one configuration, in its own process
# --------------------------------------------------------------------------- #

def percentiles(samples):
    ordered = sorted(samples)
    if not ordered:
        return {}
    def pick(p):
        return round(ordered[min(len(ordered) - 1,
                                 int(round((len(ordered) - 1) * p)))] * 1000, 3)
    return {"n": len(ordered), "p50_ms": pick(0.5), "p95_ms": pick(0.95),
            "max_ms": round(ordered[-1] * 1000, 3)}


def worker(config):
    from tracelink import consult as consult_mod
    from tracelink import linker, splitter, symbol_index

    repo = config["repo"]
    work = config["work"]
    os.makedirs(work, exist_ok=True)
    symbols_path = os.path.join(work, "symbols.json")
    vault = os.path.join(work, "vault")
    register = os.path.join(work, "FINDINGS.md")

    def quiet(fn):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn()

    out = {"config": {k: config[k]
                      for k in ("files", "notes", "label", "funcs")}}

    start = time.perf_counter()
    quiet(lambda: symbol_index.main(["--repo", repo, "--backend", "scan",
                                     "--out", symbols_path]))
    out["index_s"] = round(time.perf_counter() - start, 4)
    with open(symbols_path, encoding="utf-8") as fh:
        out["symbols"] = len(json.load(fh).get("symbols") or {})
    out["symbols_bytes"] = os.path.getsize(symbols_path)

    out["notes_written"] = make_register(symbols_path, register,
                                         config["notes"])
    start = time.perf_counter()
    quiet(lambda: splitter.main(["--register", register, "--out", vault,
                                 "--prefix", "PERF"]))
    out["split_s"] = round(time.perf_counter() - start, 4)

    start = time.perf_counter()
    quiet(lambda: linker.main(["--vault", vault, "--symbols", symbols_path,
                               "--repo", repo, "--full"]))
    out["link_full_s"] = round(time.perf_counter() - start, 4)

    start = time.perf_counter()
    quiet(lambda: linker.main(["--vault", vault, "--symbols", symbols_path,
                               "--repo", repo]))
    out["link_incremental_s"] = round(time.perf_counter() - start, 4)

    state_path = os.path.join(vault, consult_mod.STATE_FILE)
    out["state_bytes"] = os.path.getsize(state_path)
    with open(state_path, "rb") as fh:
        raw = fh.read()
    state = json.loads(raw)
    out["state_symbol_locations_bytes"] = len(
        json.dumps(state.get("symbol_locations") or {}))
    out["state_notes_bytes"] = len(json.dumps(state.get("notes") or {}))
    parses = []
    for _ in range(5):
        start = time.perf_counter()
        json.loads(raw)
        parses.append(time.perf_counter() - start)
    out["state_parse_ms"] = round(min(parses) * 1000, 3)

    targets_files, targets_symbols = [], []
    for entry in state["notes"].values():
        targets_symbols.extend(entry.get("linked") or [])
        for location in entry.get("locations") or []:
            if isinstance(location, dict) and location.get("path"):
                targets_files.append(location["path"])
    targets_files = sorted(set(targets_files))[:100]
    targets_symbols = sorted(set(targets_symbols))[:100]

    samples = []
    for path in targets_files:
        start = time.perf_counter()
        consult_mod.consult(repo, path, kind=consult_mod.FILE, vault=vault)
        samples.append(time.perf_counter() - start)
    out["consult_file"] = percentiles(samples)
    samples = []
    for name in targets_symbols:
        start = time.perf_counter()
        consult_mod.consult(repo, name, kind=consult_mod.SYMBOL, vault=vault)
        samples.append(time.perf_counter() - start)
    out["consult_symbol"] = percentiles(samples)

    out["peak_rss_mb"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return out


# --------------------------------------------------------------------------- #

def run_config(scratch, files, notes, label, funcs=FUNCS_PER_FILE,
               keep_repo=True):
    # `funcs` keeps the two axes apart. F2 asks what the *tree walk* costs,
    # so its repositories carry one symbol per file: otherwise growing the
    # file count would grow the index too and the answer would be "both".
    repo = os.path.join(scratch, f"repo-{files}x{funcs}")
    make_repo(repo, files, funcs)
    work = os.path.join(scratch, f"work-{files}-{notes}")
    shutil.rmtree(work, ignore_errors=True)
    config = {"repo": repo, "work": work, "files": files, "notes": notes,
              "label": label, "funcs": funcs}
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--worker",
         json.dumps(config)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"worker failed for {label}:\n{proc.stderr[-2000:]}")
    shutil.rmtree(work, ignore_errors=True)
    if not keep_repo:
        shutil.rmtree(repo, ignore_errors=True)
    return json.loads(proc.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worker", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--scratch", default="/tmp/tracelink-scaling")
    ap.add_argument("--out", default=None)
    ap.add_argument("--axis", choices=["f1", "f2", "both"], default="both")
    args = ap.parse_args(argv)

    if args.worker:
        print(json.dumps(worker(json.loads(args.worker))))
        return 0

    os.makedirs(args.scratch, exist_ok=True)
    results = {"f1": [], "f2": []}

    if args.axis in ("f1", "both"):
        # symbols = files * 20; the vault stays at 100 notes throughout
        for files in (700, 2500, 5000, 12500, 25000):
            label = f"f1 symbols≈{files * FUNCS_PER_FILE}"
            print(f"  {label} …", flush=True)
            results["f1"].append(run_config(args.scratch, files, 100, label))

    if args.axis in ("f2", "both"):
        for files in (2700, 10000, 25000, 50000):
            for notes in (0, 100, 1000):
                label = f"f2 files={files} notes={notes}"
                print(f"  {label} …", flush=True)
                results["f2"].append(run_config(args.scratch, files, notes,
                                                label, funcs=1))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "scaling.json"), "w") as fh:
            json.dump(results, fh, indent=2)
            fh.write("\n")
    print(json.dumps({"f1_points": len(results["f1"]),
                      "f2_points": len(results["f2"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
