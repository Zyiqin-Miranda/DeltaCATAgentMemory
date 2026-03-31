"""Tests for dcam models and store."""

from datetime import datetime

from dcam.models import (
    ChatMessage, ChatSession, ChunkType, FileChunk,
    Memory, MemoryType, MessageRole,
)


def test_memory_defaults():
    m = Memory(content="test fact")
    assert m.type == MemoryType.SEMANTIC
    assert m.active is True
    assert m.reinforcement_count == 1


def test_chat_message_defaults():
    msg = ChatMessage(session_id="abc", content="hello")
    assert msg.role == MessageRole.USER
    assert msg.session_id == "abc"


def test_chat_session_defaults():
    s = ChatSession(session_id="s1", title="Test")
    assert s.ended_at is None
    assert s.message_count == 0
    assert s.beads_issue_id is None


def test_file_chunk_defaults():
    c = FileChunk(chunk_id=1, file_path="test.py", name="foo")
    assert c.chunk_type == ChunkType.BLOCK
    assert c.start_line == 0


def test_memory_types():
    assert MemoryType.SEMANTIC.value == "semantic"
    assert MemoryType.EPISODIC.value == "episodic"
    assert MemoryType.PROCEDURAL.value == "procedural"
    assert MemoryType.SHORT_TERM.value == "short_term"
