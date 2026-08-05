#!/bin/sh
# PostToolUse (Edit|Write|MultiEdit): mark the project's tracelink vault stale.
# Hooks receive a JSON payload on stdin — drain it so the writer never blocks.
# Always exit 0: a hook that fails blocks Claude, and no marker is worth that.
cat > /dev/null
python3 "$(dirname "$0")/../scripts/plugin_refresh.py" mark || true
exit 0
