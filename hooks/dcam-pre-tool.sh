#!/bin/bash
# DCAM pre-tool-use hook — auto-injects compact context
PAYLOAD=$(cat)
TOOL_NAME=$(echo "$PAYLOAD" | jq -r '.tool_name // empty')

# Auto-resolve context for file operations
case "$TOOL_NAME" in
    Read|Write|Edit|str_replace|create)
        FILE_PATH=$(echo "$PAYLOAD" | jq -r '.tool_input.file_path // .tool_input.path // empty')
        if [ -n "$FILE_PATH" ]; then
            dcam compact "$FILE_PATH" 2>/dev/null
        fi
        ;;
esac

exit 0
