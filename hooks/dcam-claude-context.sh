#!/bin/bash
# DCAM Claude Code context injection hook
# Runs on prompt submit to inject previous session context
#
# This outputs context from recent sessions that gets appended
# to the user's prompt as additional context.

DCAM_NAMESPACE="${DCAM_NAMESPACE:-dcam}"
DCAM_CATALOG="${DCAM_CATALOG:-local}"

if command -v dcam &> /dev/null; then
    dcam claude context --namespace "$DCAM_NAMESPACE" --catalog "$DCAM_CATALOG" --no-sync --sessions 2 --messages 10 2>/dev/null || true
fi
