"""Interactive chat session with persistent history via kiro-cli.

Injects previous session context into kiro-cli so conversation
continuity is maintained across sessions.
"""

import os
import re
import sys
import tempfile
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
    print(f"│  {len(msgs)} previous messages")
    print("╰─ Launching kiro-cli with session context...")
    print()

    # Build a clean context prompt from history
    context_msg = _build_context_prompt(session.title, msgs)

    # Launch kiro-cli with context as first message
    kiro_args = ["kiro-cli", "chat"]
    if context_msg:
        kiro_args.append(context_msg)

    # Register cleanup
    _save_session_on_exit(store, session_id)

    os.execvp("kiro-cli", kiro_args)


def _build_context_prompt(title: str, msgs: List[ChatMessage]) -> Optional[str]:
    """Build a concise context prompt from previous messages."""
    from dcam.agent_instructions import AGENT_INSTRUCTIONS

    parts = [AGENT_INSTRUCTIONS, "\n---\n"]

    if msgs:
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
            recent = clean_msgs[-30:]
            parts.append(
                f"IMPORTANT CONTEXT: This is a continuation of a previous session titled \"{title}\". "
                f"Below is the conversation history. Continue from where we left off. "
                f"Do NOT say you don't have previous context.\n\n"
                + "\n\n".join(recent)
                + "\n\nAcknowledge the context briefly and ask what to do next."
            )

    return "\n".join(parts)


def _save_session_on_exit(store: DeltaStore, session_id: str):
    import atexit

    def _cleanup():
        try:
            sessions = store.read_sessions()
            for s in sessions:
                if s.session_id == session_id:
                    s.message_count = len(store.read_messages(session_id))
            store.write_sessions(sessions)
        except Exception:
            pass

    atexit.register(_cleanup)
