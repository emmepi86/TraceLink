# Roadmap

> **TraceLink is becoming the long-term memory of AI-assisted coding:
> file-based, tool-agnostic, verifiable.**

AI coding sessions are amnesic. Every session rediscovers the same
constraints, repeats the same mistakes, breaks invariants nobody wrote down —
and the codebase grows faster than anyone's understanding of it. The scarce
resource is no longer writing code; it is *persistent, verifiable
understanding of code*.

TraceLink already has the right container: findings anchored to `file:line`,
re-linked as code moves, with staleness declared instead of guessed. The
roadmap closes the loop around it.

## 0.6.x — the rails *(shipped)*

The friction killers: nothing here requires a human to remember anything.

- **Ship as `tracelink-vault`** on PyPI (the bare name belongs to an
  unrelated project); trusted publishing, version single-sourced.
- **Dotted-name resolution** — `payments.validate` links via qualified name
  or path suffix; `self.`/`cls.`/`this.` treated as syntax; conflicting
  evidence reported, never resolved silently.
- **Hotspots** in `CODE-INDEX.md` — symbols with ≥2 notes and per-file
  rollups surface the fragile places by themselves.
- **Incremental linking** — unchanged notes skip the whole pipeline
  (~4–5× faster on unchanged vaults), with a byte-identical guarantee and
  automatic self-repair of tampered blocks.
- **`tracelink status`** — one-shot health: register↔vault sync, index
  freshness, unlinked notes by cause, open+high findings.
- **`tracelink hook`** — a git post-commit hook that refreshes index and
  links, so they never go stale.
- **Claude Code auto-refresh** — edits mark the vault stale, end-of-turn
  refreshes it. Zero cost during edits.

## 0.7.0 — the memory loop *(shipped)*

The wow: agents *write* the register and the codebase *warns* the next agent.

- **Consult on touch** — before an agent edits a file that has findings,
  the relevant notes are injected as context: *"3 findings about
  `validate()`: …"*. The killer moment: an agent about to reintroduce a
  bug that was fixed twice before gets stopped by the note.
- **Capture on close** — at session end, if code changed and nothing was
  recorded, the agent is prompted once to distil durable discoveries into
  the register (explicit symbols, status, severity — the linkable-findings
  contract). The vault becomes the codebase's accumulated understanding,
  written by the agents themselves, at zero human cost.
- **Quality gates** — auto-capture is only as good as its filter. Findings
  that name no symbols are rejected at the door (the lexical contract as a
  quality gate), duplicates are folded, and the register stays lean. A thin
  vault that is true beats a fat one that is noise.
- **Opt-in by config** — both loops off by default; `.tracelink/config.json`
  turns them on per project.

## 0.8.0 — the open convention

- **`.tracelink/` as a tool-agnostic convention** — markdown + JSON in the
  repo, no API, no platform. Anything that can read files can read the
  memory: Claude Code, Cursor, Codex, CI, humans.
- **Cross-repo memory** — one vault indexing several repositories
  (services, monorepo splits), because knowledge does not stop at repo
  boundaries.
- **Report pack** — `status`/hotspots as a single shareable artefact: the
  onboarding file for humans and agents alike.

## Non-goals

- No semantic code analysis — graphify and ctags do the heavy lifting.
- No issue tracker — findings are *know this*, issues are *do this*.
- No network, no accounts, no telemetry. Files in, files out.
