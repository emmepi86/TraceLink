#!/usr/bin/env python3
"""F2b, analysis only: what evidence lets us skip re-reading every file?

Verifying that an index still describes a repository costs 5.8 s at 50 000
files, and 75% of `link`'s floor is that hash (benchmark 02). The question
is not how to make hashing faster. It is which evidence can *replace* it
without weakening what freshness means — and where no evidence can.

Four strategies are timed and, more importantly, probed against the changes
each one must be able to see:

    hash      read and hash every indexed file (what TraceLink does today)
    status    `git status --porcelain` — what git says about the worktree
    tree      the tree identity of HEAD — what git says about the commit
    hybrid    tree identity for tracked-and-clean, hashing for the rest

Nothing here changes production code. It measures, and it records what each
strategy can and cannot honestly conclude.

Two constraints shape the design and are checked, not assumed:

  * **reading must not write.** `git status` refreshes the index by default,
    which writes into `.git` — unacceptable for a tool that promises to only
    read the repository it observes. Every git call here uses
    `--no-optional-locks`, and the probe verifies `.git` is untouched.
  * **a commit is not a working tree.** "git says clean" is evidence only
    against a known snapshot: an index built at another commit cannot be
    called fresh because the tree is clean now.

    python3 benchmarks/f2b_freshness_evidence.py --scratch /tmp/f2b
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time

GIT = ["git", "--no-optional-locks"]


def git(repo, *args, check=True):
    out = subprocess.run(GIT + ["-C", repo, *args], capture_output=True,
                         text=True)
    if check and out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {out.stderr[:200]}")
    return out.stdout


def make_repo(root, files, ignored=20, untracked=20):
    if os.path.isdir(root):
        return root
    os.makedirs(root)
    for index in range(files):
        directory = os.path.join(root, f"pkg{index // 200:04d}")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"mod{index:06d}.py"), "w") as fh:
            fh.write(f"def sym_{index:06d}(value):\n    return value\n")
    with open(os.path.join(root, ".gitignore"), "w") as fh:
        fh.write("generated/\n")
    os.makedirs(os.path.join(root, "generated"), exist_ok=True)
    for index in range(ignored):
        with open(os.path.join(root, "generated", f"art{index}.json"),
                  "w") as fh:
            fh.write('{"generated": true}\n')
    env = dict(os.environ, GIT_AUTHOR_NAME="b", GIT_AUTHOR_EMAIL="b@e",
               GIT_COMMITTER_NAME="b", GIT_COMMITTER_EMAIL="b@e")
    subprocess.run(["git", "-C", root, "init", "-q"], check=True, env=env)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", root, "commit", "-qm", "base"], check=True,
                   env=env)
    for index in range(untracked):
        with open(os.path.join(root, f"loose{index}.py"), "w") as fh:
            fh.write("def loose():\n    return 1\n")
    return root


# --------------------------------------------------------------------------- #
# the four strategies, each returning an opaque identity of "the state now"
# --------------------------------------------------------------------------- #

def discover(repo):
    """The indexed set, rediscovered — as the verifier does, so that a file
    added since the index was built is part of what gets compared."""
    return sorted(os.path.relpath(os.path.join(base, name), repo)
                  for base, dirs, names in os.walk(repo)
                  if ".git" not in base
                  for name in names if name.endswith(".py"))


def by_hash(repo, _files=None):
    files = discover(repo)
    digest = hashlib.sha256()
    for rel in files:
        try:
            with open(os.path.join(repo, rel), "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"\0missing\0")
        digest.update(rel.encode())
    return digest.hexdigest()


def by_status(repo, _files):
    return hashlib.sha256(
        (git(repo, "status", "--porcelain=v1", "--untracked-files=all")
         + git(repo, "rev-parse", "HEAD")).encode()).hexdigest()


def by_tree(repo, _files):
    return git(repo, "rev-parse", "HEAD^{tree}").strip()


def by_hybrid(repo, _files=None):
    """Tree identity for what git can vouch for, hashing for the rest.

    Git certifies the CONTENT of tracked files at a commit. Anything it does
    not cover — a modified tracked file, an untracked file, an ignored
    artefact — is hashed, because nothing else can speak for it.
    """
    files = discover(repo)
    tree = by_tree(repo, None)
    dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    outside = sorted({line[3:].strip().strip('"')
                      for line in dirty.splitlines() if line.strip()})
    ignored_or_needed = [rel for rel in outside if rel in set(files)]
    scoped = hashlib.sha256()
    for rel in ignored_or_needed:
        try:
            with open(os.path.join(repo, rel), "rb") as fh:
                scoped.update(fh.read())
        except OSError:
            scoped.update(b"\0missing\0")
        scoped.update(rel.encode())
    # the set itself is evidence: a file appearing or vanishing changes it
    names = hashlib.sha256("\n".join(files).encode()).hexdigest()
    return hashlib.sha256(
        (tree + "|" + names + "|" + scoped.hexdigest()).encode()).hexdigest()


STRATEGIES = {"hash": by_hash, "status": by_status, "tree": by_tree,
              "hybrid": by_hybrid}


# --------------------------------------------------------------------------- #
# what each one can see
# --------------------------------------------------------------------------- #

def mutations(repo, files):
    """(name, apply, undo) — the changes freshness must not miss.

    The tracked files are asked of git rather than taken off the front of
    the indexed list: the first run of this probe edited an *untracked*
    file while calling it "tracked file modified", and every conclusion
    drawn from that row would have been about the wrong thing.
    """
    tracked = [rel for rel in git(repo, "ls-files").splitlines()
               if rel.endswith(".py")]
    first = os.path.join(repo, tracked[0])
    other = os.path.join(repo, tracked[1])
    ignored = os.path.join(repo, "generated", "art0.json")
    loose = os.path.join(repo, "loose0.py")
    fresh = os.path.join(repo, "pkg0000", "brand_new.py")

    def edit(path, text):
        def apply():
            with open(path) as fh:
                before = fh.read()
            with open(path, "w") as fh:
                fh.write(text)
            return before
        def undo(before):
            with open(path, "w") as fh:
                fh.write(before)
        return apply, undo

    def rename(path):
        def apply():
            os.rename(path, path + ".moved")
            return path
        def undo(original):
            os.rename(original + ".moved", original)
        return apply, undo

    def delete(path):
        def apply():
            with open(path) as fh:
                before = fh.read()
            os.remove(path)
            return before
        def undo(before):
            with open(path, "w") as fh:
                fh.write(before)
        return apply, undo

    def create(path):
        def apply():
            with open(path, "w") as fh:
                fh.write("def added():\n    return 1\n")
            return path
        def undo(created):
            os.remove(created)
        return apply, undo

    def touch(path):
        def apply():
            stat = os.stat(path)
            os.utime(path, (stat.st_atime + 10_000, stat.st_mtime + 10_000))
            return (path, stat.st_atime, stat.st_mtime)
        def undo(state):
            os.utime(state[0], (state[1], state[2]))
        return apply, undo

    return [
        ("tracked file modified", *edit(first, "def changed():\n    return 2\n")),
        ("tracked file renamed", *rename(other)),
        ("tracked file deleted", *delete(os.path.join(repo, tracked[2]))),
        ("new file created", *create(fresh)),
        ("untracked file modified", *edit(loose, "def loose():\n    return 9\n")),
        ("ignored artefact modified", *edit(ignored, '{"generated": false}\n')),
        ("mtime changed, content identical",
         *touch(os.path.join(repo, tracked[3]))),
    ]


def probe(repo, files):
    baseline = {name: fn(repo, files) for name, fn in STRATEGIES.items()}
    rows = []
    for label, apply, undo in mutations(repo, files):
        state = apply()
        seen = {name: fn(repo, files) != baseline[name]
                for name, fn in STRATEGIES.items()}
        undo(state)
        after = {name: fn(repo, files) for name, fn in STRATEGIES.items()}
        rows.append({"change": label, "detected": seen,
                     "restored": {k: after[k] == baseline[k]
                                  for k in baseline}})
    return rows


def timings(repo, files, repeats=3):
    out = {}
    for name, fn in STRATEGIES.items():
        samples = []
        for _ in range(repeats):
            start = time.perf_counter()
            fn(repo, files)
            samples.append(time.perf_counter() - start)
        out[name] = {"cold_s": round(samples[0], 4),
                     "warm_min_s": round(min(samples[1:] or samples), 4)}
    return out


def git_dir_fingerprint(repo):
    """Everything under .git, by name and mtime: reading must not write."""
    seen = {}
    for base, _dirs, names in os.walk(os.path.join(repo, ".git")):
        for name in names:
            path = os.path.join(base, name)
            try:
                seen[path] = os.stat(path).st_mtime_ns
            except OSError:
                pass
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scratch", default="/tmp/tracelink-f2b")
    ap.add_argument("--sizes", default="10000,25000,50000")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    os.makedirs(args.scratch, exist_ok=True)

    report = {"timings": {}, "detection": None, "git_write_check": None}
    for size in [int(s) for s in args.sizes.split(",")]:
        repo = make_repo(os.path.join(args.scratch, f"repo-{size}"), size)
        files = sorted(
            os.path.relpath(os.path.join(base, name), repo)
            for base, dirs, names in os.walk(repo)
            for name in names
            if name.endswith(".py") and ".git" not in base)
        print(f"  {size} files ({len(files)} indexed) …", flush=True)
        report["timings"][size] = timings(repo, files)
        if report["detection"] is None:
            before = git_dir_fingerprint(repo)
            report["detection"] = probe(repo, files)
            after = git_dir_fingerprint(repo)
            report["git_write_check"] = {
                "paths_changed": sorted(p for p in set(before) | set(after)
                                        if before.get(p) != after.get(p))[:5],
                "clean": before == after}

    print("\n  strategy timings (warm min, seconds)")
    print(f"  {'files':>7} " + " ".join(f"{n:>9}" for n in STRATEGIES))
    for size, row in report["timings"].items():
        print(f"  {size:>7} " +
              " ".join(f"{row[n]['warm_min_s']:>9.3f}" for n in STRATEGIES))

    print("\n  what each strategy detects")
    print(f"  {'change':<34} " + " ".join(f"{n:>7}" for n in STRATEGIES))
    for row in report["detection"]:
        print(f"  {row['change']:<34} " +
              " ".join(f"{'yes' if row['detected'][n] else 'NO':>7}"
                       for n in STRATEGIES))
    print(f"\n  reading wrote into .git: "
          f"{'no' if report['git_write_check']['clean'] else 'YES'}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "f2b-evidence.json"), "w") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
