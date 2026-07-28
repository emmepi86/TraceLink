# Changelog

## 0.3.1 — the graphify backend still dropped duplicates

0.3.0's central promise was that a name defined twice is never resolved by
accident. One backend did not keep that promise.

- **`from_graphify` discarded every duplicate.** `_add()` was written to keep all
  definitions, and the graphify path still ran `if not label or label in out:
  continue` — so the second definition of a name was dropped before reaching it,
  and that backend went on resolving homonyms by node order. The suite missed it
  because no test exercised a backend with two same-named definitions; there is
  now one per backend.
- **Contradictory evidence stays ambiguous.** Disambiguation returned the first
  match it found, so a note naming BOTH `users.validate` and `payments.validate`
  was linked to whichever came first in the index — the same accident, one level
  up. Two qualified names, two paths, or a qualified name and a path pointing at
  different definitions now return `multiple-qualified-names`,
  `multiple-paths-in-note` and `qualified-name-and-path-disagree`. An explicit
  override still wins, because it is a structured decision rather than prose;
  a qualified name and a path are authorial evidence of equal weight, and
  inventing a precedence between them would be the tool deciding for the author.
- **Ownership is structural.** `is_owned_note` requires `tracelink_schema: 1` in
  the frontmatter. A substring search made a hand-written note that merely
  *mentions* the marker look owned, and therefore rewritable.
- **Pruning cannot leave the vault.** A manifest entry must match
  `<PREFIX>-<n>.md`, resolve inside the vault after `realpath`, and carry the
  ownership marker in its frontmatter — three independent conditions, because
  deletion is irreversible and any one of them alone has a way to be wrong.
- **`qualified_name` is `null` when the backend cannot qualify.** Repeating the
  bare name and labelling it qualified made the qualified-name path silently
  useless. ctags now reads `class:` / `struct:` / `namespace:` / `scope:` when
  present; graphify uses `norm_label` only when it actually adds a namespace.

41 tests.


## 0.3.0 — ambiguity is data, not a guess

- **Symbol schema v2: every definition is recorded.** v1 kept one location per
  name and discarded the rest, so a finding naming `validate` where two modules
  define it was linked to whichever the backend returned first — an answer that
  depended on filesystem order and carried no warning.
- **An ambiguous symbol is never linked automatically.** It is reported with all
  its locations and left alone. Three ways to resolve it, in order: the note
  names the qualified form (`payments.validate`), the note mentions the defining
  path, or the note's frontmatter overrides it.
- **Qualified names** (`module.symbol`) where the backend can supply them. Corrected in 0.3.1: only the built-in scan could, and the other two repeated the bare name and called it qualified.
- **Provenance in the index**: `schema_version`, `backend`, `repo_commit` and any
  backend notes, so a consumer *can* detect a stale index. tracelink does not
  detect it yet — the linker prints the commit and does not compare it. Wording
  corrected in 0.3.1; automatic detection is 0.4.0.
- **`ambiguous_matches`** joins the metrics; `--explain` reports how each link
  was resolved.
- v1 symbol files still load — the linker normalises both shapes.


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
