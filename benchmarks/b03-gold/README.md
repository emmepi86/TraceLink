# Benchmark 03 — a hand-written gold set

Speed benchmarks say how fast a tool is wrong. This one asks the question
TraceLink exists for: given real engineering knowledge about a real
codebase, does it connect that knowledge to the right code — and does it
ever connect it, confidently, to the wrong code?

**50 findings**, written by hand against the same large private backend as
[b01](../b01/), by reading the code and its history. None of them was
generated from TraceLink's output: the expectations were recorded first, and
the tool was run against them afterwards. The findings and the codebase stay
private; what is published is the scoring and what it found.

The mix was chosen to include what a real register contains — historical
regressions, deliberate workarounds, compliance constraints, architectural
decisions, integration defaults, a race condition, code that looks
cleanable and is not — and **11 adversarial cases**: names defined in
several modules, a partial qualified name, a renamed symbol, a moved file, a
note citing a path that contradicts its symbol, a closed finding that is
still true, generic words, and a path inside a directory the indexer skips.

```bash
python3 benchmarks/gold_score.py --repo /path/to/code \
    --register gold/FINDINGS.md --gold gold/gold.json
```

## Result

```
                                  before F6 fix     after
FALSE AUTHORITATIVE ASSERTIONS          1             0     (target 0)
SUPPORTED KNOWLEDGE RECOVERED         32/32         32/32   (100%)
```

| Outcome | before | after |
|---|---:|---:|
| correct match | 32 | 32 |
| correct ambiguity | 9 | **10** |
| correct no-assertion | 7 | 7 |
| unsupported assertion | **1** | **0** |
| false ambiguity | 1 | 1 |

The fix is described below and in the changelog; the harness was not
touched between the two runs, and recall did not move.

Adversarial findings handled as expected: **10 / 11**.

The two headline numbers are graded differently on purpose. A missed match
costs a reader a piece of memory. A wrong match or an unsupported assertion
hands them a false one, and a memory layer that is confidently wrong is
worse than none, because it is believed. 85% recall with zero false
assertions is a better result here than 99% with a few.

## The one false assertion — F6

A finding said, in ordinary prose, that a type is used "for landing pages
and for signatures". One of those plain words is also the name of a pytest
fixture function in the test suite, so it is in the index like any other
definition — and the linker takes bare words as candidates, not only
backticked ones. The word resolved uniquely and became an anchor the author
never asserted.

The finding's *intended* subject was correctly reported as ambiguous at the
same time. The defect is not a wrong disambiguation; it is that prose
becomes evidence. The documented contract asks authors to name code in
backticks and `lint` rewards it, but the resolver does not require it.

Registered as **F6** and not fixed in this run — tightening extraction
inside the benchmark that found it is how a tool gets fitted to its own test
data. It was fixed afterwards, once [b04](../b04-context/) had measured that
**all 56 useful anchors already carried explicit evidence**, so requiring it
could not cost recall on real findings. A bare word is still a candidate and
still counts toward an ambiguity; it can no longer produce a match on its
own. Both benchmarks were then rerun unchanged: false assertions 1 → 0,
recall 32/32 → 32/32.

## The false ambiguity — F7

An adversarial finding used two generic short names to check that they
produce nothing. One of them is defined in several test modules, so it was
reported as *ambiguous* rather than filtered as prose. No anchor was
asserted, so nothing false was claimed — it is noise, not a lie. Registered
as **F7**, unfixed.

## Two corrections to the gold, not to the tool

Two findings expected a unique match on names that turn out to be defined
in both the backend and the frontend. The expectations had been written from
a scan of Python files only, while the index covers TypeScript as well:
**TraceLink was right and the gold was wrong.** Both were corrected and
marked as corrected in the private gold file, because a benchmark that
quietly edits its expectations to match the tool is measuring nothing.

## What this does and does not establish

It establishes that on 50 pieces of real knowledge about one real codebase,
every resolvable finding was connected to the code its author meant, every
genuine ambiguity but one was refused rather than guessed, and one anchor
was asserted that nobody wrote.

It does not establish precision on a second codebase, in another language,
or with findings written by someone else. One gold set on one repository is
evidence, not a claim about the world.
