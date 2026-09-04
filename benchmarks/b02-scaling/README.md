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
