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
