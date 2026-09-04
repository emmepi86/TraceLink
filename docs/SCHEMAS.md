# Schemas

TraceLink writes four kinds of structured data, and they are four different
contracts. Confusing them is how a tool ends up unable to change its own
cache without breaking somebody's script.

| Schema | Written by | Read by | Compatibility |
|---|---|---|---|
| **Finding / register format** | you | humans + TraceLink | versioned, migratable — your text, never rewritten |
| **`.tracelink/config.json`** | you (or `init`) | TraceLink | user-authored; unknown keys ignored, never deleted |
| **Link state** (`.tracelink-link-state.json`) | `link` | TraceLink internals | **disposable cache** — rebuilt at will |
| **CLI JSON** (`--json`) | every command | other programs | **public API** — the one that must not move |

The distinction that carries the most weight is the last two:

> **The link state is a rebuildable cache. The CLI JSON is an API.**

A link-state schema bump is cheap: `link` rewrites the file in full, and
until it does, `consult` and `explain` stay silent rather than guess. That
is why 0.9 moved it from 3 to 4 without ceremony. The published JSON cannot
move the same way, so it does not: **`schema_version` tracks
incompatibility, not releases.** Adding an optional field keeps the version;
removing one, renaming one, or changing what an existing field means is what
increments it.

---

## 1. Finding / register format

Your file, in your words. TraceLink reads it and never writes it.

```markdown
## RES-01 — totals ignore tax [HIGH]
### STATUS: OPEN

`compute_total` in `src/app.py` sums line items but never applies tax.
```

- the heading carries the id and, in brackets, the severity;
- `### STATUS:` and `### SEVERITY:` lines override them, **last one wins** —
  a finding downgraded from HIGH to LOW must not keep reading HIGH;
- code is named in backticks: symbols (`compute_total`), qualified names
  (`payments.validate`) and paths (`src/app.py`) are what makes a finding
  linkable. Prose alone links to nothing, and `lint` says so rather than
  guessing.

Notes in the vault carry `tracelink_schema: 1` in their frontmatter, plus a
managed block between two markers that belongs to `link`. Everything outside
those markers is yours; `split` regenerates a note around the block without
inspecting it.

## 2. `.tracelink/config.json`

Written by hand or by `init`, read by every command. Every key is optional
and has a default:

```json
{
  "register": "FINDINGS.md",
  "prefix": "RES",
  "backend": "scan",
  "vault": ".tracelink/vault",
  "symbols": ".tracelink/symbols.json",
  "consult": false,
  "capture": false
}
```

`consult` and `capture` are the two Claude Code plugin gates, off by
default: the vault does not speak inside somebody's turn uninvited. They
gate the *plugin*, never the CLI — `tracelink consult` answers a question
whether or not the hook is allowed to volunteer one.

A key this version does not understand is kept and ignored, and `doctor`
points it out. A config written by a newer TraceLink must not break an older
one.

## 3. Link state and symbol state — caches, and treated as such

Two files, split by owner rather than by size:

| file | written by | read by | holds |
|---|---|---|---|
| `.tracelink-link-state.json` (schema **5**) | `link` | `consult`, `explain`, `status` | the knowledge `link` compiled |
| `.tracelink-symbol-state.json` (schema **1**) | `link` | `link` | its own cache of the index it consumed |

Delete either and nothing is lost but time.

The split is the point. The symbol cache was 94–98% of one file that
`consult` parsed on every edit and never read a byte of, which made the
per-edit cost a function of the *codebase* rather than of the memory being
served: 9.8 ms at 14k symbols, 548 ms at 400k, for a vault that never
changed. `consult` now reads only the compiled knowledge, and does not know
the other file exists.

The link state records `symbol_state_fingerprint`, which says which symbol
state these links were produced from. `link` checks it; `consult` neither
reads it nor opens the file it names. Between `index` and `link` the two
files legitimately disagree — that is the normal condition mid-`sync`, not a
fault — and `consult` keeps serving the last complete snapshot rather than
going quiet because the index moved half a step ahead.

```json
{
  "schema_version": 4,
  "symbols_fingerprint": "sha256:…",
  "options_fingerprint": "sha256:…",
  "symbol_locations": {"validate": "sha256:…"},
  "notes": {
    "RES-01.md": {
      "content_hash": "sha256:…",
      "linked": ["payments.validate"],
      "locations": [{"path": "src/payments.py", "line": 88}],
      "provenance": [
        {"reason": "qualified-name",
         "basis": [["qualified_name_in_note", "payments.validate"]]}
      ],
      "files": ["infra/docker/compose.yml"],
      "files_fingerprint": "sha256:…",
      "ambiguous": [
        {"name": "validate", "reason": "ambiguous",
         "candidates": ["src/users.py:L3", "src/payments.py:L7"],
         "basis": []}
      ]
    }
  }
}
```

**Schema 4 has one invariant**, enforced where the state is written and
again where it is read:

```text
a link            => a known reason AND a non-empty basis
an ambiguity      => two or more candidates
a conflict        => the evidence that collided
```

`match` is an assertion. A state that cannot say how it reached one is not
repaired in place and not reported as a plain match: `link` rebuilds it,
`consult` and `explain` answer `state_unusable` and exit 5. A state whose
`schema_version` is not 4 is silence, never a guess.

`reason` is the resolver's own word for the branch it took. It is internal:
it appears in a published document only under `--debug`, and it is free to
change when the resolver is refactored.

### Coordinates: `path_integrity`

Separate from `partial`, and for a reason — `partial` says how much of the
repository an index covered, `path_integrity` says whether its coordinates
name that repository at all:

```json
"path_integrity": {"state": "ok" | "invalid",
                   "checked_locations": 13866,
                   "invalid_locations": 0}
```

Every location is validated where it enters, at indexing: a relative path is
resolved against `--repo`, an absolute one is accepted only if it resolves
inside it, and a missing file or a path escaping the root (symlinks
included — `realpath` decides) is invalid and dropped. **Nothing is
guessed**: a path that would resolve against the parent directory, the
artefact's own directory, or by suffix stays invalid, because repairing a
coordinate error into a coordinate heuristic is how the next mismatch
resolves silently to the wrong file. The fix for a mismatch is to point
`--repo` at the base the backend records against.

Rejection is per location. One bad path does not discard ten thousand good
ones; every path being bad is a configuration answer, and the index is
refused rather than written.

### Freshness, in two parts

An index carries two separate claims, and `link`, `status` and `sync`
report both:

```text
index_freshness      is this index still about this repository?
upstream_freshness   is the evidence it was built FROM current?
effective_freshness  the less certain of the two
```

The combination is monotone — `fresh + unknown` is `unknown`, never
`fresh` — because an index built a minute ago from a month-old artefact is
new and out of date at the same time, and only one of those facts is safe
to act on. `--freshness require` gates on the effective answer.

`upstream_freshness` lives in the index under `indexing.upstream`:

| state | meaning |
|---|---|
| `verified` | the backend read the working tree, or its artefact names the commit we are on and the tree is clean |
| `stale` | the artefact names a different repository state |
| `unknown` | the artefact records nothing checkable — a timestamp is diagnosis, not proof |

`unknown` does not stop an index being used. **Usable and fresh are
different words**, and only one of them is a claim about currency.

## 4. CLI JSON — the public API

Every command's `--json` prints one document on stdout and nothing else;
human text goes to stderr. Each document declares `schema_version`, which
tracks **incompatibility of that document's shape**, not the release.

### Vocabulary shared by `consult` and `explain`

```text
state    match | ambiguous | conflict
method   explicit_override | sole_candidate | qualified_symbol | path_in_note
         (null wherever nothing was asserted — a refusal has no method)
basis    [{kind, value}] — the evidence itself:
         frontmatter_override, sole_candidate, qualified_name_in_note,
         path_in_note, dotted_reference, path_suffix_match
```

This vocabulary is mapped from the resolver's internal reasons by a table
tests keep total in both directions: every internal reason has a published
name (completeness), and every published name is produced by a real
pipeline run (closure). A published word that no run produces would be a
lie waiting to happen.

### `consult --json` (schema 1)

```json
{
  "schema_version": 1,
  "target": {"input": "src/payments.py", "kind": "file",
             "resolved": "src/payments.py"},
  "hits": [
    {"finding_id": "RES-01", "status": "open", "severity": "high",
     "title": "totals ignore tax",
     "anchors": [
       {"kind": "symbol", "name": "payments.validate",
        "path": "src/payments.py", "line": 88,
        "state": "match", "method": "qualified_symbol",
        "basis": [{"kind": "qualified_name_in_note",
                   "value": "payments.validate"}]}
     ]}
  ]
}
```

An `error` object appears when there is one, with a published code:
`no_state`, `state_schema_unsupported`, `state_unusable`,
`ambiguous_target`, `target_not_found`, `invalid_target`.

### `explain --json` (schema 1)

`finding`, `links`, `files`, `unresolved` — each link carrying the same
`state` / `method` / `basis`, each unresolved name carrying its candidates.

### `sync`, `init`, `doctor` (schema 1)

`sync` reports `ok`, a `summary` of what each step did, and under `--check`
the `changed` list. `init` reports the `steps` it took (`created` / `kept` /
`detected`). `doctor` reports an overall `state` and one entry per check
with its `remedy`.

### Exit codes

Published, therefore fixed.

| Code | Meaning |
|---|---|
| `0` | answered — including "nothing is written about this" |
| `1` | `sync`: a step failed, or `--check` found work to do. `doctor`: something will stop TraceLink working |
| `2` | unusable arguments |
| `3` | a file target that does not exist |
| `4` | the name means two or more things — never guessed |
| `5` | no link state, or one this version cannot read |

`consult` on a **symbol** nothing links exits `0`, not `3`: the link state
knows which symbols have findings, not which symbols exist, and the command
does not assert what it cannot check. A **file** it can check, and does.
