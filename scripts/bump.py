#!/usr/bin/env python3
"""Keep the version identical everywhere it is declared.

The version lives in four places — `pyproject.toml`,
`src/tracelink/__init__.py`, `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` (twice) — because each consumer reads a
different file. 0.5.1 shipped with the plugin manifests still saying 0.5.0;
nobody noticed until a review did. A version that exists in four files is a
version that will drift, unless drifting fails the build.

    bump.py --check        exit 1 if any declaration disagrees (runs in CI)
    bump.py --print        print the version from pyproject.toml (the truth)
    bump.py --set X.Y.Z    rewrite all declarations to X.Y.Z

pyproject.toml is the source of truth: it is what PyPI ships.
"""

import argparse
import json
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "tracelink" / "__init__.py"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def truth() -> str:
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def declared() -> dict:
    """Every place the version is declared, with its current value."""
    out = {"pyproject.toml [project.version]": truth()}

    m = re.search(r'^__version__ = "([^"]+)"', INIT.read_text(), re.M)
    out["src/tracelink/__init__.py [__version__]"] = m.group(1) if m else "(missing)"

    plugin = json.loads(PLUGIN.read_text())
    out[".claude-plugin/plugin.json [version]"] = plugin.get("version", "(missing)")

    mp = json.loads(MARKETPLACE.read_text())
    out[".claude-plugin/marketplace.json [metadata.version]"] = mp.get(
        "metadata", {}
    ).get("version", "(missing)")
    for i, entry in enumerate(mp.get("plugins", [])):
        out[f".claude-plugin/marketplace.json [plugins[{i}].version]"] = entry.get(
            "version", "(missing)"
        )
    return out


def check() -> int:
    decls = declared()
    want = decls["pyproject.toml [project.version]"]
    drifted = {k: v for k, v in decls.items() if v != want}
    if not drifted:
        print(f"versions in lockstep: {want}")
        return 0
    print(f"version drift — pyproject.toml says {want}, but:", file=sys.stderr)
    for k, v in drifted.items():
        print(f"  {k} = {v}", file=sys.stderr)
    print("fix with: scripts/bump.py --set X.Y.Z", file=sys.stderr)
    return 1


def _sub(path: pathlib.Path, pattern: str, repl: str) -> None:
    text = path.read_text()
    new, n = re.subn(pattern, repl, text, flags=re.M)
    if n == 0:
        raise SystemExit(f"bump: pattern not found in {path}: {pattern!r}")
    path.write_text(new)


def set_version(version: str) -> int:
    if not _SEMVER.match(version):
        print(f"bump: {version!r} is not X.Y.Z", file=sys.stderr)
        return 2
    # pyproject: only the [project] version line (regex keeps comments/layout;
    # tomllib cannot write). The anchored key plus quoted value is unambiguous.
    _sub(PYPROJECT, r'^version = "[^"]+"', f'version = "{version}"')
    _sub(INIT, r'^__version__ = "[^"]+"', f'__version__ = "{version}"')
    # JSON manifests: parse and re-dump would reorder nothing but lose the
    # existing 2-space style guarantees across Python versions; a targeted
    # regex on the quoted key is simpler and diff-minimal.
    for path in (PLUGIN, MARKETPLACE):
        _sub(path, r'"version": "[^"]+"', f'"version": "{version}"')
        json.loads(path.read_text())  # still valid JSON, or die loudly
    print(f"set {version} in 4 files")
    return check()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="fail if declarations disagree")
    g.add_argument("--print", action="store_true", dest="show", help="print the version")
    g.add_argument("--set", metavar="X.Y.Z", help="rewrite every declaration")
    args = ap.parse_args()

    if args.show:
        print(truth())
        return 0
    if args.check:
        return check()
    return set_version(args.set)


if __name__ == "__main__":
    raise SystemExit(main())
