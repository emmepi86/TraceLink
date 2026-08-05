# Changelog

## 0.7.1 — what dogfooding taught us

Every change in this release was diagnosed by running tracelink against a
real Next.js project and by benchmarking agents with and without the vault
(see ROADMAP.md for the claim those benchmarks tested).

- **The scan backend now indexes `export const` and named default
  exports.** On the benchmark repository the old patterns missed the
  dominant Next.js forms — `export const getImmobili = ...`,
  `export default function robots()` — leaving the most-cited symbols out
  of the index and the consult hook silent on their files. Coverage on that
  repository went from 973 to 1,149 names.
- **Lint anchors must be reliable.** Real findings cite properties, env
  vars and YAML keys in backticks all the time: 9 of 10 real notes warned
  `unknown-symbols` while linking perfectly, and a gate that always cries
  is a gate that gets ignored. Unknown names now warn only when a finding
  cites no reliable anchor at all; beside a known symbol they are
  informational (`infos` in the JSON, additive). Reliable means: not a
  stopword, and not ubiquitous — a name defined in more than 4 distinct
  files (Next.js route configs like `dynamic` and `revalidate` are
  everywhere) anchors nothing.
- **`status` distinguishes ambiguity from tampering.** A note whose only
  references are ambiguous never enters the link-state — by design — and
  status reported it as a problem forever. It now reads the Ambiguous
  references section of CODE-INDEX.md and reports such notes as
  `ambiguous by design`, not as unverified.
- Small knives sharpened: `index` creates the parent directory of `--out`
  instead of failing; the plugin's over-threshold hint prints once per
  project (persistent sentinel), not once per turn; a pre-existing
  non-UTF-8 post-commit hook gets a clean refusal instead of a traceback;
  the minimum identifier length is one public constant
  (`DEFAULT_MIN_LEN`) shared by linker and lint; the linker names status
  needs are public API instead of borrowed privates.

## 0.7.0 — the memory loop

0.6.0 laid the rails; 0.7.0 closes the loop the ROADMAP promised: agents
write the register, and the codebase warns the next agent. Both sides are
**off by default** — `.tracelink/config.json` opts a project in:

```json
{"consult": true, "capture": true, "register": "FINDINGS.md"}
```

Values must be JSON `true` — a `"true"` string or `1` stays off, silently:
a memory feature that half-triggers is worse than one that visibly doesn't.

- **Consult on touch.** When Claude Code edits a file the vault knows about,
  the relevant findings are injected into the turn — id, status, severity,
  title, the symbols in that file with their lines, worst-first (open before
  closed, then by severity), capped at five with an "…and N more in
  CODE-INDEX.md" line after the pointer to the full notes. The lookup reads
  only the link-state sidecar: a file with no notes costs one config read
  and one JSON load (~0.2 ms), no note is opened, nothing is imported.
- **Capture on close.** If a session edited code and recorded nothing, the
  Stop hook sends the agent back once — with instructions to append durable
  discoveries to the register in the linkable-findings contract (explicit
  headings, `### STATUS:`/`### SEVERITY:`, named symbols), or to append
  nothing if nothing qualifies. Once per session: a `SessionStart` hook
  clears the session markers, so an ignored prompt in one session cannot
  silence capture forever — the failure mode the first cut of this feature
  shipped with, caught in review by replaying three sessions.
- **`tracelink lint`.** The quality gate that makes auto-capture safe:
  findings that name no linkable identifiers, cite symbols the index does
  not know, nearly duplicate an existing note's title, or omit
  `STATUS:`/`SEVERITY:` are each a warning, and any warning is exit 1. The
  register is read, never written — like everywhere else in the tool.
  `--new-only` checks only findings not yet split into the vault.

## 0.6.0 — the rails

The direction is set out in [ROADMAP.md](ROADMAP.md): TraceLink is becoming
the long-term memory of AI-assisted coding — file-based, tool-agnostic,
verifiable. 0.6.0 removes everything a user had to remember to do, and one
thing they should never have had to discover.

- **The PyPI package is `tracelink-vault`.** The bare name `tracelink` on PyPI
  belongs to an unrelated third-party project, so the README's own install
  instructions handed readers someone else's code. The command is still
  `tracelink`; the README now says all of this out loud. Publishing runs from
  a tag-triggered workflow via trusted publishing — `RELEASING.md` documents
  the one-time setup.
- **The version is declared once.** It lives in four files, and 0.5.1 shipped
  with the plugin manifests still saying 0.5.0. `scripts/bump.py --check`
  now fails CI on drift; `--set` rewrites every declaration from
  `pyproject.toml`, the single source of truth.
- **Dotted references resolve without guessing.** `payments.validate` links
  through a registered qualified name, or — when the backend cannot qualify —
  through the path suffix (`payments.py`, `payments/validate.py`,
  `payments/__init__.py`). A prefix that matches two locations stays
  ambiguous; one that matches none withholds the link (contradictory evidence
  is not no evidence); a dotted claim and a cited path that disagree are
  reported, never arbitrated. `self.`, `cls.` and `this.` are grammar, not
  location evidence — they are stripped, and the tail behaves as a bare name.
- **Hotspots.** "Three notes about one function is a signal worth seeing" was
  the README's argument; now `CODE-INDEX.md` shows it unprompted — symbols
  with two or more notes, and per-file rollups. On a vault with hotspots, the
  first `link --check` after upgrading reports the index as changed (exit 1):
  run `link` once to adopt the new section.
- **Linking is incremental.** A sidecar (`.tracelink-link-state.json`) records
  content hashes and resolved locations; unchanged notes skip candidate
  extraction and disambiguation entirely — ~4–5× faster on an unchanged
  vault — and their managed blocks are still byte-compared against the cached
  rendering, so a hand-edited block is caught and repaired. Correctness rules:
  a moved symbol relinks every note that named it, `--check` ignores the state
  and never writes it, a corrupt or older state falls back to a full pass,
  `--full` forces one.
- **`tracelink status`.** One command answers: is the vault in sync with the
  register, is the index fresh, are the links current, what is open and high?
  Unknowns are declared as unknowns — status estimates nothing. `--strict`
  turns problems into exit 1 for CI; `--format json` is stable and pure.
- **`tracelink hook`.** The README said "wire it into a hook"; now the tool
  does it: `hook install` writes a marker-delimited block into
  `.git/hooks/post-commit` (worktree-aware, append-only next to existing
  hooks, idempotent), `hook remove` deletes exactly that block, `hook status`
  reports. The hook never blocks a commit.
- **The Claude Code plugin keeps the vault fresh by itself.** Edits mark the
  vault stale (a touch, under 10 ms); the end of the turn refreshes index and
  links once, silently, and never blocks the session. Repositories past the
  scan ceiling are skipped with a hint instead of a stall.

## 0.5.1 — safety and diagnostics, from first real-project use

Found by running tracelink against a clinical codebase rather than by review.
Each was defensible behaviour that left the user with no way forward.

- **A vault belongs to one register.** Splitting a second register into an
  existing vault rewrote `INDEX.md` to describe only the newcomer and orphaned
  the notes already there — formally valid, semantically false. The manifest
  gains `schema_version: 2` with a `register` identity, and `split` refuses a
  different prefix or a different source file. `--adopt-vault` is the explicit
  way through; multi-register merging stays deliberate rather than a side
  effect of running `split` twice. v1 manifests are still read.
- **Identifiers are accepted as written.** A register whose findings read
  `### F1` produced nothing, because the pattern required a hyphen. It is now
  optional and the id is *preserved*: `F1` stays `F1`. Rewriting a human
  identifier to suit the tool is a cost paid by every reader of the register,
  forever, to save one regex. Prefixes containing hyphens work, so `P1-CQR-4`
  splits and sorts — the old `split("-")[-1]` broke on both cases.
- **Failure says what it found.** "No headings matched" named the pattern it
  wanted and nothing else. It now prints the first headings and the identifier
  styles present, with `--inspect` for the full list and no writes.
- **Notes that link nothing are named, with the cause.**
  `notes_with_matches: 10` compressed three different problems into one number:
  a finding written only in prose, candidates filtered as too common, and
  symbols that were only ambiguous call for three different fixes.
  `--report-unlinked` lists them, `--require-linked` fails CI on them, and the
  JSON output carries `unlinked_notes`.
- **Ambiguous references stay visible.** Withholding the link is right —
  guessing between two definitions is worse than abstaining — but dropping the
  reference from `CODE-INDEX.md` left silence exactly where the answer was two
  answers. They now have their own section with the referencing notes and every
  candidate.
- **README documents the editorial contract.** Linking is lexical: explicit
  identifiers and qualified names produce strong links, prose does not.

77 tests.

## Unreleased

- **Graphify line locations no longer crash indexing.** `source_location`
  accepts numeric lines, display forms such as `L88`, and ranges such as
  `L88-L94` (anchored to the first line). Unknown shapes retain the symbol with
  no line number instead of aborting the entire index.

## 0.5.0 — a package and one command

No behaviour changes. The three scripts become a package with a single entry
point, and the two legacy schema reads found during the 0.4.2 review are fixed.

- **`pipx install tracelink`**, then `tracelink index | split | link`. Built as
  a wheel and verified by installing it into a clean virtualenv and running the
  full sequence from the installed command, not from the checkout.
- **`src/tracelink/`** with `symbol_index`, `splitter`, `linker`, `cli`.
- **The scripts still work.** `python3 scripts/link.py ...` behaves exactly as
  before, because a tool that only runs after installation is a tool people
  cannot try before deciding whether to install it.
- **`requires-python = ">=3.11"`.** 3.8 and 3.9 are out of support; shipping a
  2026 release against them claims a compatibility nobody is testing.
- Still **no runtime dependencies**.

Fixed, both reading v2 fields from a v3 index:

- `CODE-INDEX.md` reported `backend: unknown` while the backend was known.
- The final line printed `repo_commit`, absent in v3, so it printed nothing —
  duplicating the freshness block above it, and wrongly.

63 tests.


## 0.4.2 — the scope is now persisted, so the linker can reproduce it

0.4.1 scoped the fingerprint at indexing time and did not record WHAT it had
scoped. The linker recomputed over the whole tree, so the two digests described
different sets of files and **a freshly written index came out `stale`
immediately**. The release was broken end to end.

Its tests could not see it. They compared two direct calls to `fingerprint()`
and never exercised `verify_freshness` — the caller that failed to pass the
scope along. Same blindness as the `note_body` case in 0.2.1: *a test that skips
the caller cannot see the caller's mistake*, twice now, which is why the new
tests all run through `verify_freshness`.

- **`indexing.scope` and `indexing.files_considered` are persisted**, and the
  verifier RE-DERIVES the scope rather than replaying the file list. Replaying
  it would miss a source file added after indexing: never hashed, digest
  unchanged, `fresh` verdict over an unindexed symbol.
- **Per-backend honesty.** `scan` rebuilds its scope exactly from extensions and
  excludes. `ctags` and `graphify` can only be as good as the `tags` file or
  `graph.json` on disk right now, and return `unknown` when those are missing.
  Pretending all three verify equally would be the same overclaim this project
  keeps removing.
- **A v3 index without a recorded scope is `unknown`**, not silently rehashed
  against a different set. That covers every index written by 0.4.0 and 0.4.1.
- `tracelink_version` in the index said `0.4.0` in both later releases.

Verified end to end, through `verify_freshness`:

```
fresh index      fresh      source modified   stale
README modified  fresh      source added      stale
vault written    fresh      source removed    stale
```

63 tests.


## 0.4.1 — freshness of the index, not of the repository

0.4.0 answered the wrong question. It hashed every file in the tree, so a
README, a CHANGELOG or tracelink's own vault marked the index stale — none of
which can change a symbol map. "Something changed" and "the symbol index
changed" were treated as the same statement.

- **The fingerprint now covers the files the backend actually read.** Each
  backend returns its scope: extensions it parsed (`scan`), paths in the tags
  file (`ctags`), `source_file` of the nodes (`graphify`). Same family as the
  `symbols.json` fix in 0.4.0, generalised — the tool no longer invalidates
  itself through anything it or its user writes alongside the code.
- **`partial` survives.** `build()` returned as soon as a backend produced
  symbols, discarding the note that came with them, so a scan truncated at the
  file limit reported `partial: false` — a completeness claim that was untrue.
  The note is recorded before the return.
- **JSON is emitted on the failure path too.** `--format json --freshness
  require` printed prose to stderr and exited, giving a CI consumer nothing
  machine-readable exactly where it needs it. The payload now always carries
  `ok` and `exit_reason`.
- **Two contract tests made exact.** The v2-index case asserted `unknown or
  stale`; the contract is `unknown` on a clean tree and `stale` on a dirty one,
  and both are now pinned with a repository built for the purpose. The previous
  looseness would have hidden a regression.
- Removed `_repo_commit`, duplicated by `repo_state` and still carrying a
  comment describing behaviour that no longer existed.

58 tests.


## 0.4.0 — freshness is verified, not recorded

0.3.x stored provenance and printed it. It never compared it, so an index built
on one commit was used against another without a word. The changelog said a
stale index "can be detected instead of trusted" — true of a consumer, not of
tracelink. Now it is true of tracelink.

### Added

- **Repository fingerprint**: `sha256` over `relative/path\0sha256(content)` records,
  sorted by normalised path. Independent of filesystem order, mtime, inode,
  absolute path and path separator — each of which changes without the code
  changing. Content alone decides.
- **Four explicit states** — `fresh`, `stale`, `unknown`, `invalid`. A boolean
  cannot say "I do not know", and rounding unknown to either side is how a tool
  starts lying quietly.
- **Schema v3**: `repository` (root, vcs, commit, dirty, fingerprint, files
  counted) and `indexing` (backend, partial, warnings, configuration and its
  fingerprint). `root` is logical (`"."`) so an index is usable from another
  checkout and does not leak the author's filesystem.
- `--repo`, `--freshness warn|require|ignore`, `--require-fresh-index`,
  `--allow-partial-index`, `--format text|json`.
- `--check` fails when the index is stale, and when it is unverifiable under
  `require`.
- **Completeness reported separately from freshness.** An index can be
  `fresh + partial`: it matches the tree and still does not cover all of it.

### Decision rules

The fingerprint decides when present, because it covers uncommitted work and
repositories with no VCS. Failing that, a commit can prove divergence but never
correspondence — the working tree is invisible to it — so a matching commit
without a fingerprint is `unknown`, not `fresh`.

### Fixed

- **The index no longer invalidates itself.** Writing `symbols.json` inside the
  repository made the tree stale the instant the index was written. Found by a
  test that indexed into its own fixture directory.

### Compatibility

v1 and v2 indexes still load. v1 is `unknown` — legacy-index-without-provenance —
and is never rejected outside `require`.

### Not done

The linker does not rebuild a stale index. It prints the command and stops:
rebuilding implicitly would hide an operational change and make `--check` stop
being purely verificative.

52 tests.


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
