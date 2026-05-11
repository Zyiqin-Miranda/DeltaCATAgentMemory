"""Claude Code integration — sync Claude Code sessions to DeltaCAT storage.

Parses Claude Code's JSONL session files (~/.claude/projects/.../*.jsonl)
and syncs them as ChatMessage/ChatSession objects into DCAM's DeltaCAT tables.

Also provides context loading for new sessions from previous history.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dcam.models import ChatMessage, ChatSession, MessageRole
from dcam.store import DeltaStore

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"


def find_project_dir(project_path: str) -> Optional[Path]:
    """Find the Claude Code project directory for a given workspace path."""
    encoded = project_path.replace("/", "-")
    if encoded.startswith("-"):
        pass
    project_dir = PROJECTS_DIR / encoded
    if project_dir.exists():
        return project_dir
    for d in PROJECTS_DIR.iterdir():
        if d.is_dir() and project_path.rstrip("/").split("/")[-1] in d.name:
            return d
    return None


def list_sessions(project_path: Optional[str] = None) -> List[Dict]:
    """List all Claude Code sessions, optionally filtered by project."""
    sessions = []
    if project_path:
        project_dir = find_project_dir(project_path)
        if not project_dir:
            return []
        dirs = [project_dir]
    else:
        if not PROJECTS_DIR.exists():
            return []
        dirs = [d for d in PROJECTS_DIR.iterdir() if d.is_dir()]

    for pdir in dirs:
        for jsonl in pdir.glob("*.jsonl"):
            if "subagents" in str(jsonl):
                continue
            session_id = jsonl.stem
            try:
                first_ts = None
                last_ts = None
                msg_count = 0
                with open(jsonl) as f:
                    for line in f:
                        obj = json.loads(line)
                        if obj.get("type") in ("user", "assistant"):
                            msg_count += 1
                            ts = obj.get("timestamp")
                            if ts:
                                if not first_ts:
                                    first_ts = ts
                                last_ts = ts
                sessions.append({
                    "session_id": session_id,
                    "project": pdir.name,
                    "path": str(jsonl),
                    "message_count": msg_count,
                    "started_at": first_ts,
                    "ended_at": last_ts,
                })
            except (json.JSONDecodeError, OSError):
                continue
    return sorted(sessions, key=lambda s: s.get("started_at") or "", reverse=True)


def _resolve_conversation_path(raw_messages: List[Dict]) -> List[Dict]:
    """Prune abandoned retry branches and return the active conversation.

    Claude Code JSONL files are append-only. When a user retries or edits a
    message, both the old and new branches remain. At each branch point (a
    parent with multiple children), the latest child represents the active path
    and older children are abandoned retries. This function marks dead branches
    and returns only active messages in timestamp order.
    """
    if not raw_messages:
        return []

    by_uuid = {m["uuid"]: m for m in raw_messages if m.get("uuid")}
    children_map: Dict[str, List[Dict]] = {}
    for m in raw_messages:
        parent = m.get("parentUuid")
        if parent and parent in by_uuid:
            children_map.setdefault(parent, []).append(m)

    # At each branch point, mark older children (and their descendants) as dead
    dead_uuids: set = set()

    def _mark_dead(uuid: str):
        dead_uuids.add(uuid)
        for child in children_map.get(uuid, []):
            _mark_dead(child["uuid"])

    for parent_uuid, kids in children_map.items():
        if len(kids) <= 1:
            continue
        sorted_kids = sorted(kids, key=lambda m: m.get("timestamp", ""))
        for dead_kid in sorted_kids[:-1]:
            _mark_dead(dead_kid["uuid"])

    active = [m for m in raw_messages if m.get("uuid") not in dead_uuids]
    return sorted(active, key=lambda m: m.get("timestamp", ""))


def _extract_tool_result_text(block: Dict) -> str:
    """Extract readable text from a tool_result content block."""
    result_content = block.get("content", "")
    if isinstance(result_content, list):
        parts = []
        for b in result_content:
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)
    return str(result_content) if result_content else ""


def _format_content_blocks(content: list) -> str:
    """Convert a list of content blocks into readable text.

    Handles text, tool_use, and tool_result blocks.
    """
    text_parts = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "").strip()
            if text:
                text_parts.append(text)
        elif btype == "tool_use":
            tool_name = block.get("name", "unknown")
            tool_input = block.get("input", {})
            if tool_name in ("Read", "Edit", "Write"):
                fp = tool_input.get("file_path", "")
                text_parts.append(f"[{tool_name}: {fp}]")
            elif tool_name == "Bash":
                cmd = tool_input.get("command", "")[:200]
                text_parts.append(f"[Bash: {cmd}]")
            elif tool_name == "Agent":
                desc = tool_input.get("description", "")
                text_parts.append(f"[Agent: {desc}]")
            else:
                text_parts.append(f"[{tool_name}]")
        elif btype == "tool_result":
            result_text = _extract_tool_result_text(block)
            if result_text:
                # Truncate very large tool outputs but keep enough for context
                if len(result_text) > 1000:
                    result_text = result_text[:1000] + "..."
                text_parts.append(result_text)
    return "\n".join(text_parts)


def parse_session(jsonl_path: str) -> Tuple[List[ChatMessage], Dict]:
    """Parse a Claude Code JSONL session file into ChatMessage objects.

    Resolves conversation branches (retries/edits) to follow the final path,
    and includes tool results for complete context.

    Returns (messages, metadata) where metadata includes session info.
    """
    metadata = {"session_id": Path(jsonl_path).stem}

    # Read all message objects
    raw_messages = []
    with open(jsonl_path) as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in ("user", "assistant") and not obj.get("isMeta"):
                raw_messages.append(obj)

    # Resolve to the main conversation path
    path = _resolve_conversation_path(raw_messages)

    messages = []
    msg_id = 0
    for obj in path:
        msg_type = obj.get("type")
        msg = obj.get("message", {})
        content = msg.get("content", "")

        if isinstance(content, list):
            content = _format_content_blocks(content)
        elif not isinstance(content, str):
            continue

        if not content or len(content.strip()) < 2:
            continue

        # Skip local command outputs, slash commands, and system tags
        if content.startswith("<local-command-caveat>"):
            continue
        if content.startswith("<local-command-stdout>"):
            continue
        if content.startswith("<command-name>"):
            continue

        timestamp_str = obj.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now()

        role = MessageRole.USER if msg_type == "user" else MessageRole.ASSISTANT
        msg_id += 1

        messages.append(ChatMessage(
            id=msg_id,
            session_id=metadata["session_id"],
            role=role,
            content=content,
            timestamp=timestamp,
            metadata=json.dumps({
                "source": "claude-code",
                "uuid": obj.get("uuid"),
                "version": obj.get("version"),
            }),
        ))

    return messages, metadata


def sync_session(store: DeltaStore, jsonl_path: str,
                 title: Optional[str] = None) -> Optional[ChatSession]:
    """Sync a Claude Code session to DCAM storage.

    Parses the JSONL file, deduplicates against existing messages using UUIDs,
    and writes new messages + session metadata.
    """
    messages, metadata = parse_session(jsonl_path)
    if not messages:
        return None

    session_id = metadata["session_id"]

    # UUID-based dedup: extract known UUIDs from existing stored messages
    existing_msgs = store.read_messages(session_id)
    existing_uuids = set()
    for m in existing_msgs:
        if m.metadata:
            try:
                meta = json.loads(m.metadata)
                if meta.get("uuid"):
                    existing_uuids.add(meta["uuid"])
            except (json.JSONDecodeError, TypeError):
                pass

    new_messages = []
    for msg in messages:
        msg_uuid = None
        if msg.metadata:
            try:
                meta = json.loads(msg.metadata)
                msg_uuid = meta.get("uuid")
            except (json.JSONDecodeError, TypeError):
                pass
        if msg_uuid and msg_uuid in existing_uuids:
            continue
        new_messages.append(msg)

    if not new_messages and existing_msgs:
        return None

    for msg in new_messages:
        msg.session_id = session_id
        store.append_message(msg)

    # Create or update session record
    sessions = store.read_sessions()
    existing_session = next((s for s in sessions if s.session_id == session_id), None)
    total_count = len(existing_msgs) + len(new_messages)

    if not existing_session:
        session = ChatSession(
            session_id=session_id,
            title=title or _infer_title(messages),
            started_at=messages[0].timestamp,
            ended_at=messages[-1].timestamp,
            message_count=total_count,
            summary=_generate_summary(messages),
        )
        sessions.append(session)
        store.write_sessions(sessions)
        return session
    else:
        existing_session.ended_at = messages[-1].timestamp
        existing_session.message_count = total_count
        store.write_sessions(sessions)
        return existing_session


def sync_all_sessions(store: DeltaStore,
                      project_path: Optional[str] = None) -> int:
    """Sync all Claude Code sessions for a project to DCAM storage."""
    sessions = list_sessions(project_path)
    synced = 0
    for s in sessions:
        result = sync_session(store, s["path"])
        if result:
            synced += 1
    return synced


def get_recent_context(store: DeltaStore, limit: int = 3,
                       max_messages_per_session: int = 20,
                       max_message_chars: int = 2000) -> str:
    """Build context string from recent Claude Code sessions for injection.

    Returns a markdown-formatted summary of recent sessions that can be
    injected into CLAUDE.md or system prompt.
    """
    def _sort_key(s):
        ts = s.started_at
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return ts

    sessions = sorted(store.read_sessions(), key=_sort_key, reverse=True)
    if not sessions:
        return ""

    lines = ["# Previous Session Context (from DeltaCAT)\n"]

    for session in sessions[:limit]:
        lines.append(f"## Session: {session.title or session.session_id}")
        lines.append(f"Date: {session.started_at.strftime('%Y-%m-%d %H:%M')}")
        if session.summary:
            lines.append(f"Summary: {session.summary}")
        lines.append("")

        msgs = store.read_messages(session.session_id)
        recent_msgs = msgs[-max_messages_per_session:]
        for msg in recent_msgs:
            role_prefix = "User" if msg.role == MessageRole.USER else "Assistant"
            content = msg.content
            if len(content) > max_message_chars:
                content = content[:max_message_chars] + "..."
            lines.append(f"**{role_prefix}**: {content}")
            lines.append("")

        lines.append("---\n")

    return "\n".join(lines)


def install_claude_code_hook(project_path: str, namespace: str = "dcam",
                             catalog: str = "local") -> Tuple[Optional[str], Optional[str]]:
    """Install DCAM integration into Claude Code settings.

    Creates:
    1. A SessionEnd hook in .claude/settings.local.json
    2. The hook shell script
    3. Updates CLAUDE.md with context-loading instructions
    """
    project_root = Path(project_path)
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(exist_ok=True)

    # Create the sync hook script
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "dcam-session-sync.sh"
    hook_script = f'''#!/bin/bash
# DCAM Claude Code SessionEnd hook
set -e
DCAM_NAMESPACE="{namespace}"
DCAM_CATALOG="{catalog}"

hook_input=$(cat)
cwd=$(echo "$hook_input" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null || echo "$(pwd)")

if command -v dcam &> /dev/null; then
    dcam claude sync --namespace "$DCAM_NAMESPACE" --catalog "$DCAM_CATALOG" --project "$cwd" 2>/dev/null || true
fi
echo '{{"continue": true}}'
exit 0
'''
    hook_path.write_text(hook_script)
    hook_path.chmod(0o755)

    # Create/update .claude/settings.local.json with SessionEnd hook
    settings_path = claude_dir / "settings.local.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            pass

    if "hooks" not in settings:
        settings["hooks"] = {}

    settings["hooks"]["SessionEnd"] = [{
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": str(hook_path),
            "timeout": 30,
        }],
    }]

    settings_path.write_text(json.dumps(settings, indent=2))

    # Create CLAUDE.md with context loading instructions
    claude_md = project_root / "CLAUDE.md"
    dcam_section = f"""
# DeltaCAT Agent Memory Integration

This project uses DCAM for persistent session memory across Claude Code sessions.
All conversations are automatically synced to DeltaCAT tables on session end.

At the START of each new session, load previous context by running:
```bash
dcam claude context --namespace {namespace} --catalog {catalog}
```

Commands available:
- `dcam claude context` — Load context from recent sessions
- `dcam claude search "query"` — Search across all session history
- `dcam claude list` — List all synced sessions
- `dcam claude recall <session-id>` — Replay a specific session
"""
    if claude_md.exists():
        existing = claude_md.read_text()
        if "DeltaCAT Agent Memory" not in existing:
            claude_md.write_text(existing + "\n" + dcam_section)
    else:
        claude_md.write_text(dcam_section)

    return str(hook_path), str(settings_path)


def _infer_title(messages: List[ChatMessage]) -> str:
    """Infer a session title from the first meaningful user message."""
    for msg in messages:
        if msg.role != MessageRole.USER:
            continue
        content = msg.content.strip()
        # Skip tool results, commands, and system messages
        if not content or content.startswith("<") or content.startswith("["):
            continue
        # Skip short tool outputs (e.g. "OK", "Done")
        if len(content) < 10:
            continue
        title = content[:60].strip()
        if len(content) > 60:
            title += "..."
        title = re.sub(r'<[^>]+>', '', title).strip()
        return title or "Claude Code Session"
    return "Claude Code Session"


def _generate_summary(messages: List[ChatMessage]) -> str:
    """Generate a brief summary from message content."""
    user_msgs = [m for m in messages if m.role == MessageRole.USER and not m.content.startswith("<")]
    if not user_msgs:
        return ""
    topics = []
    for msg in user_msgs[:5]:
        topic = msg.content[:100].strip()
        if topic:
            topics.append(topic)
    if topics:
        return f"Topics: {'; '.join(topics[:3])}"
    return ""
