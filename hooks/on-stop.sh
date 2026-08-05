#!/bin/sh
# Stop (end of turn): if the stale marker exists, rebuild the symbol index and
# relink the vault, then remove the marker. All policy lives in the Python.
# Hooks receive a JSON payload on stdin — drain it so the writer never blocks.
# Always exit 0: a hook that fails blocks Claude, and no refresh is worth that.
cat > /dev/null
python3 "$(dirname "$0")/../scripts/plugin_refresh.py" refresh || true
exit 0
