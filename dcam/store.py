"""DeltaCAT table store — shared read/write for all tables."""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pyarrow as pa

from dcam.local_catalog import LocalCatalog
from dcam.search import SearchBackend, get_backend
from dcam.models import (
    ChatMessage, ChatSession, ChunkType, CriticalPoint, CriticalPointStatus,
    Decision, DecisionStatus, FileChunk, Handoff, HandoffStatus, Lesson,
    Memory, MemoryType, MessageRole, Review, ReviewRequest,
    ReviewRequestStatus, Spec,
)

# --- Schemas ---

MEMORY_SCHEMA = pa.schema([
    ("id", pa.int64()), ("type", pa.string()), ("name", pa.string()),
    ("category", pa.string()), ("topic", pa.string()), ("content", pa.string()),
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

DECISION_SCHEMA = pa.schema([
    ("id", pa.int64()), ("title", pa.string()), ("context", pa.string()),
    ("options", pa.string()), ("recommended", pa.string()),
    ("chosen", pa.string()), ("rationale", pa.string()), ("status", pa.string()),
    ("supersedes_id", pa.int64()), ("requested_by", pa.string()),
    ("decided_by", pa.string()), ("task_id", pa.string()),
    ("session_id", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("persist_target", pa.string()),
    ("persisted_at", pa.string()), ("created_at", pa.string()),
    ("decided_at", pa.string()), ("updated_at", pa.string()),
])

LESSON_SCHEMA = pa.schema([
    ("id", pa.int64()), ("content", pa.string()), ("category", pa.string()),
    ("source_slug", pa.string()), ("session_id", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("persist_target", pa.string()), ("persisted_at", pa.string()),
    ("created_at", pa.string()),
])

CRITICAL_POINT_SCHEMA = pa.schema([
    ("id", pa.int64()), ("content", pa.string()), ("rationale", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("status", pa.string()), ("source_slug", pa.string()),
    ("session_id", pa.string()), ("persist_target", pa.string()),
    ("persisted_at", pa.string()), ("created_at", pa.string()),
    ("retired_at", pa.string()), ("retired_reason", pa.string()),
])

REVIEW_REQUEST_SCHEMA = pa.schema([
    ("id", pa.int64()), ("slug", pa.string()), ("notes", pa.string()),
    ("scope_files", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("git_head", pa.string()), ("related_decision_ids", pa.string()),
    ("status", pa.string()), ("claimed_by", pa.string()),
    ("session_id", pa.string()), ("persist_target", pa.string()),
    ("persisted_at", pa.string()), ("created_at", pa.string()),
    ("claimed_at", pa.string()), ("completed_at", pa.string()),
])

REVIEW_SCHEMA = pa.schema([
    ("id", pa.int64()), ("request_id", pa.int64()),
    ("reviewer", pa.string()), ("summary", pa.string()),
    ("blocking_findings", pa.int64()), ("advisory_findings", pa.int64()),
    ("lessons_added", pa.string()), ("critical_added", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("persist_target", pa.string()), ("persisted_at", pa.string()),
    ("created_at", pa.string()),
])

HANDOFF_SCHEMA = pa.schema([
    ("id", pa.int64()), ("from_slug", pa.string()), ("to_slug", pa.string()),
    ("files", pa.string()), ("notes", pa.string()),
    ("epic", pa.string()), ("op", pa.string()), ("ticket", pa.string()),
    ("status", pa.string()), ("ack_notes", pa.string()),
    ("persist_target", pa.string()), ("persisted_at", pa.string()),
    ("created_at", pa.string()), ("acknowledged_at", pa.string()),
])

SPEC_SCHEMA = pa.schema([
    ("id", pa.int64()), ("path", pa.string()), ("title", pa.string()),
    ("content_hash", pa.string()),
    ("epic", pa.string()), ("op", pa.string()),
    ("last_linked_decision_id", pa.int64()),
    ("last_synced_at", pa.string()),
    ("created_at", pa.string()), ("updated_at", pa.string()),
])

ALL_TABLES = {
    "memories": MEMORY_SCHEMA,
    "chat_messages": MESSAGE_SCHEMA,
    "chat_sessions": SESSION_SCHEMA,
    "compact_chunks": CHUNK_SCHEMA,
    "compact_files": FILE_SCHEMA,
    "decisions": DECISION_SCHEMA,
    "lessons": LESSON_SCHEMA,
    "critical_points": CRITICAL_POINT_SCHEMA,
    "review_requests": REVIEW_REQUEST_SCHEMA,
    "reviews": REVIEW_SCHEMA,
    "handoffs": HANDOFF_SCHEMA,
    "specs": SPEC_SCHEMA,
}


class DeltaStore:
    """Unified DeltaCAT table store for all DCAM data."""

    def __init__(self, namespace: str = "dcam", search_backend: str = "bm25",
                 catalog_backend: str = "local", branch: str = "main",
                 storage_root: Optional[str] = None):
        """
        Args:
            namespace: Table namespace for isolation.
            search_backend: "bm25" or "substring".
            catalog_backend: "local" (flat parquet), "delta" (partitioned + versioned),
                             "branch" (branched delta), or "deltacat" (ACID).
            branch: Branch name for branch backend (default: main).
            storage_root: Override the catalog's root directory. If unset,
                resolved via dcam.project.discover_root() so a project's
                `.dcam/` directory is auto-detected.
        """
        from dcam.project import discover_root, is_project_root
        self.namespace = namespace
        self.branch = branch
        self._counters: Dict[str, int] = {}
        self.storage_root = discover_root(storage_root)
        self.is_project_mode = is_project_root(self.storage_root)
        self.catalog = self._init_catalog(catalog_backend, branch,
                                          self.storage_root,
                                          self.is_project_mode)
        self.search = get_backend(search_backend)
        self._sync_counters()

    @staticmethod
    def _init_catalog(backend: str, branch: str, storage_root,
                      is_project_mode: bool):
        if backend == "deltacat":
            try:
                from dcam.deltacat_catalog import DeltaCatCatalog
                return DeltaCatCatalog()
            except ImportError as e:
                raise ImportError(
                    f"DeltaCAT backend requires deltacat package: pip install 'dcam[deltacat]'\n"
                    f"Error: {e}"
                )
        elif backend == "delta":
            from dcam.delta_catalog import DeltaNativeCatalog
            return DeltaNativeCatalog()
        elif backend == "branch":
            from dcam.branch_catalog import BranchCatalog
            return BranchCatalog(branch=branch)
        # `local` backend, the default. Switch implementation based on
        # whether we're in project mode (JSON-as-primary for committable
        # tables) or the historical global mode (parquet everything).
        if is_project_mode:
            from dcam.json_catalog import JsonCatalog
            return JsonCatalog(storage_root)
        # Global mode: keep the historical layout at ~/.dcam/tables/.
        return LocalCatalog()

    def _sync_counters(self):
        """Sync ID counters from existing table data to avoid collisions."""
        for table, col in [("memories", "id"), ("chat_messages", "id"),
                           ("compact_chunks", "chunk_id"),
                           ("decisions", "id"), ("lessons", "id"),
                           ("critical_points", "id"),
                           ("review_requests", "id"), ("reviews", "id"),
                           ("handoffs", "id"), ("specs", "id")]:
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

    def _write_table(self, name: str, table: pa.Table, append: bool = False,
                     partition_col: Optional[str] = None):
        kwargs = {"append": append}
        if partition_col:
            kwargs["partition_col"] = partition_col
        try:
            self.catalog.write_table(self.namespace, name, table, **kwargs)
        except TypeError:
            # Catalog doesn't support partition_col (local/deltacat)
            self.catalog.write_table(self.namespace, name, table, append=append)

    # --- Memory ---

    def read_memories(self) -> List[Memory]:
        t = self._read_table("memories")
        if not t:
            return []
        mems = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            m = Memory(id=r["id"], type=MemoryType(r["type"]), name=r.get("name"),
                       category=r.get("category"),
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
            "name": [m.name for m in mems],
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
        self._write_table("chat_messages", table, append=True,
                          partition_col="session_id")

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

    def search_messages(self, query: str, session_id: Optional[str] = None, limit: int = 1000) -> List[ChatMessage]:
        """Search messages using the configured search backend."""
        msgs = self.read_messages(session_id)
        docs = [(i, m.content) for i, m in enumerate(msgs)]
        results = self.search.search(query, docs, limit=limit)
        return [msgs[idx] for idx, _ in results]

    def search_memories(self, query: str, limit: int = 1000) -> List[Memory]:
        """Search active memories using the configured search backend."""
        mems = [m for m in self.read_memories() if m.active]
        docs = [(i, m.content) for i, m in enumerate(mems)]
        results = self.search.search(query, docs, limit=limit)
        return [mems[idx] for idx, _ in results]

    # --- Project Memory (cross-session) ---

    def read_project_memories(self) -> List[Memory]:
        """Read memories that persist across all sessions."""
        return [m for m in self.read_memories() if m.active and m.type == MemoryType.PROJECT]

    def add_project_memory(self, content: str, name: Optional[str] = None,
                           topic: Optional[str] = None,
                           category: Optional[str] = None) -> Memory:
        """Add a cross-session project memory with an optional name for recall."""
        mems = self.read_memories()
        # If name exists, update instead of creating duplicate
        if name:
            for m in mems:
                if m.name == name and m.active:
                    m.content = content
                    m.updated_at = datetime.now()
                    m.reinforcement_count += 1
                    self.write_memories(mems)
                    return m
        m = Memory(
            id=self._next_id("memories"),
            type=MemoryType.PROJECT,
            name=name,
            topic=topic,
            category=category,
            content=content,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        mems.append(m)
        self.write_memories(mems)
        return m

    def recall_by_name(self, name: str) -> Optional[Memory]:
        """Recall a named project memory."""
        for m in self.read_memories():
            if m.active and m.name == name:
                return m
        return None

    def get_session_context(self, session_id: Optional[str] = None) -> str:
        """Build context string from project memories + session memories.
        
        This is what gets injected into every new session automatically.
        """
        lines = []

        # Always include project memories
        project_mems = self.read_project_memories()
        if project_mems:
            lines.append("## Project Memory (persists across sessions)\n")
            for m in project_mems:
                prefix = f"[{m.topic}] " if m.topic else ""
                lines.append(f"- {prefix}{m.content}")
            lines.append("")

        # Include session-specific memories if session given
        if session_id:
            session_mems = [m for m in self.read_memories()
                           if m.active and m.source_session_id == session_id
                           and m.type != MemoryType.PROJECT]
            if session_mems:
                lines.append("## Session Memory\n")
                for m in session_mems:
                    lines.append(f"- [{m.type.value}] {m.content}")
                lines.append("")

        return "\n".join(lines)

    # --- Decisions ---

    @staticmethod
    def _opt_dt(s):
        return datetime.fromisoformat(s) if s else None

    def read_decisions(self, status: Optional[str] = None) -> List[Decision]:
        t = self._read_table("decisions")
        if not t:
            return []
        out: List[Decision] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            d = Decision(
                id=r["id"], title=r.get("title") or "",
                context=r.get("context") or "", options=r.get("options") or "",
                recommended=r.get("recommended"), chosen=r.get("chosen"),
                rationale=r.get("rationale"),
                status=DecisionStatus(r.get("status") or "open"),
                supersedes_id=r.get("supersedes_id"),
                requested_by=r.get("requested_by"),
                decided_by=r.get("decided_by"),
                task_id=r.get("task_id"), session_id=r.get("session_id"),
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
                decided_at=self._opt_dt(r.get("decided_at")),
                updated_at=self._opt_dt(r.get("updated_at")) or datetime.now(),
            )
            if status and d.status.value != status:
                continue
            out.append(d)
            if d.id and d.id >= self._counters.get("decisions", 0):
                self._counters["decisions"] = d.id
        return out

    def write_decisions(self, decisions: List[Decision]):
        if not decisions:
            return
        data = {
            "id": [d.id for d in decisions],
            "title": [d.title for d in decisions],
            "context": [d.context for d in decisions],
            "options": [d.options for d in decisions],
            "recommended": [d.recommended for d in decisions],
            "chosen": [d.chosen for d in decisions],
            "rationale": [d.rationale for d in decisions],
            "status": [d.status.value for d in decisions],
            "supersedes_id": [d.supersedes_id for d in decisions],
            "requested_by": [d.requested_by for d in decisions],
            "decided_by": [d.decided_by for d in decisions],
            "task_id": [d.task_id for d in decisions],
            "session_id": [d.session_id for d in decisions],
            "epic": [d.epic for d in decisions],
            "op": [d.op for d in decisions],
            "ticket": [d.ticket for d in decisions],
            "persist_target": [d.persist_target for d in decisions],
            "persisted_at": [d.persisted_at.isoformat() if d.persisted_at else None for d in decisions],
            "created_at": [d.created_at.isoformat() for d in decisions],
            "decided_at": [d.decided_at.isoformat() if d.decided_at else None for d in decisions],
            "updated_at": [d.updated_at.isoformat() for d in decisions],
        }
        self._write_table("decisions", pa.Table.from_pydict(data, schema=DECISION_SCHEMA))

    def append_decision(self, d: Decision) -> Decision:
        d.id = self._next_id("decisions")
        existing = self.read_decisions()
        existing.append(d)
        self.write_decisions(existing)
        return d

    def update_decision(self, d: Decision):
        all_ds = self.read_decisions()
        for i, x in enumerate(all_ds):
            if x.id == d.id:
                all_ds[i] = d
                break
        else:
            all_ds.append(d)
        self.write_decisions(all_ds)

    def get_decision_chain(self, decision_id: int) -> List[Decision]:
        """Return [oldest, …, given_id] following supersedes_id pointers."""
        by_id = {d.id: d for d in self.read_decisions()}
        chain: List[Decision] = []
        seen: set = set()
        cur = by_id.get(decision_id)
        while cur and cur.id not in seen:
            chain.append(cur)
            seen.add(cur.id)
            cur = by_id.get(cur.supersedes_id) if cur.supersedes_id else None
        return list(reversed(chain))

    # --- Lessons ---

    def read_lessons(self) -> List[Lesson]:
        t = self._read_table("lessons")
        if not t:
            return []
        out: List[Lesson] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            ls = Lesson(
                id=r["id"], content=r.get("content") or "",
                category=r.get("category"), source_slug=r.get("source_slug"),
                session_id=r.get("session_id"),
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
            )
            out.append(ls)
            if ls.id and ls.id >= self._counters.get("lessons", 0):
                self._counters["lessons"] = ls.id
        return out

    def write_lessons(self, lessons: List[Lesson]):
        if not lessons:
            return
        data = {
            "id": [l.id for l in lessons],
            "content": [l.content for l in lessons],
            "category": [l.category for l in lessons],
            "source_slug": [l.source_slug for l in lessons],
            "session_id": [l.session_id for l in lessons],
            "epic": [l.epic for l in lessons],
            "op": [l.op for l in lessons],
            "ticket": [l.ticket for l in lessons],
            "persist_target": [l.persist_target for l in lessons],
            "persisted_at": [l.persisted_at.isoformat() if l.persisted_at else None for l in lessons],
            "created_at": [l.created_at.isoformat() for l in lessons],
        }
        self._write_table("lessons", pa.Table.from_pydict(data, schema=LESSON_SCHEMA))

    def append_lesson(self, l: Lesson) -> Lesson:
        l.id = self._next_id("lessons")
        existing = self.read_lessons()
        existing.append(l)
        self.write_lessons(existing)
        return l

    def update_lesson(self, l: Lesson):
        all_ls = self.read_lessons()
        for i, x in enumerate(all_ls):
            if x.id == l.id:
                all_ls[i] = l
                break
        else:
            all_ls.append(l)
        self.write_lessons(all_ls)

    # --- Critical Points ---

    def read_critical_points(self,
                             status: Optional[str] = None) -> List[CriticalPoint]:
        t = self._read_table("critical_points")
        if not t:
            return []
        out: List[CriticalPoint] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            cp = CriticalPoint(
                id=r["id"], content=r.get("content") or "",
                rationale=r.get("rationale"),
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                status=CriticalPointStatus(r.get("status") or "active"),
                source_slug=r.get("source_slug"),
                session_id=r.get("session_id"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
                retired_at=self._opt_dt(r.get("retired_at")),
                retired_reason=r.get("retired_reason"),
            )
            if status and cp.status.value != status:
                continue
            out.append(cp)
            if cp.id and cp.id >= self._counters.get("critical_points", 0):
                self._counters["critical_points"] = cp.id
        return out

    def write_critical_points(self, points: List[CriticalPoint]):
        if not points:
            return
        data = {
            "id": [p.id for p in points],
            "content": [p.content for p in points],
            "rationale": [p.rationale for p in points],
            "epic": [p.epic for p in points],
            "op": [p.op for p in points],
            "ticket": [p.ticket for p in points],
            "status": [p.status.value for p in points],
            "source_slug": [p.source_slug for p in points],
            "session_id": [p.session_id for p in points],
            "persist_target": [p.persist_target for p in points],
            "persisted_at": [p.persisted_at.isoformat() if p.persisted_at else None for p in points],
            "created_at": [p.created_at.isoformat() for p in points],
            "retired_at": [p.retired_at.isoformat() if p.retired_at else None for p in points],
            "retired_reason": [p.retired_reason for p in points],
        }
        self._write_table("critical_points",
                          pa.Table.from_pydict(data, schema=CRITICAL_POINT_SCHEMA))

    def append_critical_point(self, cp: CriticalPoint) -> CriticalPoint:
        cp.id = self._next_id("critical_points")
        existing = self.read_critical_points()
        existing.append(cp)
        self.write_critical_points(existing)
        return cp

    def update_critical_point(self, cp: CriticalPoint):
        all_cp = self.read_critical_points()
        for i, x in enumerate(all_cp):
            if x.id == cp.id:
                all_cp[i] = cp
                break
        else:
            all_cp.append(cp)
        self.write_critical_points(all_cp)

    # --- Review Requests ---

    def read_review_requests(self,
                             status: Optional[str] = None) -> List[ReviewRequest]:
        t = self._read_table("review_requests")
        if not t:
            return []
        out: List[ReviewRequest] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            req = ReviewRequest(
                id=r["id"], slug=r.get("slug"),
                notes=r.get("notes") or "",
                scope_files=r.get("scope_files") or "",
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                git_head=r.get("git_head"),
                related_decision_ids=r.get("related_decision_ids") or "",
                status=ReviewRequestStatus(r.get("status") or "pending"),
                claimed_by=r.get("claimed_by"),
                session_id=r.get("session_id"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
                claimed_at=self._opt_dt(r.get("claimed_at")),
                completed_at=self._opt_dt(r.get("completed_at")),
            )
            if status and req.status.value != status:
                continue
            out.append(req)
            if req.id and req.id >= self._counters.get("review_requests", 0):
                self._counters["review_requests"] = req.id
        return out

    def write_review_requests(self, reqs: List[ReviewRequest]):
        if not reqs:
            return
        data = {
            "id": [r.id for r in reqs],
            "slug": [r.slug for r in reqs],
            "notes": [r.notes for r in reqs],
            "scope_files": [r.scope_files for r in reqs],
            "epic": [r.epic for r in reqs],
            "op": [r.op for r in reqs],
            "ticket": [r.ticket for r in reqs],
            "git_head": [r.git_head for r in reqs],
            "related_decision_ids": [r.related_decision_ids for r in reqs],
            "status": [r.status.value for r in reqs],
            "claimed_by": [r.claimed_by for r in reqs],
            "session_id": [r.session_id for r in reqs],
            "persist_target": [r.persist_target for r in reqs],
            "persisted_at": [r.persisted_at.isoformat() if r.persisted_at else None for r in reqs],
            "created_at": [r.created_at.isoformat() for r in reqs],
            "claimed_at": [r.claimed_at.isoformat() if r.claimed_at else None for r in reqs],
            "completed_at": [r.completed_at.isoformat() if r.completed_at else None for r in reqs],
        }
        self._write_table("review_requests",
                          pa.Table.from_pydict(data, schema=REVIEW_REQUEST_SCHEMA))

    def append_review_request(self, req: ReviewRequest) -> ReviewRequest:
        req.id = self._next_id("review_requests")
        existing = self.read_review_requests()
        existing.append(req)
        self.write_review_requests(existing)
        return req

    def update_review_request(self, req: ReviewRequest):
        all_r = self.read_review_requests()
        for i, x in enumerate(all_r):
            if x.id == req.id:
                all_r[i] = req
                break
        else:
            all_r.append(req)
        self.write_review_requests(all_r)

    # --- Reviews ---

    def read_reviews(self) -> List[Review]:
        t = self._read_table("reviews")
        if not t:
            return []
        out: List[Review] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            rv = Review(
                id=r["id"], request_id=r.get("request_id") or 0,
                reviewer=r.get("reviewer") or "reviewer",
                summary=r.get("summary") or "",
                blocking_findings=r.get("blocking_findings") or 0,
                advisory_findings=r.get("advisory_findings") or 0,
                lessons_added=r.get("lessons_added") or "",
                critical_added=r.get("critical_added") or "",
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
            )
            out.append(rv)
            if rv.id and rv.id >= self._counters.get("reviews", 0):
                self._counters["reviews"] = rv.id
        return out

    def write_reviews(self, reviews: List[Review]):
        if not reviews:
            return
        data = {
            "id": [r.id for r in reviews],
            "request_id": [r.request_id for r in reviews],
            "reviewer": [r.reviewer for r in reviews],
            "summary": [r.summary for r in reviews],
            "blocking_findings": [r.blocking_findings for r in reviews],
            "advisory_findings": [r.advisory_findings for r in reviews],
            "lessons_added": [r.lessons_added for r in reviews],
            "critical_added": [r.critical_added for r in reviews],
            "epic": [r.epic for r in reviews],
            "op": [r.op for r in reviews],
            "ticket": [r.ticket for r in reviews],
            "persist_target": [r.persist_target for r in reviews],
            "persisted_at": [r.persisted_at.isoformat() if r.persisted_at else None for r in reviews],
            "created_at": [r.created_at.isoformat() for r in reviews],
        }
        self._write_table("reviews",
                          pa.Table.from_pydict(data, schema=REVIEW_SCHEMA))

    def append_review(self, rv: Review) -> Review:
        rv.id = self._next_id("reviews")
        existing = self.read_reviews()
        existing.append(rv)
        self.write_reviews(existing)
        return rv

    # --- Handoffs ---

    def read_handoffs(self,
                      status: Optional[str] = None) -> List[Handoff]:
        t = self._read_table("handoffs")
        if not t:
            return []
        out: List[Handoff] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            h = Handoff(
                id=r["id"],
                from_slug=r.get("from_slug") or "",
                to_slug=r.get("to_slug") or "",
                files=r.get("files") or "",
                notes=r.get("notes") or "",
                epic=r.get("epic"), op=r.get("op"), ticket=r.get("ticket"),
                status=HandoffStatus(r.get("status") or "pending"),
                ack_notes=r.get("ack_notes"),
                persist_target=r.get("persist_target"),
                persisted_at=self._opt_dt(r.get("persisted_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
                acknowledged_at=self._opt_dt(r.get("acknowledged_at")),
            )
            if status and h.status.value != status:
                continue
            out.append(h)
            if h.id and h.id >= self._counters.get("handoffs", 0):
                self._counters["handoffs"] = h.id
        return out

    def write_handoffs(self, handoffs: List[Handoff]):
        if not handoffs:
            return
        data = {
            "id": [h.id for h in handoffs],
            "from_slug": [h.from_slug for h in handoffs],
            "to_slug": [h.to_slug for h in handoffs],
            "files": [h.files for h in handoffs],
            "notes": [h.notes for h in handoffs],
            "epic": [h.epic for h in handoffs],
            "op": [h.op for h in handoffs],
            "ticket": [h.ticket for h in handoffs],
            "status": [h.status.value for h in handoffs],
            "ack_notes": [h.ack_notes for h in handoffs],
            "persist_target": [h.persist_target for h in handoffs],
            "persisted_at": [h.persisted_at.isoformat() if h.persisted_at else None for h in handoffs],
            "created_at": [h.created_at.isoformat() for h in handoffs],
            "acknowledged_at": [h.acknowledged_at.isoformat() if h.acknowledged_at else None for h in handoffs],
        }
        self._write_table("handoffs",
                          pa.Table.from_pydict(data, schema=HANDOFF_SCHEMA))

    def append_handoff(self, h: Handoff) -> Handoff:
        h.id = self._next_id("handoffs")
        existing = self.read_handoffs()
        existing.append(h)
        self.write_handoffs(existing)
        return h

    def update_handoff(self, h: Handoff):
        all_h = self.read_handoffs()
        for i, x in enumerate(all_h):
            if x.id == h.id:
                all_h[i] = h
                break
        else:
            all_h.append(h)
        self.write_handoffs(all_h)

    # --- Specs ---

    def read_specs(self) -> List[Spec]:
        t = self._read_table("specs")
        if not t:
            return []
        out: List[Spec] = []
        for i in range(t.num_rows):
            r = {c: t.column(c)[i].as_py() for c in t.column_names}
            s = Spec(
                id=r["id"], path=r.get("path") or "",
                title=r.get("title"), content_hash=r.get("content_hash"),
                epic=r.get("epic"), op=r.get("op"),
                last_linked_decision_id=r.get("last_linked_decision_id"),
                last_synced_at=self._opt_dt(r.get("last_synced_at")),
                created_at=self._opt_dt(r.get("created_at")) or datetime.now(),
                updated_at=self._opt_dt(r.get("updated_at")) or datetime.now(),
            )
            out.append(s)
            if s.id and s.id >= self._counters.get("specs", 0):
                self._counters["specs"] = s.id
        return out

    def write_specs(self, specs: List[Spec]):
        if not specs:
            return
        data = {
            "id": [s.id for s in specs],
            "path": [s.path for s in specs],
            "title": [s.title for s in specs],
            "content_hash": [s.content_hash for s in specs],
            "epic": [s.epic for s in specs],
            "op": [s.op for s in specs],
            "last_linked_decision_id": [s.last_linked_decision_id for s in specs],
            "last_synced_at": [s.last_synced_at.isoformat() if s.last_synced_at else None for s in specs],
            "created_at": [s.created_at.isoformat() for s in specs],
            "updated_at": [s.updated_at.isoformat() for s in specs],
        }
        self._write_table("specs",
                          pa.Table.from_pydict(data, schema=SPEC_SCHEMA))

    def append_spec(self, s: Spec) -> Spec:
        s.id = self._next_id("specs")
        existing = self.read_specs()
        existing.append(s)
        self.write_specs(existing)
        return s

    def update_spec(self, s: Spec):
        all_s = self.read_specs()
        for i, x in enumerate(all_s):
            if x.id == s.id:
                all_s[i] = s
                break
        else:
            all_s.append(s)
        self.write_specs(all_s)
