# Benchmark 05 — Home Assistant Core

The first test on a codebase **we did not write and do not know**, and the
first one that can be published whole: the repository is public, the
findings were built from its own public history, and everything here —
[`findings.md`](findings.md), [`gold.json`](gold.json),
[`results.json`](results.json) — is in this directory.

`home-assistant/core` at `8d626131`: **10 095 Python files, 74 179
definitions, 31 330 distinct names**. Nobody involved in TraceLink has
worked on it.

## How the findings were written

Backwards from the usual direction, on purpose. Picking files first and
then looking for knowledge about them selects for what is easy to link.

```
search merged PRs whose bodies carry durable knowledge
        ↓  workaround, firmware, regression, does not report, quirk, stale
discard dependency bumps and process noise
        ↓
read what was actually learned
        ↓
find the code it applies to, and check it still exists at HEAD
        ↓
write the finding, freeze the expectations
        ↓
only then run TraceLink
```

20 findings, each from a real pull request: a printer that returns an
undocumented version field, a bulb that reports no colour mode, a fan that
sends 255 as a sentinel, a remote whose missing firmware field took down a
whole platform, a device that stops advertising when polled too often,
setpoint constants that are converted twice. This is knowledge *about* code
that is not *in* the code — the shape the project claims to be for.

Expectations were verified with an independent scan of HEAD, not with
TraceLink's index: which names exist, and **how many times**.

## Result

```
FALSE AUTHORITATIVE ASSERTIONS   0    (target 0)
SUPPORTED KNOWLEDGE RECOVERED    14/14   (100%)
```

| Outcome | |
|---|---:|
| correct match | 14 |
| correct ambiguity | 2 |
| false ambiguity | 4 |
| wrong match / unsupported assertion | **0** |

The matches include the cases this codebase makes hard. `async_setup_entry`
is defined **3 815 times** here and `__init__` 6 431 times; a finding naming
one of those without saying where was refused as ambiguous, correctly. A
finding that named `_async_update_attrs` — **165 definitions** — together
with the file it meant resolved to exactly one, through the path the author
wrote. Same for a name with 29 definitions and one with 4.

## What the four false ambiguities are

Ordinary English words that are also function names somewhere in 74 179
definitions: `template`, `firmware`, `integration`, `callback`, `current`,
`function`, `release`. A finding that names no code at all still collects
them, and they surface as an ambiguity — a refusal, never a link.

This is [F7](../../docs/LIMITATIONS.md), and this benchmark is the first
measurement of its size: on a codebase of this scale, **4 of 20 findings
pick up a noise line**, against 1 of 50 on the smaller private codebase.
Still no false claim — the evidence gate means a bare word cannot produce a
link — but the noise is materially larger here, and that is worth knowing
before deciding it stays accepted forever.

## What it establishes

On a large codebase nobody here knows, with knowledge taken from that
project's own history: every finding whose code still exists was connected
to it, the genuinely ambiguous names were refused rather than guessed, and
nothing was asserted that nobody wrote.

It does not establish that the findings are useful to a Home Assistant
maintainer. That question needs them, not us.
