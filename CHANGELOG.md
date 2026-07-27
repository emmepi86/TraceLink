# Changelog

## 0.2.1 — correctness follow-up

Review found the documented explicit grammar never reaching the CLI.

- **`### STATUS:` and `### SEVERITY:` now work end to end.** `note_body` filtered
  headings to `## RES-n` before classifying, so the explicit lines were discarded
  and a note marked CLOSED came out `open`. The 0.2.0 tests missed it because
  they called `classify()` directly — a test that skips the caller cannot see the
  caller's mistake. Finding headings and state headings are now tracked
  separately.
- **Generated notes carry `tracelink_schema: 1`,** and `link.py` refuses to touch
  anything without it. Pointing `--vault` at a folder of hand-written markdown
  can no longer rewrite it; skipped files are reported.
- **Stale notes are pruned** via a manifest. A findings that leaves the register
  takes its note with it — but only files the previous manifest recorded are ever
  deleted.
- **The example proves what the README claims.** RES-02's body now really
  contains the word CLOSED while staying open, and RES-01 carries explicit
  status and severity headings.
- **The end-to-end test asserts exact symbol sets** per note, not membership. The
  looser check would have passed a regression that added `severity` back.
- **CI** on 3.11 and 3.12: compile, unit tests, and the demo run twice with
  `--check` to prove idempotence.

## 0.2.0 — correctness

No new features. Every entry is a defect that produced silently wrong output in
0.1.0, found by review rather than by use.

- **tracelink no longer reads its own output.** Frontmatter and the managed
  block are stripped before matching. 0.1.0's demo linked both example notes to
  `severity` — a function inside `split.py` — and reported "2/2 notes linked".
  Every downstream statistic was formally correct and substantively false.
- **Links are no longer immortal.** The block is regenerated from the current
  match and removed when nothing matches. Previously the writer only ran when
  there were hits, so a note whose last symbol left the prose kept a stale block
  forever, and the next run rediscovered the symbol inside it.
- **`UNRESOLVED` is no longer classified as closed.** Legacy keyword matching
  moved to word boundaries; `RESOLVED` no longer matches inside `UNRESOLVED`.
- **The last explicit status and severity win.** A finding CLOSED then REOPENED
  is open; one downgraded from HIGH to LOW reads LOW. Added the explicit
  `STATUS:` / `SEVERITY:` grammar, with free-form keywords kept as a fallback.
- **Ranking before truncation.** Symbols inside backticks outrank bare
  identifiers, so `--max-links` no longer discards the strongest evidence for
  the weakest.
- **Atomic writes** via a temporary file and `os.replace`.
- **Separated metrics** — `notes_scanned`, `notes_with_matches`,
  `notes_modified`, `symbols_linked`. The old single counter reported files
  touched, not notes carrying links.
- **`--check`** writes nothing and exits 1 when anything is stale.
  **`--explain`** prints why each link was made.
- **The example now has a real source tree** (`examples/demo-project/`), so the
  demo links symbols that actually exist.
- **14 tests** under `tests/`, one per defect above, on `unittest` — still zero
  runtime dependencies.
- **Skill paths** use `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}`;
  relative paths only worked from the TraceLink checkout.

### Known, not addressed in 0.2.0

Duplicate symbol names still resolve to whichever definition the backend
returned first. A finding naming `validate` where two modules define it is
linked to one of them arbitrarily, with no warning. The fix is a versioned
symbol schema carrying every location plus an explicit ambiguity state, and it
lands in 0.3.0. Until then, prefer qualified names in findings.

## 0.1.0

First release.
