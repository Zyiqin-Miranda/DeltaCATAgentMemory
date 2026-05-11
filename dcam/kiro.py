"""Kiro hook integration — installs hooks and agent instructions."""

import json
from pathlib import Path

from dcam.agent_instructions import AGENT_INSTRUCTIONS

HOOK_SCRIPT = '''#!/bin/bash
# DCAM pre-tool-use hook — auto-indexes files before operations
PAYLOAD=$(cat)
TOOL_NAME=$(echo "$PAYLOAD" | jq -r '.tool_name // empty')

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
    "mcpServers": {
        "dcam": {
            "command": "dcam",
            "args": ["serve"],
            "type": "stdio",
        }
    },
    "tools": ["@builtin", "@dcam"],
    "allowedTools": [
        "@dcam/*",
        "fs_read",
        "fs_write",
        "execute_bash",
        "grep",
        "glob",
        "@*",
    ],
    "resources": ["file://AGENTS.md"],
    "hooks": {
        "preToolUse": [
            {"command": "./hooks/dcam-pre-tool.sh"}
        ]
    },
}


def install_hooks(project_root: str = "."):
    """Install DCAM hooks, agent instructions, and config into a Kiro project."""
    root = Path(project_root)

    # Install hook script
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "dcam-pre-tool.sh"
    hook_path.write_text(HOOK_SCRIPT)
    hook_path.chmod(0o755)

    # Install agent instructions as AGENTS.md (kiro reads this automatically)
    agents_md = root / "AGENTS.md"
    existing = agents_md.read_text() if agents_md.exists() else ""
    marker = "## DCAM Compact Context Protocol"
    if marker not in existing:
        with open(agents_md, "a") as f:
            f.write("\n\n" + AGENT_INSTRUCTIONS)

    # Install Kiro agent config
    kiro_dir = root / ".kiro" / "agents"
    kiro_dir.mkdir(parents=True, exist_ok=True)
    config_path = kiro_dir / "dcam.json"
    config_path.write_text(json.dumps(KIRO_AGENT_CONFIG, indent=2))

    return str(hook_path), str(config_path)
