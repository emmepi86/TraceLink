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
- [`b05-home-assistant/`](b05-home-assistant/) — **the first codebase we did not write**, published whole: findings, gold and results.
- [`b06-erpnext/`](b06-erpnext/) — a second external codebase, where the knowledge is business rules rather than device quirks.
- [`b07-zulip/`](b07-zulip/) — a third, chosen as a control for heavily documented projects.

## An experiment we invalidated

An agent A/B experiment — same model, same repository, same tasks, with and
without TraceLink — was **halted after eight pilot runs and no effectiveness
claim was made**. Two defects in the harness, not in the tool, made the
comparison uninterpretable:

- the baseline agents inherited project memory from outside the repository,
  which already contained the operational knowledge under test, so the
  control arm was not a control;
- the knowledge the treatment arm was supposed to receive had been frozen in
  a design document and never written into the register the tool reads, so
  thirteen consultations returned "nothing recorded".

The runs are kept as harness diagnostics and enter no percentage. The
pre-qualification that preceded them did produce a result worth keeping:
nine of fourteen candidate "traps" were disqualified before any run, because
the codebase documents those constraints at the point of use — which is
where a well-maintained repository *should* carry them.

That is the honest shape of the claim this project can make:
**TraceLink is for the knowledge code cannot carry by itself** — why
something must keep working this way, what has already failed, which
constraints live outside the repository, which non-local relationships must
be preserved. Where a comment next to the function does the job, it does the
job.
