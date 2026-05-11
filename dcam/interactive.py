"""Interactive chat session with persistent history via kiro-cli.

Runs kiro-cli as a subprocess (not execvp) so dcam stays alive
and can capture the conversation after kiro exits.
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

from dcam.models import ChatMessage, MessageRole
from dcam.store import DeltaStore

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)


def run_interactive(store: DeltaStore, session_id: str):
    """Enter an interactive chat session backed by kiro-cli with persistent context."""
    sessions = store.read_sessions()
    session = next((s for s in sessions if s.session_id == session_id), None)
    if not session:
        print(f"Session {session_id} not found.", file=sys.stderr)
        sys.exit(1)
    if session.ended_at:
        print(f"Session {session_id} is already ended.", file=sys.stderr)
        sys.exit(1)

    msgs = store.read_messages(session_id)

    print(f"╭─ {session.title} ({session_id})")
    print(f"│  {len(msgs)} previous messages in dcam")
    print("╰─ Launching kiro-cli with session context...")
    print()

    # Build context prompt from project memory + history
    context_msg = _build_context_prompt(session.title, msgs)

    # Run kiro-cli as subprocess (NOT execvp) so we regain control after
    kiro_args = ["kiro-cli", "chat"]
    if context_msg:
        kiro_args.append(context_msg)

    try:
        # Run kiro interactively — user sees full kiro UI
        subprocess.run(kiro_args)
    except KeyboardInterrupt:
        pass

    # After kiro exits, try to sync kiro's session back to dcam
    print("\nKiro session ended. Syncing messages to dcam...")
    _sync_kiro_session(store, session_id)

    # Update session message count
    sessions = store.read_sessions()
    for s in sessions:
        if s.session_id == session_id:
            s.message_count = len(store.read_messages(session_id))
    store.write_sessions(sessions)

    total = len(store.read_messages(session_id))
    print(f"dcam now has {total} messages for this session.")


def _sync_kiro_session(store: DeltaStore, session_id: str):
    """Try to sync the latest kiro-cli session messages back to dcam.
    
    Kiro stores sessions locally. We find the most recent one and
    extract any new messages that dcam doesn't have yet.
    """
    try:
        # Get kiro's most recent session transcript
        result = subprocess.run(
            ["kiro-cli", "chat", "--list-sessions"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return

        # Parse the most recent session ID from kiro's output
        clean = _strip_ansi(result.stdout)
        lines = clean.strip().splitlines()
        kiro_session_id = None
        for line in lines:
            if "Chat SessionId:" in line:
                kiro_session_id = line.split("Chat SessionId:")[-1].strip()
                break  # Most recent is first

        if not kiro_session_id:
            return

        # Resume that session with a "dump history" request
        dump_result = subprocess.run(
            ["kiro-cli", "chat", "--no-interactive",
             "--resume",
             "Output ONLY the last 50 messages from our conversation as a numbered list. Format: 'N. [role] message'. No other text."],
            capture_output=True, text=True, timeout=30,
        )

        if dump_result.returncode != 0 or not dump_result.stdout.strip():
            return

        # Parse and store new messages
        existing_contents = {m.content[:100] for m in store.read_messages(session_id)}
        new_count = 0
        
        for line in _strip_ansi(dump_result.stdout).splitlines():
            line = line.strip()
            if not line:
                continue
            
            # Try to parse "N. [role] message" format
            role = MessageRole.ASSISTANT
            content = line
            
            if "[user]" in line.lower():
                role = MessageRole.USER
                content = line.split("]", 1)[-1].strip() if "]" in line else line
            elif "[assistant]" in line.lower():
                role = MessageRole.ASSISTANT
                content = line.split("]", 1)[-1].strip() if "]" in line else line

            # Skip if we already have this message
            if content[:100] in existing_contents:
                continue
            if len(content) < 3:
                continue

            store.append_message(ChatMessage(
                session_id=session_id,
                role=role,
                content=content,
                timestamp=datetime.now(),
            ))
            new_count += 1

        if new_count:
            print(f"  Synced {new_count} new messages from kiro.")
        else:
            print("  No new messages to sync.")

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"  Could not sync kiro session: {e}")


def _build_context_prompt(title: str, msgs: List[ChatMessage]) -> Optional[str]:
    """Build a concise context prompt from previous messages."""
    from dcam.agent_instructions import AGENT_INSTRUCTIONS

    parts = [AGENT_INSTRUCTIONS, "\n---\n"]

    if msgs:
        # Inject project memories first
        try:
            from dcam.store import DeltaStore
            s = DeltaStore()
            project_ctx = s.get_session_context()
            if project_ctx:
                parts.append(project_ctx)
                parts.append("---\n")
        except Exception:
            pass

        clean_msgs = []
        for m in msgs:
            content = _strip_ansi(m.content).strip()
            if not content:
                continue
            if len(content) > 300:
                content = content[:300] + "..."
            role = "User" if m.role == MessageRole.USER else "Assistant"
            clean_msgs.append(f"{role}: {content}")

        if clean_msgs:
            recent = clean_msgs[-1000:]
            parts.append(
                f"IMPORTANT CONTEXT: This is a continuation of a previous session titled \"{title}\". "
                f"Below is the conversation history. Continue from where we left off. "
                f"Do NOT say you don't have previous context.\n\n"
                + "\n\n".join(recent)
                + "\n\nAcknowledge the context briefly and ask what to do next."
            )

    return "\n".join(parts)
