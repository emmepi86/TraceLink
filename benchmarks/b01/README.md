# Benchmark 01

**One large private multi-tenant SaaS backend.** One codebase, one machine,
one run: these numbers describe that repository and generalise no further.
They are published because of what the run found, not because they are a
score.

Redacted by design — the target's name, its commit, absolute paths, module
and symbol names and internal dates are not here, and the raw results are
not published. What is here is everything needed to judge the findings:
[`results-redacted.json`](results-redacted.json).

## The target, as TraceLink sees it

| | |
|---|---|
| Indexable files | 2 737 |
| Indexable lines | 515 181 |
| Symbols (scan backend) | 13 866 |
| Languages | `.py` 1705 · `.tsx` 521 · `.js` 335 · `.ts` 168 · `.php` 6 · `.sql` 2 |
| Files walked / directories skipped | 8 295 / 48 |

The repository on disk is an order of magnitude larger; most of it is
vendored dependencies the indexer skips. What TraceLink reads is half a
million lines.

## Timings

Three repeats per step; cold is the first, warm the median and minimum of
the rest. `consult` and `explain` are sampled over every target the vault
links, reported as p50/p95. The register is a performance fixture of 100
findings generated from indexed symbols.

| Step | Whole repository (scan) | Subtree (graph backend) |
|---|---|---|
| index (cold) | 1.60 s | 0.77 s |
| split (100 findings) | 0.025 s | 0.024 s |
| link, full | 0.84 s | 0.69 s |
| link, incremental | 0.93 s | 0.85 s |
| consult, file — p50 / p95 | 9.9 / 13.9 ms | 8.6 / 10.5 ms |
| consult, symbol — p50 / p95 | 10.3 / 13.3 ms | 7.9 / 10.8 ms |
| explain — p50 / p95 | 9.0 / 10.7 ms | 7.8 / 9.6 ms |
| doctor | 0.004 s | 0.004 s |
| link state on disk | 1.51 MB | 1.15 MB |

`index` cold ≈ warm: it is CPU-bound parsing, and the page cache does not
help it.

## What the run found

Four defects in TraceLink, and one observation. None was fixed while the
benchmark that found them was still running.

### F3 — staleness of a backend's *input* is invisible  ·  P0

The graph backend read an artefact generated 36 days before the code it
described. TraceLink reported the index as `fresh`, reason
`fingerprint-match` — which is formally true, because freshness compares the
repository's fingerprint at index time with now, and the index had just been
built. The *content* came from a month-old graph.

Probed on 300 sampled symbols, asking whether the recorded line still
contains the symbol it names:

| Backend | Correct | Wrong |
|---|---|---|
| scan (reads the tree) | 300 / 300 | 0 |
| graph (month-old artefact) | 175 / 300 | **125 (42%)** |

Freshness is transitive and the chain is not checked. *The derived index is
unchanged* was reported where *the evidence it was built from is current*
was meant — the same distinction TraceLink makes elsewhere between an
anchor still resolving and a finding still being true.

### F4 — a path-base mismatch passes silently  ·  P0

The graph artefact records paths relative to the repository root, while its
own location forces `--repo` one level down. Every emitted path therefore
resolved to nothing. 104 links were written anyway; the only signal was
`index_completeness: partial`, which says something else.

### F1 — `consult` pays, on every edit, for data it never reads  ·  P1

| Part of the link state | Bytes | Share |
|---|---|---|
| `symbol_locations` | 1 417 793 | **93.9 %** |
| `notes` | 51 133 | 3.4 % |

Parsing the file: 9.78 ms. Measured `consult` p50: 9.91 ms. The per-edit
cost is essentially all parsing of a structure only the linker uses — so it
scales with the size of the **index**, not the size of the vault.

### F2 — incremental linking saves a quarter, not 4–5×  ·  P1

| | s |
|---|---|
| link on an **empty** vault — fixed cost | 0.638 |
| link, 100 notes, full | 0.843 |
| link, 100 notes, incremental | 0.931 |

The fixed cost — the tree walk that resolves file anchors — is 76% of the
total. The 4–5× measured on small repositories is real there and not here.

### O1 — one backend emits module docstrings as symbol names  ·  P2

Index noise, ingested without a filter.
