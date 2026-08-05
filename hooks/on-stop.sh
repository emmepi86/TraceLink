#!/bin/sh
# Stop (end of turn): if the stale marker exists, rebuild the symbol index and
# relink the vault, then remove the marker. Then — if the project opted in via
# .tracelink/config.json — check whether this session's edits were ever
# distilled into the findings register, and block the stop ONCE with
# instructions when they were not. All policy lives in the Python.
# The JSON payload on stdin flows through to the script, which reads it in
# full, so the writer never blocks.
# Always exit 0: a hook that fails blocks Claude by accident — the only
# legitimate block is the capture JSON on stdout, which requires exit 0.
python3 "$(dirname "$0")/../scripts/plugin_refresh.py" capture-check || true
exit 0
