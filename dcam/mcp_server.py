"""DCAM MCP Server — exposes dcam tools via Model Context Protocol.

Start with: dcam serve
Configure in kiro: kiro-cli mcp add dcam -- dcam serve
"""

from datetime import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from dcam import compact as compact_mod
from dcam.models import ChatMessage, Memory, MemoryType, MessageRole
from dcam.store import DeltaStore

mcp = FastMCP("dcam", instructions=(
    "DCAM provides persistent memory, chat history, and code indexing. "
    "Use dcam_compact before reading large files — it returns summaries. "
    "Use dcam_fetch to get only the specific chunk you need to edit. "
    "Use dcam_lookup to find symbols across all indexed files."
))

_store = DeltaStore(namespace="dcam")


# --- Memory Tools ---

@mcp.tool()
def dcam_store_memory(content: str, type: str = "semantic",
                      topic: Optional[str] = None, category: Optional[str] = None) -> str:
    """Store a memory for long-term recall. Types: semantic, episodic, procedural, short_term, project.
    Use type='project' for facts that should persist across ALL sessions."""
    mems = _store.read_memories()
    m = Memory(
        id=_store._next_id("memories"),
        type=MemoryType(type),
        topic=topic,
        category=category,
        content=content,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    mems.append(m)
    _store.write_memories(mems)
    return f"Stored memory {m.id}: {content[:80]}"


@mcp.tool()
def dcam_project_memory() -> str:
    """Get all project memories that persist across sessions.
    These are automatically included in every new session's context."""
    ctx = _store.get_session_context()
    return ctx if ctx else "No project memories stored."


@mcp.tool()
def dcam_recall_memory(name: str) -> str:
    """Recall a named project memory by its name handle."""
    m = _store.recall_by_name(name)
    if m:
        return f"[{m.name}] {m.content}"
    return f"No memory named '{name}'"


@mcp.tool()
def dcam_search_memories(query: str, limit: int = 1000) -> str:
    """Search memories by keyword using BM25 ranking."""
    results = _store.search_memories(query, limit=limit)
    if not results:
        return f"No memories matching '{query}'"
    lines = [f"[{m.id}] [{m.type.value}] {m.content[:200]}" for m in results]
    return "\n".join(lines)


# --- Chat History Tools ---

@mcp.tool()
def dcam_recall(session_id: str, limit: int = 1000) -> str:
    """Recall messages from a past chat session."""
    msgs = _store.read_messages(session_id)
    if not msgs:
        return f"No messages for session {session_id}"
    msgs.sort(key=lambda m: m.timestamp)
    lines = [f"[{m.timestamp.strftime('%H:%M')}] {m.role.value}: {m.content[:300]}"
             for m in msgs[-limit:]]
    return "\n".join(lines)


@mcp.tool()
def dcam_list_sessions(limit: int = 1000) -> str:
    """List past chat sessions."""
    sessions = sorted(_store.read_sessions(), key=lambda s: s.started_at, reverse=True)[:limit]
    if not sessions:
        return "No sessions found"
    lines = [f"{s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')}  "
             f"{s.message_count} msgs  {s.title}" for s in sessions]
    return "\n".join(lines)


@mcp.tool()
def dcam_search_history(query: str, limit: int = 1000) -> str:
    """Search across all chat history using BM25 ranking."""
    results = _store.search_messages(query, limit=limit)
    if not results:
        return f"No messages matching '{query}'"
    lines = [f"[{m.session_id}] {m.role.value}: {m.content[:150]}" for m in results]
    return "\n".join(lines)


# --- Code Indexing Tools ---

@mcp.tool()
def dcam_compact(file_path: str) -> str:
    """Index a file into summarized chunks. Use this INSTEAD of reading the full file.
    Returns a compact summary of all functions/classes with line ranges."""
    try:
        n = compact_mod.compact_file(_store, file_path)
        chunks = _store.read_chunks(file_path)
        lines = [f"{c.chunk_type.value:8} {c.name:30} L{c.start_line}-{c.end_line}  {c.summary[:80]}"
                 for c in chunks]
        return f"Indexed {n} chunks:\n" + "\n".join(lines)
    except FileNotFoundError:
        return f"File not found: {file_path}"


@mcp.tool()
def dcam_lookup(symbol: str) -> str:
    """Find a function, class, or symbol across all indexed files.
    Returns file path, line range, and summary for each match."""
    chunks = compact_mod.lookup(_store, symbol)
    if not chunks:
        return f"No matches for '{symbol}'"
    lines = [f"{c.chunk_type.value:8} {c.name:30} {c.file_path} L{c.start_line}-{c.end_line}"
             for c in chunks]
    return "\n".join(lines)


@mcp.tool()
def dcam_fetch(file_path: str, start_line: int, end_line: int) -> str:
    """Fetch specific lines from a file. Use after dcam_compact to get only
    the code you need instead of loading the entire file."""
    code = compact_mod.fetch_lines(file_path, start_line, end_line)
    if not code:
        return f"Could not read {file_path} L{start_line}-{end_line}"
    return code


@mcp.tool()
def dcam_fetch_chunk(chunk_id: int) -> str:
    """Fetch the source code for a specific indexed chunk by ID."""
    chunks = _store.read_chunks()
    chunk = next((c for c in chunks if c.chunk_id == chunk_id), None)
    if not chunk:
        return f"Chunk {chunk_id} not found"
    code = compact_mod.fetch_lines(chunk.file_path, chunk.start_line, chunk.end_line)
    return f"# {chunk.name} ({chunk.chunk_type.value}) — {chunk.file_path} L{chunk.start_line}-{chunk.end_line}\n{code}"


# --- Task Tools ---

@mcp.tool()
def dcam_task_create(title: str, priority: int = 1, session_id: Optional[str] = None) -> str:
    """Create a task tracked by beads."""
    from dcam.orchestrator import create_task
    task = create_task(title, priority=priority, session_id=session_id)
    if task:
        return f"Created task {task.id}: {task.title} (P{task.priority})"
    return "Failed to create task. Is bd initialized?"


@mcp.tool()
def dcam_task_ready() -> str:
    """List tasks that are ready for work (no open blockers)."""
    from dcam.orchestrator import get_ready_tasks
    tasks = get_ready_tasks()
    if not tasks:
        return "No tasks ready"
    lines = [f"{t.id}  P{t.priority}  {t.title}" for t in tasks]
    return "\n".join(lines)


@mcp.tool()
def dcam_task_complete(task_id: str, reason: str = "completed") -> str:
    """Mark a task as completed."""
    from dcam.orchestrator import complete_task
    if complete_task(task_id, reason):
        return f"Completed task {task_id}"
    return f"Failed to complete task {task_id}"


# --- Status ---

@mcp.tool()
def dcam_status() -> str:
    """Show dcam status: sessions, memories, indexed files."""
    sessions = _store.read_sessions()
    chunks = _store.read_chunks()
    mems = [m for m in _store.read_memories() if m.active]
    from dcam.bridge import bd_available
    return (
        f"Sessions: {len(sessions)} ({sum(1 for s in sessions if not s.ended_at)} active)\n"
        f"Messages: {len(_store.read_messages())}\n"
        f"Memories: {len(mems)} active\n"
        f"Indexed: {len(set(c.file_path for c in chunks))} files, {len(chunks)} chunks\n"
        f"Beads: {'connected' if bd_available() else 'not available'}"
    )


# --- Claude Code Integration Tools ---

@mcp.tool()
def dcam_claude_sync(project_path: Optional[str] = None) -> str:
    """Sync Claude Code sessions to DeltaCAT storage.
    Call this to persist the current session's conversation."""
    from dcam import claude_code
    synced = claude_code.sync_all_sessions(_store, project_path)
    return f"Synced {synced} session(s)"


@mcp.tool()
def dcam_claude_context(sessions: int = 3, messages_per_session: int = 20) -> str:
    """Load context from recent Claude Code sessions.
    Use this at the start of a new session to get previous conversation context."""
    from dcam import claude_code
    ctx = claude_code.get_recent_context(_store, limit=sessions,
                                         max_messages_per_session=messages_per_session)
    return ctx if ctx else "No previous session context available."


def run_server():
    """Start the MCP server (stdio transport)."""
    _store.init_tables()
    mcp.run()
