# Contributing

Small tool, small scope. The most useful contribution is a **new symbol
backend** — everything else is deliberately thin.

Apache-2.0 already covers contribution terms, so there is no CLA to sign.

## Adding a symbol backend

A backend answers exactly one question: **where does this name live?** It returns
`{identifier: "path/to/file.ext:L123"}` and nothing else. That narrowness is the
point — it is what lets the tool survive when an upstream project changes shape.

In `scripts/symbols.py`:

```python
def from_yourthing(repo: str) -> Tuple[Dict[str, str], Optional[str]]:
    """One line on what it reads and what it needs installed."""
    ...
    return symbols, None          # or {}, "why it could not run"

BACKENDS = {..., "yourthing": from_yourthing}
```

Three rules:

1. **Return a reason, never raise.** A missing tool or an unreadable file is a
   normal outcome; `auto` mode moves on to the next backend and prints the note.
2. **Read every upstream field with `.get`.** The graphify backend is written
   this way on purpose: a young project is allowed to change its mind, and this
   should degrade rather than crash.
3. **Add it to the `auto` order in `build()`** only if it is more precise than
   the one below it. Precision descends: graphify, ctags, scan.

## Language coverage in the built-in scan

`_DEF_PATTERNS` in `symbols.py` is one shallow regex per language family. It
answers "where is this defined", not "parse this correctly" — anything needing
more should use ctags. Adding a language is one tuple; keep the pattern anchored
at line start and prefer missing a definition over matching a call.

## Testing

There is no framework. Run the three scripts against `examples/` and check the
output:

```bash
python3 scripts/split.py --register examples/FINDINGS.example.md --out /tmp/tl --prefix RES
python3 scripts/symbols.py --repo scripts --backend scan --out /tmp/tl.json
python3 scripts/link.py --vault /tmp/tl --symbols /tmp/tl.json
```

`examples/FINDINGS.example.md` exists to pin one specific behaviour: **RES-02
must come out `open`** even though its body mentions RES-01 being closed. If a
change makes it `closed`, the status classifier has started reading bodies
instead of headings, and that bug produces an index that is confidently wrong.

If you add behaviour, add a case to the example that would fail without it.

## What is out of scope

Semantic linking, finding prioritisation, and anything that reads code meaning.
TraceLink joins notes to symbols; understanding is left to the tools that do it
well, and to the person reading.
