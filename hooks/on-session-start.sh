#!/bin/sh
# SessionStart (startup|clear): a new session begins — drop the previous
# session's capture markers, so its spent prompt and stale baseline cannot
# silence or misread this one. resume/compact never reach here: they are the
# same logical session, matched out in hooks.json.
# Drain stdin so the writer never blocks; always exit 0.
cat > /dev/null
python3 "$(dirname "$0")/../scripts/plugin_refresh.py" session-clear || true
exit 0
