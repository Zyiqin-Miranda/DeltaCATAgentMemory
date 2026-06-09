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


class Severity(str, Enum):
    """Routing priority for review requests and decision asks.

    BLOCKER: dev cannot make progress without this. Reviewer / manager
        should drain it before any other work. Surfaces at the top of
        `dcam tmux digest`, prefixed loudly in the reviewer-pane
        notification, and listed by `dcam tmux escalations`.
    ADVISORY (default): normal priority, picked up at the reviewer's
        own pace.
    """
    BLOCKER = "blocker"
    ADVISORY = "advisory"


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
    severity: Severity = Severity.ADVISORY  # blocker = dev is stuck waiting
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


class ReviewRequestStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    WITHDRAWN = "withdrawn"


@dataclass
class ReviewRequest:
    """A dev's pull-based ask for review.

    The reviewer agent runs in a long-lived tmux window and consumes these
    on `dcam tmux reviews pending`. Each new request triggers a tmux
    send-keys notification into the reviewer's pane.
    """
    id: Optional[int] = None
    slug: Optional[str] = None              # requesting dev
    notes: str = ""                         # what to look at
    scope_files: str = ""                   # comma-sep glob list
    epic: Optional[str] = None
    op: Optional[str] = None
    ticket: Optional[str] = None
    git_head: Optional[str] = None          # HEAD SHA at request time
    related_decision_ids: str = ""          # comma-sep DEC ids
    status: ReviewRequestStatus = ReviewRequestStatus.PENDING
    severity: Severity = Severity.ADVISORY
    claimed_by: Optional[str] = None
    session_id: Optional[str] = None
    persist_target: Optional[str] = None
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    claimed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class Review:
    """A reviewer's record of a completed review.

    Always tied to a ReviewRequest (request_id required). Records the
    summary, what new lessons/critical points came out of it, and any
    findings flagged blocking. The reviewer's own memory grows: lessons
    tagged `category=review-finding` are surfaced into the next
    reviewer-session's startup prompt.
    """
    id: Optional[int] = None
    request_id: int = 0
    reviewer: str = "reviewer"              # role label or actual identity
    summary: str = ""
    blocking_findings: int = 0              # count of must-fix issues
    advisory_findings: int = 0              # count of suggestions
    lessons_added: str = ""                 # comma-sep lesson ids
    critical_added: str = ""                # comma-sep CP ids
    epic: Optional[str] = None
    op: Optional[str] = None
    ticket: Optional[str] = None
    persist_target: Optional[str] = None
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)


class HandoffStatus(str, Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    WITHDRAWN = "withdrawn"


@dataclass
class Handoff:
    """Structured peer-to-peer handoff between dev slugs.

    More durable than a `dcam tmux msg`: handoffs are the artifact of
    one engineer finishing a slice that the next engineer needs to pick
    up. Renders into a managed `## Handoffs` section.
    """
    id: Optional[int] = None
    from_slug: str = ""
    to_slug: str = ""
    files: str = ""                         # comma-sep paths/globs
    notes: str = ""
    epic: Optional[str] = None
    op: Optional[str] = None
    ticket: Optional[str] = None
    status: HandoffStatus = HandoffStatus.PENDING
    ack_notes: Optional[str] = None
    persist_target: Optional[str] = None
    persisted_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    acknowledged_at: Optional[datetime] = None


@dataclass
class Spec:
    """A versioned markdown artifact registered with DCAM.

    Specs live anywhere in the repo, registered by path. DCAM tracks
    their content hash and the most recent decision id linked to them so
    `dcam tmux spec drift` can flag specs whose underlying decisions
    have changed since the last sync.
    """
    id: Optional[int] = None
    path: str = ""                          # relative to repo root
    title: Optional[str] = None
    content_hash: Optional[str] = None      # sha256 of file content
    epic: Optional[str] = None
    op: Optional[str] = None
    last_linked_decision_id: Optional[int] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


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
