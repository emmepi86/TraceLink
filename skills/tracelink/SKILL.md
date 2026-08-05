---
name: tracelink
description: Use when engineering findings, bug notes or an audit register need to be turned into a navigable vault and connected to the code they describe — or when asked "what has been written about this function".
---

# tracelink

Turn an append-only findings register into one note per finding, and connect
every note to the code it names — file and line, both directions.

## When this applies

- a findings/audit/bug register has grown past the point where a single finding
  can be read in one place
- someone asks which notes concern a given function, class or table
- a repository is being explored and prior findings should surface alongside it

## Run it

Three steps. The first two are independent of any editor; the vault happens to
be Obsidian-compatible because that costs nothing.

Paths must be absolute. When the plugin is invoked the working directory is the
user's project, not the plugin — relative `scripts/...` only works from the
TraceLink checkout.

Installed (`pipx install tracelink`):

```bash
tracelink index --repo "${CLAUDE_PROJECT_DIR}" --out "${CLAUDE_PROJECT_DIR}/.tracelink/symbols.json"
tracelink split --register "${CLAUDE_PROJECT_DIR}/FINDINGS.md" --out "${CLAUDE_PROJECT_DIR}/.tracelink/vault" --prefix RES
tracelink link  --vault "${CLAUDE_PROJECT_DIR}/.tracelink/vault" --symbols "${CLAUDE_PROJECT_DIR}/.tracelink/symbols.json" --repo "${CLAUDE_PROJECT_DIR}"
```

From a checkout, with nothing installed:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/symbols.py" \
  --repo "${CLAUDE_PROJECT_DIR}" \
  --out  "${CLAUDE_PROJECT_DIR}/.tracelink/symbols.json"

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/split.py" \
  --register "${CLAUDE_PROJECT_DIR}/FINDINGS.md" \
  --out      "${CLAUDE_PROJECT_DIR}/.tracelink/vault" --prefix RES

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/link.py" \
  --vault   "${CLAUDE_PROJECT_DIR}/.tracelink/vault" \
  --symbols "${CLAUDE_PROJECT_DIR}/.tracelink/symbols.json"
```

`--check` writes nothing and exits 1 when anything is stale — use it in CI.
`--explain` prints why each link was made. Linking is incremental: unchanged
notes are skipped (their blocks stay byte-verified); `--full` forces a
complete pass.

Outputs: `<VAULT>/<ID>.md` per finding, `INDEX.md` (status and severity table,
open+high listed separately), `CODE-INDEX.md` (symbol -> notes, plus a
Hotspots section: symbols with ≥2 notes and per-file rollups — check it first
when asked "what is known about this code").

Two more commands:

```bash
# one-shot health of register, vault, index and links (--strict = exit 1 for CI)
tracelink status --register "${CLAUDE_PROJECT_DIR}/FINDINGS.md" \
  --vault "${CLAUDE_PROJECT_DIR}/.tracelink/vault" \
  --symbols "${CLAUDE_PROJECT_DIR}/.tracelink/symbols.json"

# refresh index+links on every git commit (marker-delimited, never blocks)
tracelink hook install && tracelink hook status
```

When this plugin is installed the vault also refreshes itself inside a
session: edits mark it stale, the end of the turn re-runs index+link once
(only in projects where `.tracelink/` exists).

## Things that will bite

- **Re-run steps 1 and 3 after code changes** — or let it happen for you:
  `tracelink hook install` (git) and the plugin's auto-refresh (in-session)
  both exist for exactly this. A stale symbol map points notes at lines that
  have moved, with no sign that anything is wrong.
- **Status comes from each finding's own headings, never its body.** A note
  saying "this already caused a withdrawn finding (X-16)" is not itself
  withdrawn. `split.py` enforces this; if you reimplement it, keep it.
- **Linking is lexical.** Notes that name their symbols get linked; prose that
  talks around them does not. Encourage naming symbols in findings.
- **Cap the links.** Generic names (`data`, `status`, `record`) match modules and
  tables everywhere. `--max-links` and `--min-len` exist for this; names inside
  backticks bypass the length filter, since a code span is deliberate.
- If graphify is the backend, run `graphify update <REPO>` first, from the
  directory that holds `graphify-out/`.

## Why the backend is pluggable

The linker needs one fact — where a name lives. graphify gives the richest
answer, ctags the most portable, the built-in scan the most available. Coupling
to a single upstream schema is how a small tool breaks when someone else's
project changes shape.
