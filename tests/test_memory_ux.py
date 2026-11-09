"""Regression coverage for retrieval, legacy identity and read-only validation."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from dcam import cli
from dcam.claude_code import get_recent_context
from dcam.memory import memory_record, render_memory, validate_references
from dcam.models import (
    ChatMessage, ChatSession, CriticalPoint, CriticalPointStatus,
    Decision, DecisionStatus, Handoff, Lesson, Memory, MemoryType,
)
from dcam.store import DeltaStore, MEMORY_SCHEMA


@pytest.fixture(params=["global", "project"])
def store(tmp_path, monkeypatch, request):
    monkeypatch.setattr("dcam.project.is_project_root", lambda path: request.param == "project")
    return DeltaStore(storage_root=str(tmp_path / "store"))


def run_cli(monkeypatch, capsys, root, *args):
    monkeypatch.setattr("sys.argv", ["dcam", "--root", str(root), *args])
    cli.main()
    return capsys.readouterr()


def seed_legacy(store, backend="local"):
    fields = {"memory_uid", "source", "supersedes_uid"}
    old_schema = pa.schema([f for f in MEMORY_SCHEMA if f.name not in fields])
    rows = [{
        "id": 18, "type": "project", "name": name, "category": None,
        "topic": "audit", "content": name, "created_at": f"2026-06-0{i}T12:00:00",
        "updated_at": f"2026-06-0{i}T12:00:00", "reinforcement_count": 1,
        "source_session_id": None, "active": True,
    } for i, name in enumerate(("first", "second"), 1)]
    store._write_table("memories", pa.Table.from_pylist(rows, schema=old_schema))
    return rows


def test_explicit_root_isolation(tmp_path):
    first = DeltaStore(storage_root=str(tmp_path / "one"))
    second = DeltaStore(storage_root=str(tmp_path / "two"))
    first.add_project_memory("private to first", name="one")
    assert second.read_memories() == []
    assert (tmp_path / "one/tables/dcam/memories.parquet").exists()
    assert not (tmp_path / "two").exists()


def test_stale_store_counter_does_not_reuse_memory_id(tmp_path):
    root = str(tmp_path / "store")
    stale = DeltaStore(storage_root=root)
    writer = DeltaStore(storage_root=root)
    writer.add_project_memory("a", name="a")
    writer.add_project_memory("b", name="b")
    third = stale.add_project_memory("c", name="c")
    assert third.id == 3
    assert [m.id for m in stale.read_memories()] == [1, 2, 3]


def test_message_counter_reread_does_not_reuse_id(store):
    store.append_message(ChatMessage(session_id="s", content="one"))
    store._counters["chat_messages"] = 1
    store.read_messages()
    store.append_message(ChatMessage(session_id="s", content="two"))
    assert [m.id for m in store.read_messages()] == [1, 2]


def test_legacy_handles_stable_read_only_and_integer_recall_rejects_collision(store):
    seed_legacy(store)
    path = store.storage_root / "tables/dcam/memories.parquet"
    before = path.read_bytes()
    first = store.read_memories()
    second = store.read_memories()
    assert [m.memory_uid for m in first] == [m.memory_uid for m in second]
    assert len({m.memory_uid for m in first}) == 2
    assert path.read_bytes() == before
    assert store.recall_memory(first[0].memory_uid).name == "first"
    with pytest.raises(ValueError, match="Ambiguous"):
        store.recall_memory("18")
    added = store.add_project_memory("new", name="new")
    assert added.id == 19
    assert [m.id for m in store.read_memories()] == [18, 18, 19]
    assert store.recall_memory(first[0].memory_uid).name == "first"


def test_identical_legacy_identity_does_not_conflate_current_state_facts(store):
    rows = seed_legacy(store)
    rows[1] = {**rows[0], "content": "different fact with same old identity"}
    old_schema = pa.schema([f for f in MEMORY_SCHEMA
                            if f.name not in {"memory_uid", "source", "supersedes_uid"}])
    store._write_table("memories", pa.Table.from_pylist(rows, schema=old_schema))
    memories = store.read_memories()
    assert len(memories) == 2 and len({m.memory_uid for m in memories}) == 2
    assert [m.memory_uid for m in store.read_memories()] == [m.memory_uid for m in memories]
    store.add_project_memory("third", name="third")
    assert len(store.read_memories()) == 3


@pytest.mark.parametrize("backend", ["local", "delta", "branch"])
def test_legacy_schema_evolution_and_updates(tmp_path, backend):
    store = DeltaStore(storage_root=str(tmp_path / "store"), catalog_backend=backend)
    seed_legacy(store)
    uid = store.recall_by_name("first").memory_uid
    store.add_project_memory("updated", name="first", source="audit.md", topic="migration")
    fresh = DeltaStore(storage_root=str(tmp_path / "store"), catalog_backend=backend)
    memories = fresh.read_project_memories()
    assert len(memories) == 2
    assert fresh.recall_memory(uid).content == "updated"
    assert fresh.recall_memory(uid).source == "audit.md"
    assert fresh.recall_memory(uid).topic == "migration"


def test_project_mode_memory(tmp_path):
    from dcam.project import init_project
    root = init_project(str(tmp_path), force=True)
    store = DeltaStore(storage_root=str(root))
    memory = store.add_project_memory("project memory", name="project", source="code.md")
    store.write_lessons([Lesson(id=1, content="project lesson")])
    assert (root / "lessons.json").exists()
    fresh = DeltaStore(storage_root=str(root))
    assert fresh.recall_memory(memory.memory_uid).source == "code.md"
    assert fresh.read_lessons()[0].content == "project lesson"
    assert store.is_project_mode
    assert store.catalog.parquet_root == store.storage_root / "tables"


def test_search_matches_name_topic_category_source_and_filters(store):
    a = store.add_project_memory("unrelated prose", name="allowlist", topic="migration",
                                 category="schema", source="evidence.md")
    b = store.add_project_memory("schema migration", name="other", topic="ops")
    for query in ("allowlist", "migration", "schema", "evidence"):
        assert a.memory_uid in {m.memory_uid for m in store.search_memories(query)}
    assert store.search_memories("migration", topic="migration", category="schema") == [a]
    assert len(store.search_memories("migration", limit=1)) == 1
    b.active = False
    store.write_memories([a, b])
    assert store.search_memories("schema") == [a]
    assert store.search_memories("   ") == []
    with pytest.raises(ValueError, match="positive"):
        store.search_memories("schema", limit=0)


def test_search_projects_only_and_substring_backend(store):
    project = store.add_project_memory("alpha beta", name="project")
    semantic = Memory(id=2, type=MemoryType.SEMANTIC, content="alpha beta")
    store.write_memories([project, semantic])
    assert len(store.search_memories("alpha beta")) == 2
    assert store.search_memories("alpha beta", project_only=True) == [project]
    from dcam.search import get_backend
    store.search = get_backend("substring")
    assert store.search_memories("alpha beta", project_only=True) == [project]
    assert store.search_memories("beta alpha") == []


def test_source_and_supersession_round_trip_and_context_warnings(store):
    old = store.add_project_memory("old current status", name="old", source="old.md")
    new = store.add_project_memory("replacement", name="new", source="new.md", supersedes="old")
    report = memory_record(old, store.read_memories())
    assert report["superseded_by"] == [new.memory_uid]
    assert new.supersedes_uid == old.memory_uid
    assert store.get_session_context() == ""
    assert "SUPERSEDED BY" in render_memory(old, store.read_memories())
    assert "source=old.md" in render_memory(old)
    with pytest.raises(ValueError, match="cannot supersede itself"):
        store.add_project_memory("bad", name="old", supersedes="old")
    with pytest.raises(ValueError, match="cycle"):
        store.add_project_memory("bad", name="old", supersedes="new")
    with pytest.raises(ValueError, match="No memory"):
        store.add_project_memory("bad", name="third", supersedes="missing")
    assert store.recall_by_name("old").content == "old current status"


def test_freshness_timezone_safe_and_legacy_provenance_unknown():
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    memory = Memory(id=1, created_at=now - timedelta(days=100),
                    updated_at=now - timedelta(days=31))
    r = memory_record(memory, now=now)
    assert r["stale"] and r["age_days"] == 31
    assert r["source"] is None
    assert "unknown" in render_memory(memory, now=now)
    memory.updated_at = now.replace(tzinfo=None)
    assert isinstance(memory_record(memory, now=now)["age_days"], int)


def test_cli_search_json_recall_handle_raw_and_metadata(store, monkeypatch, capsys):
    memory = store.add_project_memory("decimal partition schema", name="decimal",
                                      topic="allowlist", source="code.md")
    result = run_cli(monkeypatch, capsys, store.storage_root, "memory", "search",
                     "decimal", "--topic", "allowlist", "--json")
    data = json.loads(result.out)
    assert data["store"]["root"] == str(store.storage_root)
    assert data["memories"][0]["memory_uid"] == memory.memory_uid
    result = run_cli(monkeypatch, capsys, store.storage_root, "memory", "recall",
                     memory.memory_uid)
    assert "updated=" in result.out and "source=code.md" in result.out
    result = run_cli(monkeypatch, capsys, store.storage_root, "memory", "recall",
                     "decimal", "--raw")
    assert result.out.strip() == memory.content
    result = run_cli(monkeypatch, capsys, store.storage_root, "memory", "list", "--json")
    assert json.loads(result.out)["memories"][0]["source"] == "code.md"


def test_cli_collision_error_missing_and_invalid_limit(store, monkeypatch, capsys):
    seed_legacy(store)
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, capsys, store.storage_root, "memory", "recall", "18")
    assert exc.value.code == 2
    assert "Ambiguous" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, capsys, store.storage_root, "memory", "recall", "unknown")
    assert exc.value.code == 1
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, capsys, store.storage_root, "memory", "search", "x", "--limit", "-1")
    assert exc.value.code == 2


def test_validate_refs_resolves_all_types_and_reports_missing_ambiguous_read_only(store, tmp_path):
    seed_legacy(store)
    memory = store.recall_by_name("first")
    store.write_lessons([Lesson(id=19, content="lesson")])
    store.write_critical_points([CriticalPoint(id=8, status=CriticalPointStatus.RETIRED)])
    store.write_decisions([Decision(id=2, status=DecisionStatus.SUPERSEDED)])
    store.write_handoffs([Handoff(id=3)])
    path = tmp_path / "MEMORY.md"
    path.write_text(f"L-19 CP-8 DEC-2 D-2 H-3 M-18 {memory.memory_uid} L-999\n"
                    "notL-1 CP-8-extra CR-123")
    before = {p: p.read_bytes() for p in store.storage_root.rglob("*.parquet")}
    report = validate_references(store, [path])
    assert report["missing"] == 1 and report["ambiguous"] == 1
    assert len(report["references"]) == 8
    assert report["references"][1]["record_status"] == "retired"
    assert report["references"][2]["record_status"] == "superseded"
    assert {p: p.read_bytes() for p in store.storage_root.rglob("*.parquet")} == before
    assert report["store"]["catalog"] == "local"
    assert report["store"]["root"] == str(store.storage_root)


def test_validate_refs_strict_json_and_missing_input(store, tmp_path, monkeypatch, capsys):
    path = tmp_path / "MEMORY.md"
    path.write_text("L-999")
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, capsys, store.storage_root, "memory", "validate-refs",
                str(path), "--json", "--strict")
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["missing"] == 1
    with pytest.raises(ValueError, match="does not exist"):
        validate_references(store, [tmp_path / "missing.md"])


def test_validate_refs_unreadable_table_is_error(store, tmp_path):
    path = tmp_path / "MEMORY.md"
    path.write_text("L-1")
    corrupt = (store.storage_root / "lessons.json" if store.is_project_mode
               else store.storage_root / "tables/dcam/lessons.parquet")
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not parquet")
    with pytest.raises(ValueError, match="Cannot read"):
        validate_references(store, [path])


def test_history_sorted_by_activity_warns_stale_and_zero_messages(store):
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    older_start = ChatSession(session_id="older", title="older start",
                              started_at=now - timedelta(days=100))
    newer_start = ChatSession(session_id="newer", title="newer start",
                              started_at=now - timedelta(days=80))
    store.write_sessions([older_start, newer_start])
    store.append_message(ChatMessage(session_id="older", timestamp=now - timedelta(days=1),
                                     content="recent activity"))
    store.append_message(ChatMessage(session_id="newer", timestamp=now - timedelta(days=70),
                                     content="stale content"))
    context = get_recent_context(store, limit=1, now=now)
    assert "older start" in context and "recent activity" in context
    assert "newer start" not in context and "STALE:" not in context
    context = get_recent_context(store, limit=2, max_messages_per_session=0, now=now)
    assert "STALE:" in context and "historical" in context
    assert "stale content" not in context and "recent activity" not in context


def test_parallel_cli_additions_are_preserved_with_distinct_ids(tmp_path):
    root = tmp_path / "parallel-store"
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "dcam.cli", "--root", str(root), "memory",
             "add", f"content {i}", "--name", f"dev-{i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "DCAM_ROOT": str(root)})
        for i in range(8)
    ]
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, (out, err)
    memories = DeltaStore(storage_root=str(root)).read_project_memories()
    assert len(memories) == 8
    assert len({m.id for m in memories}) == 8
    assert len({m.memory_uid for m in memories}) == 8


def test_mcp_memory_tools_and_chunk_handles(store, tmp_path, monkeypatch):
    from dcam import mcp_server
    monkeypatch.setattr(mcp_server, "_store", store)
    stored = mcp_server.dcam_store_memory(
        "topic-only-match", type="project", name="named", topic="allowlist", source="code.md")
    memory = store.recall_by_name("named")
    assert memory.memory_uid in stored
    assert memory.memory_uid in mcp_server.dcam_search_memories("allowlist")
    assert "source=code.md" in mcp_server.dcam_recall_memory(memory.memory_uid)
    assert "Select a session" in mcp_server.dcam_project_memory()
    assert "updated=" in mcp_server.dcam_recall_memory(memory.memory_uid)
    path = tmp_path / "guide.md"
    path.write_text("# Root\n## Topic\nbody\n")
    assert "#" in mcp_server.dcam_compact(str(path))
    assert "#" in mcp_server.dcam_lookup("Topic")
    chunk = store.read_chunks(str(path))[1]
    assert "body" in mcp_server.dcam_fetch_chunk(chunk.chunk_id)
    refs = tmp_path / "refs.md"
    refs.write_text(memory.memory_uid)
    assert mcp_server.dcam_validate_references([str(refs)])["missing"] == 0


def test_registered_mcp_tool_dispatch(store, tmp_path, monkeypatch):
    from dcam import mcp_server
    monkeypatch.setattr(mcp_server, "_store", store)
    memory = store.add_project_memory("dispatch check", name="dispatch", topic="allowlist")
    result = asyncio.run(mcp_server.mcp.call_tool(
        "dcam_search_memories", {"query": "allowlist", "limit": 1}))
    content = result[0] if isinstance(result, tuple) else result
    assert memory.memory_uid in "\n".join(getattr(c, "text", "") for c in content)
    path = tmp_path / "refs.md"
    path.write_text(memory.memory_uid)
    result = asyncio.run(mcp_server.mcp.call_tool(
        "dcam_validate_references", {"paths": [str(path)]}))
    content = result[0] if isinstance(result, tuple) else result
    report = json.loads("\n".join(getattr(c, "text", "") for c in content))
    assert report["missing"] == 0 and report["references"][0]["result"] == "resolved"


def test_mcp_serve_respects_selected_store(tmp_path, monkeypatch, capsys):
    from dcam import mcp_server
    original = mcp_server._store
    monkeypatch.setattr(mcp_server, "_store", original)
    monkeypatch.setattr(mcp_server, "run_server", lambda: None)
    root = tmp_path / "selected"
    run_cli(monkeypatch, capsys, root, "--namespace", "team", "serve")
    assert mcp_server._store.storage_root == root
    assert mcp_server._store.namespace == "team"


def test_reference_validation_does_not_create_missing_branch(tmp_path):
    root = tmp_path / "branch-store"
    path = tmp_path / "refs.md"
    path.write_text("L-19")
    store = DeltaStore(storage_root=str(root), catalog_backend="branch", branch="new")
    report = validate_references(store, [path])
    assert report["store"]["branch"] == "new" and report["missing"] == 1
    assert not root.exists()
    store.add_project_memory("first write", name="first")
    assert (root / "delta/dcam/__branches__.json").exists()
