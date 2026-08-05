#!/usr/bin/env python3
"""Install a git post-commit hook that refreshes the index and the links.

The README promises the commands can be wired "into a hook or make target";
this module does the wiring. Three actions, spelled as a positional
sub-command because `install` and `remove` are verbs and a flag-only
spelling (`--status`) would leave the other two without a home:

    tracelink hook install [--repo .] [--vault .tracelink/vault]
                           [--symbols .tracelink/symbols.json]
    tracelink hook status  [--repo .]
    tracelink hook remove  [--repo .]

The hook file is found with `git rev-parse --git-path hooks`, which is
correct in worktrees and under core.hooksPath. Everything tracelink writes
lives between two exact markers:

    # >>> tracelink hook v1 >>>
    ...
    # <<< tracelink hook <<<

and only those lines are ever rewritten or removed. A missing post-commit
is created executable with a `#!/bin/sh` shebang; an existing one keeps its
permissions and every byte outside the markers, whether tracelink's block
is appended (no markers yet) or replaced in place (idempotent install).
`remove` deletes the block and nothing else; it never deletes the file.

The block itself is harmless by construction: it runs only when the vault
directory exists and `tracelink` is on PATH, sends stdout to /dev/null,
keeps stderr, and suffixes every command with `|| true` — a refresh
failure must never look like a commit failure. The --repo/--vault/--symbols
values are shell-quoted and baked into the block at install time; git runs
hooks from the top of the working tree, so the relative defaults resolve
against the repository root.

Exit codes: 0 on success and for every no-op (remove with nothing to
remove, status of an uninstalled hook); 2 when --repo is not a git
repository, the marker pair is corrupted, or an existing post-commit is
not UTF-8 text (refused untouched, by every action). No tracebacks.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys

MARK_START = "# >>> tracelink hook v1 >>>"
MARK_END = "# <<< tracelink hook <<<"
_HOOK_NAME = "post-commit"
_SHEBANG = "#!/bin/sh\n"
_PATHS_LINE = re.compile(r"^# paths: (.*)$", re.M)


def _fail(message: str) -> int:
    print(f"tracelink hook: {message}", file=sys.stderr)
    return 2


_NOT_UTF8 = "existing post-commit is not UTF-8 text; refusing to touch it"


def _read_hook(path: str):
    """The hook file's text, or None when it is not UTF-8. A binary
    post-commit is not ours to parse, let alone rewrite — every caller turns
    None into the same polite exit 2 instead of a traceback."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        return None


def hooks_dir(repo: str):
    """Absolute hooks directory, or None when repo is not a git repository.

    `--git-path hooks` (not a hand-built .git/hooks) so worktrees and
    core.hooksPath both resolve to the directory git will actually read.
    """
    r = subprocess.run(["git", "-C", repo, "rev-parse", "--git-path", "hooks"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    path = r.stdout.strip()
    if not os.path.isabs(path):
        path = os.path.join(repo, path)
    return os.path.normpath(path)


def build_block(repo: str, vault: str, symbols: str) -> str:
    """The managed block, markers included, valid sh for any path."""
    q = shlex.quote
    return "\n".join([
        MARK_START,
        "# managed by `tracelink hook` — edits between the markers are overwritten",
        f"# paths: repo={q(repo)} vault={q(vault)} symbols={q(symbols)}",
        f"if [ -d {q(vault)} ] && command -v tracelink >/dev/null 2>&1; then",
        f"  tracelink index --repo {q(repo)} --out {q(symbols)} >/dev/null || true",
        f"  tracelink link --vault {q(vault)} --symbols {q(symbols)}"
        f" --repo {q(repo)} >/dev/null || true",
        "fi",
        MARK_END,
    ]) + "\n"


def _split(text: str):
    """(before, block, after) around the managed block, or None when there
    is no block. Raises ValueError when the markers do not pair up."""
    start = text.find(MARK_START)
    end = text.find(MARK_END)
    if start == -1 and end == -1:
        return None
    if start == -1 or end == -1 or end < start:
        raise ValueError(
            "the tracelink markers in the hook are corrupted — fix or delete "
            "the block between "
            f"{MARK_START!r} and {MARK_END!r} by hand")
    stop = end + len(MARK_END)
    if text[stop:stop + 1] == "\n":
        stop += 1
    return text[:start], text[start:stop], text[stop:]


def install(repo: str, vault: str, symbols: str) -> int:
    hooks = hooks_dir(repo)
    if hooks is None:
        return _fail(f"not a git repository: {repo}")
    os.makedirs(hooks, exist_ok=True)
    path = os.path.join(hooks, _HOOK_NAME)
    block = build_block(repo, vault, symbols)

    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(_SHEBANG + block)
        os.chmod(path, 0o755)
        print(f"installed {path}")
        return 0

    text = _read_hook(path)
    if text is None:
        return _fail(_NOT_UTF8)
    try:
        parts = _split(text)
    except ValueError as exc:
        return _fail(str(exc))
    if parts is None:
        if text and not text.endswith("\n"):
            text += "\n"
        text += block
    else:
        before, _old, after = parts
        text = before + block + after
    # Rewritten in place: the file keeps its inode-recorded permissions,
    # which is the promise made to a hand-managed hook. But a hook git
    # cannot execute refreshes nothing, so say so.
    with open(path, "w") as fh:
        fh.write(text)
    if not os.access(path, os.X_OK):
        print(f"tracelink hook: warning: {path} is not executable — "
              f"git will not run it (chmod +x to enable)", file=sys.stderr)
    print(f"installed {path}")
    return 0


def remove(repo: str) -> int:
    hooks = hooks_dir(repo)
    if hooks is None:
        return _fail(f"not a git repository: {repo}")
    path = os.path.join(hooks, _HOOK_NAME)
    if not os.path.exists(path):
        print(f"no {_HOOK_NAME} hook at {path} — nothing to remove")
        return 0
    text = _read_hook(path)
    if text is None:
        return _fail(_NOT_UTF8)
    try:
        parts = _split(text)
    except ValueError as exc:
        return _fail(str(exc))
    if parts is None:
        print(f"no tracelink block in {path} — nothing to remove")
        return 0
    before, _block, after = parts
    # Only the block goes; the file stays even when what remains is just a
    # shebang — deleting a file tracelink did not fully write is not ours
    # to decide.
    with open(path, "w") as fh:
        fh.write(before + after)
    print(f"removed the tracelink block from {path}")
    return 0


def _block_paths(block: str) -> dict:
    """The repo/vault/symbols the block was installed with, from its own
    `# paths:` line — the file is the authority, not a guess."""
    m = _PATHS_LINE.search(block)
    out = {}
    if m:
        for token in shlex.split(m.group(1)):
            key, sep, value = token.partition("=")
            if sep:
                out[key] = value
    return out


def status(repo: str) -> int:
    hooks = hooks_dir(repo)
    if hooks is None:
        return _fail(f"not a git repository: {repo}")
    path = os.path.join(hooks, _HOOK_NAME)
    print(f"hook_file:          {path}")
    if not os.path.exists(path):
        print("installed:          no")
        return 0
    text = _read_hook(path)
    if text is None:
        return _fail(_NOT_UTF8)
    try:
        parts = _split(text)
    except ValueError as exc:
        print("installed:          BROKEN — " + str(exc))
        return 0
    if parts is None:
        print("installed:          no (a post-commit exists, "
              "but it has no tracelink block)")
        return 0
    before, block, after = parts
    print("installed:          yes")
    for key, value in _block_paths(block).items():
        print(f"{key + ':':<20}{value}")
    print("executable:         "
          + ("yes" if os.access(path, os.X_OK) else "NO — git will not run it"))
    foreign = [ln for ln in (before + after).splitlines()
               if ln.strip() and not ln.startswith("#!")]
    print("other_content:      "
          + (f"{len(foreign)} non-tracelink line(s) — install and remove "
             f"leave them untouched" if foreign else "none"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install a git post-commit hook that refreshes the "
                    "tracelink index and links after every commit. The hook "
                    "only ever runs when the vault exists and tracelink is "
                    "on PATH, and it never fails the commit.")
    ap.add_argument("action", choices=["install", "status", "remove"],
                    help="install writes/updates the managed block, status "
                         "reports it, remove deletes it (and nothing else)")
    ap.add_argument("--repo", default=".",
                    help="repository whose hooks to manage (default .)")
    ap.add_argument("--vault", default=".tracelink/vault",
                    help="vault path baked into the hook (default "
                         ".tracelink/vault, relative to the repo root)")
    ap.add_argument("--symbols", default=".tracelink/symbols.json",
                    help="symbols path baked into the hook (default "
                         ".tracelink/symbols.json)")
    args = ap.parse_args()

    if args.action == "install":
        return install(args.repo, args.vault, args.symbols)
    if args.action == "remove":
        return remove(args.repo)
    return status(args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
