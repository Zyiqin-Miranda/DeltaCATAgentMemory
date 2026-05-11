#!/bin/bash
# DCAM Claude Code SessionEnd hook
# Syncs the current session's conversation to DeltaCAT storage
#
# Receives JSON on stdin from Claude Code with:
#   session_id, transcript_path, cwd, hook_event_name

set -e

DCAM_NAMESPACE="${DCAM_NAMESPACE:-dcam}"
DCAM_CATALOG="${DCAM_CATALOG:-local}"

# Read hook input from stdin
hook_input=$(cat)

session_id=$(echo "$hook_input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
transcript_path=$(echo "$hook_input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('transcript_path',''))" 2>/dev/null || echo "")
cwd=$(echo "$hook_input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "$(pwd)")

if command -v dcam &> /dev/null; then
    if [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
        dcam claude sync --namespace "$DCAM_NAMESPACE" --catalog "$DCAM_CATALOG" --session-file "$transcript_path" 2>/dev/null || true
    else
        dcam claude sync --namespace "$DCAM_NAMESPACE" --catalog "$DCAM_CATALOG" --project "$cwd" 2>/dev/null || true
    fi
fi

echo '{"continue": true}'
exit 0
