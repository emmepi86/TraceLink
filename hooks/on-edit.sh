#!/bin/sh
# PostToolUse (Edit|Write|MultiEdit): mark the project's tracelink vault
# stale, then — if the project opted in via .tracelink/config.json — let the
# vault speak about the edited file (additionalContext on stdout).
# The JSON payload on stdin flows through to the script, which reads it in
# full, so the writer never blocks.
# Always exit 0: a hook that fails blocks Claude, and no marker is worth that.
python3 "$(dirname "$0")/../scripts/plugin_refresh.py" consult || true
exit 0
