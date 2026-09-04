# Benchmark 04 — what an agent actually receives

[b03](../b03-gold/) asked whether findings land on the right code. This asks
the question an agent lives with: when it touches a file or a symbol, how
much of the context that comes back is useful, how much is noise, and how
much is *asserted without evidence*.

**43 targets** on the same private codebase — the files and symbols the b03
gold anchors to, plus five adversarial ones: a test module whose fixture
carries a domain word, three names defined in several places, and a generic
short name. Each target carries `must_include` and `acceptable` sets written
by hand; everything else is noise.

```bash
python3 benchmarks/context_quality.py --repo /path/to/code \
    --vault vault/ --targets targets.json
```

## Result

```
CONTEXT RECALL           56/56   (100%)
CONTEXT PRECISION        92%
FALSE-AUTHORITY EXPOSURE   3     (target 0)
```

| Verdict | |
|---|---:|
| useful | 56 |
| false authority | **3** |
| acceptable | 3 |
| benign noise | 2 |

| Shape of a briefing | |
|---|---|
| findings per target | median 1, max 9 |
| size | median 274 B, max 1 126 B |
| first useful finding | position 1 (median) |
| duplicates | 0 |
| `consult` latency | 0.64 ms (median) |

## Three failure classes, kept apart

They are not averaged into one score, because only one of them is
dangerous.

**A — wrong evidence: 3 occurrences, 2 findings.** Both are
[F6](../b03-gold/): a plain word in the prose also names an indexed symbol,
so the finding surfaces at a target its author never pointed at. This is not
a ranking problem. Ranking it lower leaves it there, believed by whoever
reads far enough.

**B — irrelevant context: 2.** One is worth its own name. A finding cites a
path *in order to deny a relationship* — "this has nothing to do with that
module" — and is anchored to it anyway. TraceLink reads no semantics and so
cannot see the negation; the anchor is formally correct and contextually
backwards. Registered as **F8**, unfixed.

**C — bad ordering: 0.** On the busiest target, all nine findings were
useful and arrived in anchor order; across every target the first useful
finding sits at position 1 in the median case. **No data here supports a
`--budget` flag or a new ranking**, which is the useful negative result: the
feature that seemed obviously needed is not, at this size.

## Two flaws found in the benchmark, not in the tool

The first scoring pass reported **zero** false authority. It was wrong: it
read the backticks out of the note *as it sits on disk*, and `link` writes
its chosen anchors into that note in backticks — so every anchor looked
authored and the tool was certifying its own evidence. The managed block is
now cut out before the check.

The second pass then reported five, of which two were the scorer being too
strict: a qualified name in backticks (`module.function` for the anchor
`function`) is authored evidence and TraceLink documents it as such. Both
fixes are in the script; neither touched the resolver, which stayed frozen
for the whole benchmark.
