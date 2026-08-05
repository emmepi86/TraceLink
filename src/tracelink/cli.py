"""Single entry point: `tracelink <command> [...]`.

The three scripts remain importable and runnable — `scripts/*.py` are thin
wrappers — because a tool that only works after installation is a tool people
cannot try. `python3 scripts/link.py ...` behaves exactly as before.
"""

from __future__ import annotations

import sys

from . import __version__

_COMMANDS = {
    "split": ("splitter", "turn a findings register into one note per finding"),
    "index": ("symbol_index", "build the symbol map (graphify | ctags | scan)"),
    "link": ("linker", "cross-link notes and code, both directions"),
    "status": ("status", "one-shot health of register, vault, index and links"),
    "hook": ("hook", "install a git post-commit hook that refreshes index and links"),
}

_USAGE = f"""tracelink {__version__}

usage: tracelink <command> [options]

  index   {_COMMANDS['index'][1]}
  split   {_COMMANDS['split'][1]}
  link    {_COMMANDS['link'][1]}
  status  {_COMMANDS['status'][1]}
  hook    {_COMMANDS['hook'][1]}

Every command takes --help. A typical run:

  tracelink index --repo . --out .tracelink/symbols.json
  tracelink split --register FINDINGS.md --out .tracelink/vault --prefix RES
  tracelink link  --vault .tracelink/vault --symbols .tracelink/symbols.json --repo .

`link --check --freshness require` is the CI form: it writes nothing and exits
non-zero when the vault is out of date or the index no longer describes the
repository.
"""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(__version__)
        return 0

    command = argv[0]
    entry = _COMMANDS.get(command)
    if entry is None:
        print(f"unknown command {command!r}\n", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 2

    module_name, _desc = entry
    module = __import__(f"tracelink.{module_name}", fromlist=["main"])
    # argparse in each module reads sys.argv, so the sub-command is removed
    # rather than the modules being rewritten to take an argv parameter.
    sys.argv = [f"tracelink {command}"] + argv[1:]
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
