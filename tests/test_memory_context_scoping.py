"""Regression coverage for keeping unrelated memory out of session prompts."""
from pathlib import Path
from datetime import datetime, timezone

import pytest

from dcam.models import Memory, MemoryType
from dcam.store import DeltaStore


@pytest.fixture
def store(tmp_path):
    result = DeltaStore(storage_root=str(tmp_path / ".dcam"), catalog_backend="local")
    result.init_tables()
    return result


def seed(store):
    memories = [
        Memory(id=1, type=MemoryType.PROJECT, content="UNRELATED_PROJECT", name="other"),
        Memory(id=2, type=MemoryType.PROJECT, content="TASK_A_HANDOFF", source_session_id="task-a"),
        Memory(id=3, type=MemoryType.SHORT_TERM, content="TASK_A_SESSION", source_session_id="task-a"),
        Memory(id=4, type=MemoryType.SHORT_TERM, content="TASK_B_SESSION", source_session_id="task-b"),
        Memory(id=5, type=MemoryType.SHORT_TERM, content="INACTIVE", source_session_id="task-a", active=False),
    ]
    store.write_memories(memories)


def test_unselected_context_does_not_read_or_inject_archive(store, monkeypatch):
    def unexpected_read():
        raise AssertionError("unselected context must not read the archive")
    monkeypatch.setattr(store, "read_memories", unexpected_read)
    assert store.get_session_context() == ""


def test_session_context_includes_own_handoff_and_excludes_other_tasks(store):
    seed(store)
    context = store.get_session_context("task-a")
    assert "TASK_A_HANDOFF" in context and "TASK_A_SESSION" in context
    assert all(s not in context for s in ("UNRELATED_PROJECT", "TASK_B_SESSION", "INACTIVE"))
    assert store.get_session_context("unknown") == ""


def test_mcp_context_requires_selection_and_excludes_unrelated_records(store, monkeypatch):
    from dcam import mcp_server
    seed(store)
    monkeypatch.setattr(mcp_server, "_store", store)
    assert "Select a session" in mcp_server.dcam_project_memory()
    context = mcp_server.dcam_project_memory(session_id="task-a")
    assert "TASK_A_SESSION" in context and "TASK_B_SESSION" not in context


def test_interactive_resume_injects_only_the_selected_session(store, monkeypatch):
    from dcam import store as store_module
    from dcam.interactive import _build_context_prompt
    from dcam.models import ChatMessage, MessageRole
    seed(store)
    monkeypatch.setattr(store_module, "DeltaStore", lambda: store)
    message = ChatMessage(session_id="task-a", role=MessageRole.USER, content="continue")
    prompt = _build_context_prompt("resume", [message])
    assert "TASK_A_SESSION" in prompt and "TASK_A_HANDOFF" in prompt
    assert "TASK_B_SESSION" not in prompt and "UNRELATED_PROJECT" not in prompt


def test_new_claude_integration_does_not_request_unselected_history(tmp_path):
    from dcam.claude_code import install_claude_code_hook
    install_claude_code_hook(str(tmp_path))
    instructions = (tmp_path / "CLAUDE.md").read_text()
    assert "dcam project path" in instructions
    assert "only matching" in instructions and "--session-id" in instructions
    assert "dcam claude context" not in instructions


def test_session_context_limits_size_and_does_not_write(store):
    seed(store)
    before = {p: p.read_bytes() for p in Path(store.storage_root).rglob("*.parquet")}
    context = store.get_session_context("task-a", max_chars=200)
    assert len(context) <= 200 and "Context truncated" in context
    assert store.get_session_context("task-a", max_chars=1) == "\n"
    assert store.get_session_context("task-a", limit=1).count("TASK_A_") == 1
    assert before == {p: p.read_bytes() for p in Path(store.storage_root).rglob("*.parquet")}


def test_session_context_orders_legacy_and_aware_timestamps(store, monkeypatch):
    old = Memory(content="OLDER", source_session_id="task-a",
                 updated_at=datetime(2026, 1, 1))
    new = Memory(content="NEWER", source_session_id="task-a",
                 updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    monkeypatch.setattr(store, "read_memories", lambda: [old, new])
    context = store.get_session_context("task-a", limit=1)
    assert "NEWER" in context and "OLDER" not in context


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"max_chars": 0}])
def test_invalid_context_budget_is_rejected(store, kwargs):
    with pytest.raises(ValueError, match="positive"):
        store.get_session_context("task-a", **kwargs)
