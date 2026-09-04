# F2b — what evidence lets us stop re-reading every file

Design note. **No production code was changed to write this.**

Verifying that an index still describes its repository costs 2.5 s on a
real 2 737-file codebase and 5.8 s on a 50 000-file one — 75% of `link`'s
floor (benchmark 02). The question is not how to hash faster. It is which
evidence can *replace* hashing without weakening what `fresh` means.

## The rule that must survive

> `fresh` requires positive evidence of correspondence.

So "git says clean" is evidence only against a **known snapshot**. An index
built at another commit cannot be called fresh because the tree is clean
now, and `git status` being empty says nothing about a file git never
tracked.

## What each source can actually witness

Measured, not assumed (`benchmarks/f2b_freshness_evidence.py`), on a git
repository with tracked, untracked and ignored files:

| change | hash | `git status` | HEAD tree | hybrid |
|---|---|---|---|---|
| tracked file modified | yes | yes | **no** | yes |
| tracked file renamed | yes | yes | **no** | yes |
| tracked file deleted | yes | yes | **no** | yes |
| new file created | yes | yes | **no** | yes |
| **untracked file modified** | yes | **no** | **no** | yes |
| **ignored artefact modified** | **no** | **no** | **no** | **no** |
| mtime changed, content identical | no | no | no | no |

Three of these decide the design.

**The HEAD tree identity witnesses nothing about the working tree.** It
changes when the commit changes and not otherwise, so on its own it can
never support `fresh` — only *disprove* it when HEAD moved.

**`git status` cannot see the content of an untracked file.** It lists the
name; the bytes are outside what git vouches for. Anything untracked must
still be hashed.

**Nobody sees an ignored artefact change** — which is why upstream
provenance (ms-11a) exists as a separate mechanism rather than being folded
into freshness.

And the last row is the one to keep: a touched file with identical content
is *not* a change, and no strategy here reports one. **mtime and size stay
hints for skipping work, never evidence of currency.**

## Cost

Synthetic repositories, warm minimum of three runs:

| files | current (walk + hash) | git inventory + hash of what git cannot vouch for |
|---:|---:|---:|
| 10 000 | 0.56 s | **0.05 s** |
| 25 000 | 1.35 s | **0.10 s** |
| 50 000 | 2.95 s | **0.22 s** |

On the real codebase of benchmark 01 — 2 737 indexed files, read-only:

```
current:   walk 0.223 s + hashing 2.484 s = 2.707 s
git-only:  0.422 s                          → 6.4x
```

The shape matters more than the ratio: the new cost does not grow with file
*size*, because nothing is read unless git says it changed.

## Two constraints the measurement exposed

**Reading must not write.** `git status` refreshes the index by default,
which writes inside `.git` — unacceptable for a tool that promises only to
read the repository it observes. Every call must use
`--no-optional-locks`; the probe verifies `.git/index` mtime is unchanged
after a full verification, and it is.

**Git's inventory is not TraceLink's scope.** TraceLink indexes by
extension and skips directories by name; git knows tracked files and
gitignore. A file TraceLink indexes but git ignores would be invisible to a
git-only check — which would let `fresh` be claimed over a file that
changed. The index already records the set it covered, so the fix needs no
walk:

```text
evidence =
    HEAD tree identity                      (disproves: HEAD moved)
  + git status delta                        (tracked changes, untracked names)
  + content hash of  recorded_scope ∖ tracked-and-clean
```

Everything git can vouch for is taken from git; everything else is read.
The recorded scope is what keeps the two inventories reconciled.

## When there is no evidence to be had

- **no git repository** → full hashing, as today. Not a regression: it is
  the only evidence available.
- **recorded scope missing or unreadable** (an index from before this) →
  full hashing.
- **shallow clone / detached worktree oddities** → HEAD tree identity still
  works; the delta still works; nothing depends on history.

In every case the fallback is *more* work, never a weaker claim.

## Decision

**Worth implementing.** 6.4× on a real repository and an order of magnitude
on a large one, with no weakening of the evidence: every change the current
strategy detects is still detected, by the same probe that measures it.

It is not a small change — it touches how the index records its scope and
how the verifier reconciles two inventories — so it belongs in its own
ministep, with the detection matrix above as its acceptance test.

**Not adopted:** `git status` alone (blind to untracked content), tree
identity alone (blind to the working tree), and anything keyed on mtime or
size, which remain accelerators and never grounds for `fresh`.


---

# Implementation, and what it cost to be right

Shipped. The differential test — *same verdict as the full hash, every
case* — was the acceptance criterion, and it earned its keep three times.

**It caught a false `fresh`.** At 50 000 files the fast path said fresh and
the hash said stale. The scan had truncated at its file limit, so the index
had never read most of the tree: git could honestly report the tree
unchanged while the index was missing half of it. *The two facts were about
different sets.* A partial index now never takes the fast path.

**It caught an optimisation that broke a guarantee.** `git ls-files
--others --ignored --directory` collapses a wholly-ignored tree to one
entry, which makes the enumeration cheap — and makes the collapsed entry
extensionless, so an ignored *file* the indexer reads vanishes from the
candidate set and its appearance stops being detectable. Reverted.

**It caught the wrong instrument.** `git diff-index` trusts stat
information, so a file whose mtime moved but whose bytes did not comes back
as changed. That would have sent the most common case — a checkout, a touch
— down the slow path forever. `git status` compares content; it is used
instead, with the untracked walk switched off because those names already
come from `ls-files --others`.

## Where it pays, and where it does not

| repository | fast path | full hash | |
|---|---:|---:|---|
| synthetic, 10 000 files, scope = repo | **0.29 s** | 1.18 s | 4.1× |
| the b01 codebase, 2 737 indexed of 27 000 tracked | declines | 0.67 s | — |

Asking git costs in proportion to the **repository**: it enumerates
tracked, untracked and ignored names, and a vendored subtree is enormous.
Hashing costs in proportion to the **indexed scope**. Where the scope is
most of the repository git wins several times over; where 2 700 files are
indexed out of 27 000 tracked and 17 000 ignored, git loses by two.

So the fast path is taken only when the scope is at least a quarter of what
git tracks. That threshold picks **which correct road to walk, never which
answer to give** — the differential test asserts both roads agree, and the
b01 measurement above is the fast path declining and costing nothing.

The projected "6.4×" of the analysis above was measured *without* the
guardrails the design then required: the `assume-unchanged` inventory, the
ignored enumeration, the scope filter. Adding them ate the margin on that
repository. The analysis was not wrong to project it — it was wrong to
project it from a probe simpler than the thing it was modelling.
