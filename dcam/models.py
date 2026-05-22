"""Data models for DeltaCAT Agent Memory."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class MemoryType(str, Enum):
    SEMANTIC = "semantic"        # Facts: "project uses Java 17"
    EPISODIC = "episodic"        # Events: "fixed auth bug on Apr 3"
    PROCEDURAL = "procedural"    # Rules: "always run tests before commit"
    SHORT_TERM = "short_term"    # Temporary: "currently working on auth"
    PROJECT = "project"          # Cross-session: "repo structure, team conventions"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChunkType(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    IMPORT = "import"
    BLOCK = "block"


@dataclass
class Memory:
    id: Optional[int] = None
    type: MemoryType = MemoryType.SEMANTIC
    name: Optional[str] = None    # Named handle for recall (e.g. "java-stack")
    category: Optional[str] = None
    topic: Optional[str] = None
    content: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    reinforcement_count: int = 1
    source_session_id: Optional[str] = None
    active: bool = True


@dataclass
class ChatMessage:
    id: Optional[int] = None
    session_id: str = ""
    role: MessageRole = MessageRole.USER
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Optional[str] = None


@dataclass
class ChatSession:
    session_id: str = ""
    title: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    message_count: int = 0
    summary: Optional[str] = None
    beads_issue_id: Optional[str] = None


class DecisionStatus(str, Enum):
    OPEN = "open"
    DECIDED = "decided"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


@dataclass
class Decision:
    id: Optional[int] = None
    title: str = ""
    context: str = ""
    options: str = ""           # JSON: [{"key": "A", "summary": "..."}]
    recommended: Optional[str] = None
    chosen: Optional[str] = None
    rationale: Optional[str] = None
    status: DecisionStatus = DecisionStatus.OPEN
    supersedes_id: Optional[int] = None
    requested_by: Optional[str] = None  # slug of asker
    decided_by: Optional[str] = None    # "manager" / role label
    task_id: Optional[str] = None       # bd task id
    session_id: Optional[str] = None
    epic: Optional[str] = None          # epic slug, e.g. "native-read"
    op: Optional[str] = None            # operation, e.g. "CreateProvider"
    ticket: Optional[str] = None        # external ticket URL
    persist_target: Optional[str] = None  # "claude" | "agents" | "both"
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    decided_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Lesson:
    id: Optional[int] = None
    content: str = ""
    category: Optional[str] = None      # "design" | "testing" | "ops" | None
    source_slug: Optional[str] = None
    session_id: Optional[str] = None
    epic: Optional[str] = None
    op: Optional[str] = None
    ticket: Optional[str] = None
    persist_target: Optional[str] = None
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)


class CriticalPointStatus(str, Enum):
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass
class CriticalPoint:
    """A forward-looking invariant or guard rail.

    Distinct from a Lesson: lessons are reactive ("we learned X");
    critical points are prescriptive ("never run X under admin creds").
    The reviewer agent reads them every run and flags violations.
    """
    id: Optional[int] = None
    content: str = ""
    rationale: Optional[str] = None
    epic: Optional[str] = None          # scope: epic-wide
    op: Optional[str] = None            # scope: per-op (most specific)
    ticket: Optional[str] = None
    status: CriticalPointStatus = CriticalPointStatus.ACTIVE
    source_slug: Optional[str] = None
    session_id: Optional[str] = None
    persist_target: Optional[str] = None
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    retired_at: Optional[datetime] = None
    retired_reason: Optional[str] = None


@dataclass
class FileChunk:
    chunk_id: int = 0
    file_path: str = ""
    chunk_type: ChunkType = ChunkType.BLOCK
    name: str = ""
    summary: str = ""
    start_line: int = 0
    end_line: int = 0
    last_indexed: datetime = field(default_factory=datetime.now)
