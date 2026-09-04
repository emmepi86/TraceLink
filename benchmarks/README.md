# Benchmarks

`harness.py` measures TraceLink against a real repository without touching
it: every artefact it produces is written outside the target tree, and each
run ends by checking that nothing under the target was created or modified
while it ran. A benchmark that quietly edits its subject measures the wrong
thing twice.

```bash
python3 benchmarks/harness.py --repo /path/to/code --out results/ --backend scan
```

The register it uses for timing is generated from symbols the index already
found. That is deliberate and labelled in the file itself: timings from a
fixture are honest, precision numbers from one would not be. Accuracy needs
findings a human wrote about code they know, and is measured separately.

- [`b01/`](b01/) — one large private codebase, redacted results.
