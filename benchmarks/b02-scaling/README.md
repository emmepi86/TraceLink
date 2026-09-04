# Benchmark 02 — where TraceLink stops being cheap

Benchmark 01 found two costs on a real repository and gave one point on
each curve. This is the rest of the curve, on synthetic repositories anyone
can rebuild:

```bash
python3 benchmarks/scaling.py --scratch /tmp/scale --out benchmarks/b02-scaling/
```

Nothing here is private, so [`scaling.json`](scaling.json) is the raw
output. Every configuration ran in its own process, so the peak RSS is that
configuration's alone. **Single run per cell** — the noise is visible below
and is called out where it matters.

## F1 — the per-edit cost follows the index, not the vault

The vault stays at 100 notes throughout. Only the symbol count grows.

| Symbols | Link state | `symbol_locations` share | `json.loads` | `consult` p50 | p95 | index | peak RSS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 14 000 | 1.38 MB | 94.1 % | 6.4 ms | **9.8 ms** | 13.0 ms | 0.42 s | 41 MB |
| 50 000 | 4.80 MB | 96.8 % | 33.1 ms | **43.1 ms** | 82.2 ms | 1.59 s | 103 MB |
| 100 000 | 9.55 MB | 97.3 % | 87.0 ms | **88.8 ms** | 113.6 ms | 3.03 s | 191 MB |
| 250 000 | 23.80 MB | 97.7 % | 290.7 ms | **306.6 ms** | 372.8 ms | 8.57 s | 435 MB |
| 400 000 | 38.05 MB | 97.8 % | 527.0 ms | **547.7 ms** | 806.9 ms | 13.19 s | 704 MB |

The vault never changed. The cost did, by 56×.

`consult` p50 tracks the size of the link state almost exactly —

```
consult_p50_ms  ≈  14.8 × (link state in MB)          R² = 0.9935
```

— and `json.loads` of that file accounts for essentially all of it. The
part being parsed is `symbol_locations`, which rises from 94 % to 98 % of
the file and which `consult` never reads: it exists so `link` can decide
what to skip.

**Thresholds on this machine:** the per-edit path costs ~10 ms at about
29 000 symbols, crosses 50 ms at about **57 000**, and at a million symbols
would cost roughly **1.4 s per edit** — on a path whose whole design was to
be free. Peak RSS follows the same line: 700 MB at 400 000 symbols.

### After the fix: the dependency is gone, not reduced

The state was split by owner — the linker's cache of the index moved to its
own file, and `consult` reads only what `link` compiled. Same harness, same
generated repositories, same command:

| Symbols | link state before → after | `consult` p50 before → after |
|---:|---:|---:|
| 14 000 | 1.38 MB → **0.054 MB** | 9.8 ms → **1.35 ms** |
| 50 000 | 4.80 MB → **0.054 MB** | 43.1 ms → **1.32 ms** |
| 100 000 | 9.55 MB → **0.054 MB** | 88.8 ms → **1.16 ms** |
| 250 000 | 23.80 MB → **0.054 MB** | 306.6 ms → **1.06 ms** |
| 400 000 | 38.05 MB → **0.054 MB** | 547.7 ms → **1.18 ms** |

The vault is the same 100 notes at every row, and now so is the state: the
file `consult` reads no longer contains anything that grows with the
codebase.

```
slope before:  +1381 ms per million symbols
slope after:   −0.5 ms per million symbols   (i.e. none, within noise)
```

Measured in a process that does nothing but consult, at 400 000 symbols:
**p50 1.06 ms, peak RSS 10.0 MB**. Parsing the symbol state alone — the
structure that used to be in the same file — costs 160 MB. It is still
38 MB on disk, and that is fine: it is the linker's business, and the
per-edit path never opens it.

The result worth stating is not "26× faster". It is that
`consult` no longer has a term in the number of symbols at all.

## F2 — `link`'s floor is the tree walk, and it grows with the repository

Files and notes are separated here: these repositories carry one symbol per
file, so growing the file count does not also grow the index.

| Files | link, 0 notes (the floor) | 1 000 notes | walk share |
|---:|---:|---:|---:|
| 2 700 | 0.375 s | 1.472 s | 25 % |
| 10 000 | 1.430 s | 2.497 s | 57 % |
| 25 000 | 3.369 s | 4.142 s | 81 % |
| 50 000 | 7.059 s | 7.930 s | **89 %** |

A repository of 50 000 files costs 7 seconds to link **before a single note
is considered**. What the incremental skip can save is whatever is left:

| Files | saving from skipping unchanged notes (1 000 notes) |
|---:|---:|
| 2 700 | 52 % |
| 10 000 | 36 % |
| 25 000 | 11 % |
| 50 000 | 12 % |

The 4–5× measured on small repositories is real there, and gone by 25 000
files.

### Correction: the floor is not the walk

The measurements above stand; the cause named beside them did not. This
page originally attributed the fixed cost to "the tree walk that resolves
file anchors". Timed component by component at 50 000 files:

| | s | share of the floor |
|---|---:|---:|
| hashing every indexed file (freshness) | 5.80 | **75 %** |
| walking to rebuild the fingerprint scope | 1.12 | 14 % |
| the file-anchor map | 0.98 | 12 % |

The floor is dominated by **freshness verification** — proving the index
still describes the repository means reading and hashing every file it
indexed, on every run. The same run with `--freshness ignore` takes
**0.28 s** instead of 5.78 s.

That is a different problem from the one the paragraph above named, and it
is recorded as **F2b** rather than quietly fixed: the hash is what makes a
freshness claim evidence instead of an assumption, so making it cheaper is
a design question, not an optimisation.

### After ms-11d: the walk is demand-driven

The file-anchor map is now built on the first note that names a path, and
never otherwise — a sentinel test replaces `repo_file_map` with a counter
and fails if it is entered. With freshness verification switched off, so
that the term is visible at all:

| Files | 0 file refs | 1 file ref | many file refs |
|---:|---:|---:|---:|
| 2 700 | 0.112 s | 0.115 s | 0.137 s |
| 10 000 | 0.200 s | 0.353 s | 0.388 s |
| 25 000 | 0.302 s | 0.775 s | 0.844 s |
| 50 000 | **0.313 s** | **1.278 s** | 1.363 s |

A vault that anchors nothing to a path no longer pays for the tree at all —
what growth remains in that column is loading the symbol index, not walking
the repository. One reference brings the walk back in full, and a hundred
cost 7 % more than one: **the term is the walk, not the references**.

So the unconditional O(files) is gone and a conditional one remains,
recorded as the honest state of affairs rather than declared solved. Making
*that* cheaper means an incremental file index — and a reference is
ambiguous when two files share its tail, so any shortcut that checks one
likely path instead of the whole map would turn an `AMBIGUOUS` into a
confident `MATCH`. Slow is a worse tool; wrong is a different tool.

**On noise:** with 0 notes there is nothing to skip, so full and
incremental do identical work — the cells where they differ by up to 40 %
are measuring the noise floor of a single run (roughly ±0.5 s at 10 000
files). The walk-share column is a ratio of much larger differences and is
robust; the per-cell saving percentages are not, beyond their trend.

## What this changes

Nothing yet, deliberately. These numbers exist to decide what to fix and in
what order, and fixing them inside the benchmark that found them is how a
tool ends up overfitted to its own test data.

What they do establish is that both b01 findings are structural rather than
incidental, and that they have different shapes: F1 is linear in the index
and hits a wall at a size real monorepos reach, while F2 is a floor that
rises with the repository and quietly cancels an optimisation the project
advertises.
