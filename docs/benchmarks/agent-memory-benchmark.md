# Does persistent memory make coding agents better? We measured it.

*August 2026 · 38 agent runs · 3 real tasks on a production codebase ·
frozen criteria · blind judging*

TraceLink's claim is that a file-based, verifiable memory — findings anchored
to `file:line`, injected when an agent touches the code they describe — makes
AI-assisted coding cheaper and safer. Claims are cheap. This is the
measurement.

## Setup

**Target**: a production Next.js 16 / TypeScript real-estate platform
(~330 source files, live traffic), plus its realtime WebSocket sidecar and
Docker/nginx deployment. The register held 10 real findings distilled from
the project's actual engineering history — cache gotchas, timer constraints,
deploy traps — written in the linkable-findings contract.

**Tasks** (each with a hidden trap that the register knows about):

| | task given to the agent | the knowledge that matters |
|---|---|---|
| T1 | "Map what governs session timing, then change the session to 8 minutes" | the CMS config panel is a decoy: the sidecar hard-codes the active timers and only consumes the kill-switch flag |
| T2 | "The kiosk hero photos are heavy — optimise them, keep perceived quality on a 1080p/4K TV viewed up close" | serving the 1280px variant was a past bug (upscaled, soft); the fix deliberately moved to full-res |
| T3 | "Staging shows stale data; `docker restart` didn't help. Diagnose and give the exact remedy" | `unstable_cache` persists in the container's writable layer; only recreating the container clears it |

**Arms**: identical prompts; the memory arm additionally receives exactly
what TraceLink's consult hook injects (2–4 lines per relevant file: finding
id, status, severity, title, symbols with lines) plus the path to the full
notes. Each agent works in an isolated git worktree; the no-memory arm's
tree contains no register, no vault. Metrics come from the harness
(tokens, wall-clock, tool calls). Pass/fail criteria were frozen before any
run. All 26 extension-run reports were re-judged **blind** by an
independent judge agent that verified every factual claim against the
source; it agreed with the inline verdicts on 25 of 26 arms (the one
divergence: the judge was more lenient).

## Result 1 — memory closes the capability gap between models

The headline. Same tasks, three model tiers, with and without memory
(correctness per the blind judge):

| T1 + T3 | without memory | with memory |
|---|---|---|
| **Opus** | T1 3/3 · T3 pass (hedged in 2 of 3 reps) | T1 3/3 · T3 full pass |
| **Sonnet** | T1 3/3 · **T3 FAIL** — called the cache "in-memory", prescribed the restart the prompt said had already failed | T1 3/3 · T3 pass — **the most efficient run of the entire benchmark (31.9k tokens, 81s)** |
| **Haiku** | **T1 1/3** — inverted the config hierarchy · **T3 FAIL** — hallucinated a CLI command (`npx next revalidateTag`) | T1 3/3 · T3 pass, citing the note, exact remedy |

Token cost tells the other half: without memory, cost stays flat
(~105–123k) while correctness collapses down the tiers. With memory, cost
*falls* down the tiers — Opus 108.7k → Sonnet 83.0k → Haiku 75.3k — and
correctness stays perfect.

> **Sonnet with memory beat Opus without memory: 32% fewer tokens and
> better correctness.** Haiku with memory was the cheapest correct
> configuration in the whole study.

The failures of the cheaper models are not slowness. They are operational
hallucinations — commands that do not exist, mechanisms invented, config
hierarchies inverted — exactly the kind of confident wrongness a
vibe-coding workflow would execute without blinking.

## Result 2 — memory steers diagnosis (reproduced, not anecdotal)

On the ops task, no-memory arms drifted systematically toward a plausible
but speculative primary cause (an unrelated pipeline gap) in 3 separate
runs. Memory arms stayed anchored to the recorded incident in **every**
repetition, with the exact commands. And they used the vault critically,
not as an oracle: one arm explicitly dismissed an injected note as a red
herring for this task; another *composed* two notes — the cache remedy from
one, a `--no-deps` guard from another — into a single better command.

## Result 3 — the comment-quality experiment (2×2)

Is the memory just substituting for good code comments? We stripped every
comment from the areas the tasks explore (270 files, committed as an
orphan history so `git diff` archaeology was impossible) and re-ran both
arms.

| mean tokens, 3 tasks | without memory | with memory | memory saves |
|---|---|---|---|
| well-commented repo (N=3) | 184.5k | 169.0k | **8.4%** (range 2–15%) |
| comments stripped (N=2) | 191.3k | 144.4k | **24.5%** |

On a well-commented repo with a frontier model, the efficiency gain is
modest and noisy — run-to-run variance dominates, which is why we ran
repetitions. Strip the comments and the benefit **triples**. The single
most expensive run of the whole study (94.8k tokens) was the bare cell: no
comments, no memory, ops knowledge — nothing to read, nothing to recall,
everything to excavate. Note the asymmetry: losing the comments costs the
no-memory arm ~15% of extra tokens; the memory arm barely notices (+3%
in the first pass). Memory is insurance against undocumented code — and
vibe-coded repositories are undocumented by construction.

Quality degrades before correctness does: without the comment that
recorded *why* the 1280px variant was rejected ("looked soft on TV"), both
stripped arms of one repetition rationalised a 2× upscale on 4K panels as
acceptable — judged PARTIAL. The removed knowledge didn't flip a binary;
it degraded the decision on exactly the nuance it encoded.

## Result 4 — the index scales

The scan backend on real OSS repositories (zero dependencies, zero
network, cold run):

| repo | files | symbols | definitions | time | RSS |
|---|---|---|---|---|---|
| fastapi | 1,140 | 3,034 | 5,719 | 1.2s | 21MB |
| excalidraw | 660 | 2,641 | 2,807 | 1.2s | 26MB |
| payload (monorepo) | 6,858 | 11,722 | 19,840 | 4.2s | 30MB |

## What the benchmark gave back

Running 38 fresh agents against real tasks produced new knowledge for the
register itself: a bandwidth bug found independently by three separate
arms (every slideshow cover mounted eagerly — tens of MB per kiosk boot),
a token-expiry reconnection gap, and three ready implementations of the
photo fix. That is the capture loop, demonstrated: fresh eyes on real
tasks → durable findings.

## Limits, stated plainly

- N=3 (commented) / N=2 (stripped) per cell: ranges, not statistical
  significance. Directionality is consistent across 9 metrics and all
  repetitions; individual numbers wobble.
- One target repository, and a well-commented one — which makes these
  numbers a *conservative floor* for the codebases the tool targets.
- The T2 trap was defused in all commented arms by task wording plus first
  principles; binary correctness effects only appeared on cheaper models
  and stripped trees.
- Memory-arm injections were pre-composed to be byte-faithful to the real
  hook output rather than fired live.
- One early stripped cell could reach the stripped comments via
  `git diff` (worktrees share history); detected in transcripts, isolated
  to a single arm, and eliminated in later waves by committing the strip
  as an orphan root. One memory cell ran with the injection summary but a
  missing full-notes vault (setup error) — disclosed, and informative: the
  summary alone was weaker than summary+notes.
- The blind judge covered the 26 extension runs; the 12 original runs
  kept the controller's verdicts (25/26 concordance on the overlap).

## Reproduce it

Everything the harness needs is in this repository: `tracelink index`,
`split`, `link`, the consult hook in `hooks/`, and the linkable-findings
contract in the README. Write a register with your project's real
gotchas, give half your agents the consult output, and count tokens. We
would genuinely like to see numbers from other codebases — especially
uncommented ones.
