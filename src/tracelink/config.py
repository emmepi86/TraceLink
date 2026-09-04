"""`.tracelink/config.json` — the few things a project may decide once.

Small on purpose. Configuration that can be wrong is configuration that has
to be diagnosed, so this file holds only what a command cannot work out for
itself: where the register lives, what the finding prefix is, which symbol
backend to prefer, and the two opt-in gates the Claude Code plugin reads.

Every key is optional and every one has a default. A missing file, an
unreadable one, broken JSON or JSON that is not an object all mean "no
configuration", never an error: a project that has not decided is a project
that gets the defaults.

The plugin keeps its own private reader (`scripts/plugin_refresh.py`)
because it runs after every edit and must import nothing at all; this
module is for the commands, which can afford an import. The two must agree
on key names, and a test checks that they do.
"""

from __future__ import annotations

import json
import os

CONFIG_DIR = ".tracelink"
CONFIG_FILE = "config.json"

#: Every key this version understands, with the value used when it is absent.
DEFAULTS = {
    "register": "FINDINGS.md",
    "prefix": "RES",
    "backend": "scan",
    "vault": ".tracelink/vault",
    "symbols": ".tracelink/symbols.json",
    "consult": False,
    "capture": False,
}


def path(project):
    return os.path.join(project, CONFIG_DIR, CONFIG_FILE)


def read(project):
    """The project's configuration as a dict, defaults filled in.

    Unknown keys are kept, not rejected: a config written by a newer
    tracelink must not break an older one, and `doctor` is the right place
    to point out a key nobody reads.
    """
    values = dict(DEFAULTS)
    try:
        with open(path(project), encoding="utf-8") as fh:
            stored = json.load(fh)
    except Exception:  # noqa: BLE001 — no config shape may break a command
        return values
    if isinstance(stored, dict):
        values.update(stored)
    return values


def unknown_keys(project):
    """Keys present in the file that this version does not understand."""
    try:
        with open(path(project), encoding="utf-8") as fh:
            stored = json.load(fh)
    except Exception:  # noqa: BLE001
        return ()
    if not isinstance(stored, dict):
        return ()
    return tuple(sorted(k for k in stored if k not in DEFAULTS))


def resolve(project, values, key):
    """A configured path, made absolute against the project."""
    value = values.get(key) or DEFAULTS[key]
    return value if os.path.isabs(value) else os.path.join(project, value)
