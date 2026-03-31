"""Kiro hook integration — installs hooks for auto context injection."""

import json
from pathlib import Path
from typing import Optional

HOOK_SCRIPT = '''#!/bin/bash
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
'''

KIRO_AGENT_CONFIG = {
    "name": "dcam",
    "description": "DeltaCAT Agent Memory — persistent memory across sessions",
    "hooks": {
        "pre-tool-use": "hooks/dcam-pre-tool.sh"
    },
    "memory": {
        "provider": "deltacat",
        "auto_compact": True,
        "auto_inject": True,
    }
}


def install_hooks(project_root: str = "."):
    """Install DCAM hooks into a Kiro project."""
    root = Path(project_root)

    # Install hook script
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "dcam-pre-tool.sh"
    hook_path.write_text(HOOK_SCRIPT)
    hook_path.chmod(0o755)

    # Install Kiro agent config
    kiro_dir = root / ".kiro" / "agents"
    kiro_dir.mkdir(parents=True, exist_ok=True)
    config_path = kiro_dir / "dcam.json"
    config_path.write_text(json.dumps(KIRO_AGENT_CONFIG, indent=2))

    return str(hook_path), str(config_path)
