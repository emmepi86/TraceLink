# Benchmark 07 — Zulip

Third external codebase, chosen as a **control**: Zulip is famous for how
much it documents itself, so if TraceLink's value comes from knowledge the
code cannot carry, this is where there should be least of it.

`zulip/zulip` — 16 242 definitions, 13 241 distinct names. 15 findings from
its own merged pull requests.

## Result

```
FALSE AUTHORITATIVE ASSERTIONS   0    (target 0)
SUPPORTED KNOWLEDGE RECOVERED    12/12   (100%)
```

| Outcome | |
|---|---:|
| correct match | 12 |
| correct ambiguity | 2 |
| correct no-assertion | 1 |

## Two corrections to the gold, and one of them is a finding

The first run showed a wrong match and a false ambiguity. Both were the
gold's fault, and the second taught us something we had not seen on two
larger codebases.

**A path does not always disambiguate.** A finding named `process_request`
and cited `zerver/middleware.py`. That name is defined **five times inside
that one file** — one per middleware class — so every candidate shares the
cited path and the path resolves nothing. TraceLink refused the symbol and
anchored the file the author had named. Correct, and a property worth
stating: path-in-note evidence disambiguates *between* files, never within
one.

**Existence is not uniqueness.** A finding named `build_reactions` without
saying which importer; three importers define it. The expectation had been
built by checking the anchor exists, not that it is unique. TraceLink
refused; the gold was wrong.

Both corrected and marked as corrected in [`gold.json`](gold.json).

## The control question is still open

The reason for choosing Zulip was to ask whether a heavily documented
codebase leaves less room for an external memory. Answering that means
deciding, per finding, whether the knowledge is *already written next to the
code* — and that is a judgement, not a string match.

A mechanical proxy was tried: take the distinctive words of each piece of
knowledge and look for them in the anchor file's comments and docstrings. It
returned 70%, 71% and 75% for the three projects — three numbers so alike
that they measure vocabulary overlap, not documentation. **It is not
published as a result**, because it looks like an answer and is not one. The
one figure of this kind that is real came from reading the code by hand on a
private repository, and it is not comparable to a proxy.

So: TraceLink links this codebase as well as the others. Whether it *helps*
here less than elsewhere is unmeasured, and saying so is the honest end of
this benchmark.
