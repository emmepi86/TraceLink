# Benchmarks

`harness.py` measures TraceLink against a real repository without touching
it: every artefact it produces is written outside the target tree, and each
run ends by checking that nothing under the target was created or modified
while it ran. A benchmark that quietly edits its subject measures the wrong
thing twice.

```bash
python3 benchmarks/harness.py --repo /path/to/code --out results/ --backend scan
```

It reports what the repository is (files and lines the indexer actually
sees, not what `find` would count), how long each step takes cold and warm,
and p50/p95 for `consult` and `explain` over every target the vault links.

The register it uses for timing is generated from symbols the index already
found. That is deliberate and it is labelled in the file itself: timings
from a fixture are honest, precision numbers from one would not be.
Accuracy needs findings a human wrote about code they know, and is measured
separately.

## Benchmark 01

A large private multi-tenant backend — roughly half a million indexable
lines across Python and TypeScript, ~14k symbols. Results are not published
here; what came out of it, in the first run, was four defects in TraceLink
itself, each recorded before being fixed:

- `consult` pays, on every edit, for parsing a part of the link state it
  never reads — so its per-edit cost scales with the size of the *index*
  rather than the size of the vault;
- incremental linking saves about a quarter, not the 4–5× measured on small
  repositories, because the fixed cost is the tree walk;
- staleness of a *backend's input* is invisible: an index built from a
  month-old graph reported `fresh` while 42% of its line numbers no longer
  held the symbol they named;
- a mismatch between the path base a backend records and the `--repo` given
  produces links whose paths resolve to nothing, silently.

None of them was fixed while the benchmark that found them was running.
Widening a heuristic to turn a benchmark green is how a tool ends up
overfitted to the one repository it was tested on.
