"""DeltaCAT table store — shared read/write for all tables."""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pyarrow as pa

from dcam.local_catalog import LocalCatalog
from dcam.search import SearchBackend, get_backend
from dcam.models import (
    ChatMessage, ChatSession, ChunkType, FileChunk,
    Memory, MemoryType, MessageRole,
)

# --- Schemas ---

MEMORY_SCHEMA = pa.schema([
    ("id", pa.int64()), ("type", pa.string()), ("category", pa.string()),
    ("topic", pa.string()), ("content", pa.string()),
    ("created_at", pa.string()), ("updated_at", pa.string()),
    ("reinforcement_count", pa.int64()), ("source_session_id", pa.string()),
    ("active", pa.bool_()),
])

MESSAGE_SCHEMA = pa.schema([
    ("id", pa.int64()), ("session_id", pa.string()), ("role", pa.string()),
    ("content", pa.string()), ("timestamp", pa.string()), ("metadata", pa.string()),
])

SESSION_SCHEMA = pa.schema([
    ("session_id", pa.string()), ("title", pa.string()),
    ("started_at", pa.string()), ("ended_at", pa.string()),
    ("message_count", pa.int64()), ("summary", pa.string()),
    ("beads_issue_id", pa.string()),
])

CHUNK_SCHEMA = pa.schema([
    ("chunk_id", pa.int64()), ("file_path", pa.string()),
    ("chunk_type", pa.string()), ("name", pa.string()),
    ("summary", pa.string()), ("start_line", pa.int64()),
    ("end_line", pa.int64()), ("last_indexed", pa.string()),
])

FILE_SCHEMA = pa.schema([
    ("file_path", pa.string()), ("language", pa.string()),
    ("line_count", pa.int64()), ("summary", pa.string()),
    ("chunk_count", pa.int64()), ("last_indexed", pa.string()),
])

ALL_TABLES = {
    "memories": MEMORY_SCHEMA,
    "chat_messages": MESSAGE_SCHEMA,
    "chat_sessions": SESSION_SCHEMA,
    "compact_chunks": CHUNK_SCHEMA,
    "compact_files": FILE_SCHEMA,
}


class DeltaStore:
    """Unified DeltaCAT table store for all DCAM data."""

    def __init__(self, namespace: str = "dcam", search_backend: str = "bm25"):
        self.namespace = namespace
        self._counters: Dict[str, int] = {}
        self.catalog = LocalCatalog()
        self.search = get_backend(search_backend)
        self._sync_counters()

    def _sync_counters(self):
        """Sync ID counters from existing table data to avoid collisions."""
        for table, col in [("memories", "id"), ("chat_messages", "id"), ("compact_chunks", "chunk_id")]:
            try:
                t = self._read_table(table)
                if t and t.num_rows > 0:
                    self._counters[table] = max(t.column(col).to_pylist()) + 1
            except Exception:
                pass

    def init_tables(self):
        """Create namespace and all tables if they don't exist."""
        self.catalog.ensure_namespace(self.namespace)
        for name, schema in ALL_TABLES.items():
            if not self.catalog.table_exists(self.namespace, name):
                self.catalog.create_table(self.namespace, name, schema)

    def _next_id(self, table: str) -> int:
        if table not in self._counters:
            self._counters[table] = 1
        val = self._counters[table]
        self._counters[table] = val + 1
        return val

    def _read_table(self, name: str) -> Optional[pa.Table]:
        return self.catalog.read_table(self.namespace, name)

    def _write_table(self, name: str, table: pa.Table, append: bool = False):
        self.catalog.write_table(self.namespace, name, table, append=append)

    # --- Memory ---

    def read_memories(self) -> List[Memory]:
        t = self._read_table("memories")
        if not t:
            return []
        mems = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            m = Memory(id=r["id"], type=MemoryType(r["type"]), category=r.get("category"),
                       topic=r.get("topic"), content=r["content"],
                       created_at=datetime.fromisoformat(r["created_at"]),
                       updated_at=datetime.fromisoformat(r["updated_at"]),
                       reinforcement_count=r.get("reinforcement_count", 1),
                       source_session_id=r.get("source_session_id"), active=r.get("active", True))
            mems.append(m)
            if m.id and m.id >= self._counters.get("memories", 0):
                self._counters["memories"] = m.id
        return mems

    def write_memories(self, mems: List[Memory]):
        if not mems:
            return
        data = {
            "id": [m.id for m in mems], "type": [m.type.value for m in mems],
            "category": [m.category for m in mems], "topic": [m.topic for m in mems],
            "content": [m.content for m in mems],
            "created_at": [m.created_at.isoformat() for m in mems],
            "updated_at": [m.updated_at.isoformat() for m in mems],
            "reinforcement_count": [m.reinforcement_count for m in mems],
            "source_session_id": [m.source_session_id for m in mems],
            "active": [m.active for m in mems],
        }
        self._write_table("memories", pa.Table.from_pydict(data, schema=MEMORY_SCHEMA))

    # --- Chat Messages ---

    def read_messages(self, session_id: Optional[str] = None) -> List[ChatMessage]:
        t = self._read_table("chat_messages")
        if not t:
            return []
        msgs = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            msg = ChatMessage(id=r["id"], session_id=r["session_id"],
                              role=MessageRole(r["role"]), content=r.get("content", ""),
                              timestamp=datetime.fromisoformat(r["timestamp"]) if r.get("timestamp") else datetime.now(),
                              metadata=r.get("metadata"))
            if session_id and msg.session_id != session_id:
                continue
            msgs.append(msg)
            if msg.id and msg.id >= self._counters.get("chat_messages", 0):
                self._counters["chat_messages"] = msg.id
        return msgs

    def append_message(self, msg: ChatMessage):
        msg.id = self._next_id("chat_messages")
        data = {
            "id": [msg.id], "session_id": [msg.session_id],
            "role": [msg.role.value], "content": [msg.content],
            "timestamp": [msg.timestamp.isoformat()], "metadata": [msg.metadata],
        }
        table = pa.Table.from_pydict(data, schema=MESSAGE_SCHEMA)
        self._write_table("chat_messages", table, append=True)

    # --- Sessions ---

    def read_sessions(self) -> List[ChatSession]:
        t = self._read_table("chat_sessions")
        if not t:
            return []
        return [ChatSession(
            session_id=t.column("session_id")[i].as_py(),
            title=t.column("title")[i].as_py(),
            started_at=datetime.fromisoformat(t.column("started_at")[i].as_py()) if t.column("started_at")[i].as_py() else datetime.now(),
            ended_at=datetime.fromisoformat(t.column("ended_at")[i].as_py()) if t.column("ended_at")[i].as_py() else None,
            message_count=t.column("message_count")[i].as_py() or 0,
            summary=t.column("summary")[i].as_py(),
            beads_issue_id=t.column("beads_issue_id")[i].as_py(),
        ) for i in range(t.num_rows)]

    def write_sessions(self, sessions: List[ChatSession]):
        if not sessions:
            return
        data = {
            "session_id": [s.session_id for s in sessions],
            "title": [s.title for s in sessions],
            "started_at": [s.started_at.isoformat() for s in sessions],
            "ended_at": [s.ended_at.isoformat() if s.ended_at else None for s in sessions],
            "message_count": [s.message_count for s in sessions],
            "summary": [s.summary for s in sessions],
            "beads_issue_id": [s.beads_issue_id for s in sessions],
        }
        self._write_table("chat_sessions", pa.Table.from_pydict(data, schema=SESSION_SCHEMA))

    # --- Compact Chunks ---

    def read_chunks(self, file_path: Optional[str] = None) -> List[FileChunk]:
        t = self._read_table("compact_chunks")
        if not t:
            return []
        chunks = []
        for i in range(t.num_rows):
            c = FileChunk(
                chunk_id=t.column("chunk_id")[i].as_py(),
                file_path=t.column("file_path")[i].as_py(),
                chunk_type=ChunkType(t.column("chunk_type")[i].as_py()),
                name=t.column("name")[i].as_py() or "",
                summary=t.column("summary")[i].as_py() or "",
                start_line=t.column("start_line")[i].as_py() or 0,
                end_line=t.column("end_line")[i].as_py() or 0,
                last_indexed=datetime.fromisoformat(t.column("last_indexed")[i].as_py()) if t.column("last_indexed")[i].as_py() else datetime.now(),
            )
            if file_path and c.file_path != file_path:
                continue
            chunks.append(c)
            if c.chunk_id >= self._counters.get("compact_chunks", 0):
                self._counters["compact_chunks"] = c.chunk_id
        return chunks

    def write_chunks(self, chunks: List[FileChunk]):
        if not chunks:
            return
        data = {
            "chunk_id": [c.chunk_id for c in chunks],
            "file_path": [c.file_path for c in chunks],
            "chunk_type": [c.chunk_type.value for c in chunks],
            "name": [c.name for c in chunks],
            "summary": [c.summary for c in chunks],
            "start_line": [c.start_line for c in chunks],
            "end_line": [c.end_line for c in chunks],
            "last_indexed": [c.last_indexed.isoformat() for c in chunks],
        }
        self._write_table("compact_chunks", pa.Table.from_pydict(data, schema=CHUNK_SCHEMA))

    # --- Search ---

    def search_messages(self, query: str, session_id: Optional[str] = None, limit: int = 20) -> List[ChatMessage]:
        """Search messages using the configured search backend."""
        msgs = self.read_messages(session_id)
        docs = [(i, m.content) for i, m in enumerate(msgs)]
        results = self.search.search(query, docs, limit=limit)
        return [msgs[idx] for idx, _ in results]

    def search_memories(self, query: str, limit: int = 20) -> List[Memory]:
        """Search active memories using the configured search backend."""
        mems = [m for m in self.read_memories() if m.active]
        docs = [(i, m.content) for i, m in enumerate(mems)]
        results = self.search.search(query, docs, limit=limit)
        return [mems[idx] for idx, _ in results]
