# Limitations

Things TraceLink does not do, written down on purpose. Each of these was
found by measuring, considered, and left as it is — with the reason, so the
decision can be argued with instead of rediscovered.

## Low-information names may produce a conservative ambiguity

**Accepted, not fixed.**

A bare word in prose that happens to name several definitions — `db`, or
any short generic identifier — is reported as an ambiguity rather than
filtered as prose. It costs a line of noise in a report.

It cannot become a false link: since the evidence gate, a bare word in
prose can produce a candidate and an ambiguity, never a match. So what
remains is over-refusal, which is the direction this tool prefers to err in.

Measured twice, and the size depends on the codebase: 1 finding in 50 on a
private 500k-line codebase, **4 in 20** on Home Assistant Core, where 74 179
definitions make ordinary English words — `template`, `firmware`,
`callback`, `release` — collide with real symbol names. Still no false
claim; more noise.

The fix would be a stop-word list, and that is the reason there isn't one.
`db`, `app`, `user`, `session`, `config`, `client` are common words *and*
perfectly real symbols. A list that hides them is a list that varies by
language, framework and codebase, and it immediately needs exceptions —
because an author who writes `` `db` `` in backticks means the symbol, and
explicit evidence must keep winning. Complexity spent to suppress noise
that creates no false claim is complexity in the wrong place.

## A reference is not a relationship

**Known limitation.**

TraceLink establishes that a finding *refers* to a symbol or a file. It does
not establish what the sentence around the reference asserts. A finding
saying

> this has nothing to do with `app/core/send_gate.py`

names that path, and anchors to it. The anchor is formally correct and
contextually backwards.

Reading the negation means reading natural language — negation, exception,
comparison, hypothesis — and a deterministic resolver that starts guessing
at intent is a different kind of tool, with a different failure mode. The
honest shape of a future answer is *reference exists, relationship
unknown*, not a list of words like "not" and "unrelated".

## Freshness can be proved cheaply only sometimes

Git can prove a scope unchanged without re-reading it, but asking git costs
in proportion to the repository while hashing costs in proportion to the
indexed scope. On a repository with a large vendored subtree and a small
indexed scope, the cheap road is the expensive one, and TraceLink takes the
slow one. See [F2B-FRESHNESS-EVIDENCE.md](F2B-FRESHNESS-EVIDENCE.md).

## Backend artefacts can be stale in ways nothing detects

An index built from an external symbol graph is only as current as that
graph. TraceLink checks what the artefact records about its own provenance
and reports `unknown` when it records nothing — which is most of the time.
`unknown` is not `stale`, and it is not `fresh` either.

## What the benchmarks establish, and what they do not

The measurements in [`benchmarks/`](../benchmarks/) come from one private
codebase and synthetic repositories. They establish that on that codebase,
50 hand-written findings linked to the code their authors meant with no
false authoritative assertion. They establish nothing about a second
codebase, another language, or findings written by someone else.

An agent A/B experiment was **halted and never turned into a claim**: the
baseline agents inherited project memory from outside the repository, so
the control arm was not a control. That is written up rather than
suppressed, in `benchmarks/README.md`.

## Where TraceLink is not the answer

A pre-qualification of candidate "knowledge traps" on a well-maintained
codebase disqualified nine of fourteen **before any run**: the code already
documented those constraints at the point of use, often citing the incident
that produced them. That is where they belong.

TraceLink is for what a comment beside a function cannot carry: why
something must keep working this way, what has already failed, which
constraints live outside the repository, which non-local relationships must
be preserved. Where the code can carry the knowledge, let it.
