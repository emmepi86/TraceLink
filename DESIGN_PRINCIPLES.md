# Design principles

> **No conclusion may carry more certainty than the evidence it came from.**

Everything below follows from that one line. It is not a style preference: each
principle here was extracted from a defect that shipped, and each defect was
silent — the tool reported success while producing a wrong answer.

---

## 1. Never consume your own output as input

A tool that reads what it wrote will confirm itself.

0.1.0 matched over the whole markdown file, frontmatter and generated block
included. It linked both example notes to `severity` — a function inside its own
`split.py` — and reported *"2/2 notes linked"*. Every statistic downstream was
formally correct and substantively false: the backlink table, the
most-referenced ranking, the percentage of notes linked. All computed correctly,
over evidence the tool had manufactured.

The same defect made links immortal. A symbol removed from the prose survived,
because the next run rediscovered it in the block written by the last one.

**Rule.** Separate authored content from generated content with explicit
markers, and strip the generated part before any analysis. If you cannot tell
the two apart in code, you cannot tell them apart in a result either.

## 2. The documented contract must traverse the real path

0.2.0 documented `### STATUS: CLOSED` and did not honour it. The classifier
understood the syntax perfectly; the caller filtered those headings out before
calling it. A note marked closed came out open.

The tests missed it because they called the classifier directly, bypassing the
very filter that broke it. **A test that skips the caller cannot see the
caller's mistake.**

**Rule.** Test the documented entry point, not the function you happen to have
written. A unit test that never touches the path a user takes is evidence about
a function, not about a product.

## 3. Ambiguity is a state, not a tie to be broken

0.2.x kept one location per symbol name and discarded the rest. A note naming
`validate`, where two modules define it, was linked to whichever definition the
backend returned first — an answer that depended on filesystem order,
reproducible only by coincidence, and carried no warning.

Collapsing several candidates into one is not a simplification. It is a claim of
uniqueness that the evidence does not support.

**Rule.** Model the three outcomes distinctly:

```
MATCH        exactly one candidate, or the author disambiguated
NO MATCH     no candidate
AMBIGUOUS    several candidates and nothing chooses between them
```

`AMBIGUOUS` is a result, and it is reported with all its candidates. Anything
built on top can then reason about certainty instead of re-deriving it from an
answer that looks definite.

## 4. Disambiguation is the author's decision, ranked by directness

```
explicit override in the note's frontmatter
> path named in the note
> qualified name used in the note
> nothing — stay ambiguous
```

Each level is a statement the author made. None is an inference the tool made on
their behalf.

**Rule.** Probabilistic resolution does not belong in the core. A future
`suggest` command may propose candidates with a confidence and the evidence
behind it — and must not turn a suggestion into a link until a human accepts it.
The moment a guess is written into the artefact, it becomes indistinguishable
from a fact.

## 5. Fail open when deleting, fail closed when asserting

The two directions are not symmetric, and the asymmetry follows from what is at
stake.

- **Removing** something the evidence does not clearly condemn destroys
  information. When a symbol cannot be located, or a concept cannot be judged,
  keep it.
- **Asserting** something the evidence does not clearly support fabricates
  information. When a criterion cannot be evaluated, say so; do not credit it.

**Rule.** Decide which failure your operation can cause, and default against it.
A guard that deletes on uncertainty and a guard that asserts on uncertainty are
different tools wearing the same name.

## 6. Distinguish "not applicable" from "not satisfied"

These are different answers and must not collapse into each other:

```
present          the evidence is there and supports it
absent           the evidence is there and contradicts it
unknown          the evidence is missing
ambiguous        the evidence supports several answers
not applicable   the rule does not govern this case at all
```

A rule that does not apply should never enter the evaluable set. If it does, its
"unevaluated" state will eventually be read as a verdict by something downstream
— and the further that reading travels from the point of measurement, the more
authoritative it looks.

This is the principle that generalises furthest, and it is the one most often
lost in plumbing rather than in logic: the correct answer is computed, then
discarded by a layer that only knows about two states.

## 7. Provenance and staleness are part of the result

An index says where things were, at a moment, according to some backend. Record
which backend, which commit, and whether the scan was truncated. An answer
derived from a stale index is not wrong — it is unfalsifiable, which is worse,
because nothing about it looks doubtful.

**Rule.** Ship the metadata that lets a consumer distrust the result.

## 8. Report what you did not do

Counts that silently omit are the most persuasive form of wrong. If a run
skipped files, truncated a scan, capped a list or could not judge an item, say
so beside the number.

`282 of 300 replayed, 18 had no ingestion to replay` is a true statement.
`282 replayed` is a misleading one, and it is misleading in the direction that
flatters the tool.

---

## Where this came from

These are not theoretical. Each maps to a shipped defect, found by review or by
measurement rather than by use:

| principle | defect |
|---|---|
| 1 | 0.1.0 linked notes to a symbol in its own source and called it success |
| 2 | 0.2.0 documented a grammar the CLI never reached |
| 3 | 0.2.x resolved duplicate names by filesystem order |
| 5 | a guard deleted a condition asserted later in the same document |
| 6 | a criterion that could not apply was credited as satisfied |
| 8 | a completion figure that counted only what was attempted |

The pattern across all of them is the same, and it is worth naming: **the number
was large and correct, and the phenomenon it described did not exist.** Silent
failure is not the absence of output. It is confident output over evidence that
was never checked.
