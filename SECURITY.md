# Security

## What these scripts touch

TraceLink is three local Python scripts with no third-party dependencies.

| script | reads | writes | network |
|---|---|---|---|
| `symbols.py` | source files under `--repo`; `graphify-out/graph.json` or `tags` if present | `--out` (a JSON symbol map) | none |
| `split.py` | `--register` (read-only) | `--out` directory: one note per finding, plus `INDEX.md` | none |
| `link.py` | notes in `--vault`; the symbol map | the notes in `--vault`, plus `CODE-INDEX.md` | none |

**No network calls, no telemetry, no credentials, no subprocess execution.** The
only outbound behaviour in this repository is `git`, when you push it yourself.

## The one thing to know before running it

`link.py` **edits the notes in `--vault` in place**, inserting or replacing a
`## Linked code` block. Everything else in each note is left untouched, and
re-runs replace the previous block rather than stacking, so it is idempotent.

Your original register is never modified by any script. Point `--out` at a fresh
directory the first time and inspect the result before wiring it into anything.

## If you use the graphify backend

`symbols.py` only reads `graphify-out/graph.json` — it never invokes graphify.
How that file was produced is your decision, and it matters:
[graphify](https://github.com/Graphify-Labs/graphify) parses code locally with
tree-sitter and calls no model, but its semantic pass over documents, PDFs and
media can call an external backend.

**If your repository holds confidential, personal or regulated material, do not
run that pass over it**, or point it at a local model. Exclude the directories
holding those documents rather than relying on a flag. TraceLink itself sends
nothing anywhere regardless.

## Reporting

Open an issue at
[github.com/emmepi86/TraceLink/issues](https://github.com/emmepi86/TraceLink/issues).

If a finding involves data exposure and you would rather not open it publicly,
write to the address on [aosol.cloud](https://aosol.cloud) and mark it as a
security report.

There are no version branches: fixes land on `main`.
