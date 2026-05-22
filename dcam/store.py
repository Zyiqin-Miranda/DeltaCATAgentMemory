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
    Decision, DecisionStatus, FileChunk, Lesson, Memory, MemoryType,
    MessageRole,
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

ALL_TABLES = {
    "memories": MEMORY_SCHEMA,
    "chat_messages": MESSAGE_SCHEMA,
    "chat_sessions": SESSION_SCHEMA,
    "compact_chunks": CHUNK_SCHEMA,
    "compact_files": FILE_SCHEMA,
    "decisions": DECISION_SCHEMA,
    "lessons": LESSON_SCHEMA,
    "critical_points": CRITICAL_POINT_SCHEMA,
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
                           ("critical_points", "id")]:
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
