"""Evidence freshness, correction history, and bounded agent discovery."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from dcam import cli, compact
from dcam.memory import memory_record
from dcam.models import Memory
from dcam.store import DeltaStore


@pytest.fixture(params=["local", "delta", "branch", "project"])
def store(tmp_path, monkeypatch, request):
    monkeypatch.setattr("dcam.project.is_project_root", lambda path: request.param == "project")
    backend = "local" if request.param == "project" else request.param
    return DeltaStore(storage_root=str(tmp_path / "store"), catalog_backend=backend)


def test_same_name_update_retains_history_and_stable_identity(store):
    first = store.add_project_memory("draft", name="cr", keywords=["CR-123"], source="r1")
    second = store.add_project_memory("published", name="cr", change_kind="state_transition",
                                      reason="Publication readback", source="r1-open")
    fresh = DeltaStore(storage_root=str(store.storage_root), catalog_backend=store.catalog_backend)
    current = fresh.recall_memory(first.memory_uid)
    assert second.memory_uid == first.memory_uid == current.memory_uid
    assert current.revision == 2 and current.content == "published"
    history = fresh.memory_history("cr")["history"]
    assert history[0]["content"] == "draft"
    assert history[0]["source"] == "r1"
    assert history[0]["change_kind"] == "state_transition"


def test_correction_requires_evidence_and_preserves_wrong_conclusion(store):
    old = store.add_project_memory("Decimal split required", name="decimal")
    for metadata in ({}, {"source": "design"}, {"source": "design", "reason": "Original design read"}):
        with pytest.raises(ValueError, match="requires"):
            store.revise_memory("decimal", "Grouping intentional", change_kind="correction", **metadata)
    corrected = store.revise_memory(
        old.memory_uid, "Grouping intentional", change_kind="correction", reason="Original design read",
        source="design-v126", verified_at=datetime.now(timezone.utc))
    assert corrected.history[0]["content"] == "Decimal split required"
    assert corrected.history[0]["reason"] == "Original design read"
    assert memory_record(corrected)["verification_state"] == "verified"


def test_metadata_edit_does_not_refresh_verification_and_content_change_invalidates(store):
    verified = datetime.now(timezone.utc) - timedelta(days=45)
    first = store.add_project_memory("claim", name="evidence", source="r1", verified_at=verified)
    edited = store.revise_memory("evidence", keywords=["ALIAS"])
    assert edited.verified_at == verified and edited.verification_state == "verified"
    assert memory_record(edited)["stale"]
    changed = store.revise_memory("evidence", content="new claim")
    assert changed.verified_at == verified
    assert changed.verification_state == "needs_reverification"
    assert changed.revision == 3 and len(changed.history) == 2
    assert changed.memory_uid == first.memory_uid


def test_unknown_legacy_and_review_deadline_are_explicit():
    now = datetime.now(timezone.utc)
    assert memory_record(Memory(updated_at=now))["needs_reverification"]
    verified = Memory(updated_at=now, verified_at=now, verification_state="verified",
                      review_after=now - timedelta(seconds=1))
    assert memory_record(verified, now=now)["stale"]
    assert memory_record(verified, now=now)["verification_age_days"] == 0


def test_index_alias_scope_supersession_bounds_and_no_write(store, monkeypatch):
    old = store.add_project_memory("x" * 10000, name="old", keywords=["TASK-123"], scope="catalog")
    new = store.add_project_memory("Current " + "x" * 10000, name="current", keywords=["TASK-123"],
                                   scope="catalog", supersedes=old.memory_uid)
    store.add_project_memory("TASK-123 body mention", name="other", scope="dcam")
    monkeypatch.setattr(store, "_write_table", lambda *a, **k: pytest.fail("Read wrote a table"))
    rows = store.memory_index("TASK-123", scope="catalog", summary_chars=40)
    assert [r["memory_uid"] for r in rows] == [new.memory_uid]
    assert len(rows[0]["summary"]) == 40 and "content" not in rows[0]
    assert len(store.memory_index("TASK-123", scope="catalog", include_history=True)) == 2
    assert len(store.get_session_context(query="TASK-123", scope="catalog", max_chars=300)) <= 300
    context = store.get_session_context(query="TASK-123", scope="catalog")
    assert f"{old.memory_uid} [old]" not in context
    assert context.startswith(f"{new.memory_uid} [current]")


def test_exact_alias_outranks_body_and_keywords_normalize(store):
    alias = store.add_project_memory("Long " * 1000, name="target", keywords=["CR-123", "cr-123"])
    store.add_project_memory("CR-123", name="short")
    assert store.search_memories("CR-123", limit=1)[0].memory_uid == alias.memory_uid
    assert alias.keywords == ["cr-123"]
    with pytest.raises(ValueError, match="list"):
        store.add_project_memory("bad", name="bad", keywords="not-a-list")


def test_old_keyword_follows_replacement_chain_without_returning_old_claim(store):
    first = store.add_project_memory("Incorrect assumption", name="first", keywords=["old-alias"])
    second = store.add_project_memory("Intermediate", name="second", supersedes=first.memory_uid)
    final = store.add_project_memory("Verified replacement", name="final", supersedes=second.memory_uid)
    assert [m.memory_uid for m in store.search_memories("old-alias")] == [final.memory_uid]
    assert [m.memory_uid for m in store.search_memories("first")] == [final.memory_uid]
    assert {m.memory_uid for m in store.search_memories("old-alias", include_history=True)} == {
        first.memory_uid, second.memory_uid, final.memory_uid}


def test_parallel_named_updates_retain_every_revision(tmp_path):
    import os
    import subprocess
    import sys
    root = tmp_path / "store"
    store = DeltaStore(storage_root=str(root))
    original = store.add_project_memory("initial", name="shared")
    env = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path.cwd())}
    processes = [subprocess.Popen(
        [sys.executable, "-m", "dcam.cli", "--root", str(root), "memory", "update",
         "shared", "--content", f"revision-{i}"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(5)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    current = store.recall_memory(original.memory_uid)
    contents = {item["content"] for item in current.history} | {current.content}
    assert contents == {"initial"} | {f"revision-{i}" for i in range(5)}
    assert current.revision == 6


def test_evidence_timestamps_reject_naive_future_and_missing_source(store):
    for metadata in ({"verified_at": "2026-01-01T00:00:00"},
                     {"verified_at": datetime.now(timezone.utc) + timedelta(days=1), "source": "e"},
                     {"verified_at": datetime.now(timezone.utc)}):
        with pytest.raises(ValueError):
            store.add_project_memory("bad", name="bad", **metadata)
    assert not store.read_memories()


def test_cli_roundtrip(tmp_path, monkeypatch, capsys):
    root = tmp_path / "store"

    def run(*args):
        monkeypatch.setattr("sys.argv", ["dcam", "--root", str(root), "memory", *args])
        cli.main()
        return capsys.readouterr().out

    run("add", "old", "--name", "claim", "--keyword", "alias", "--scope", "dcam")
    data = json.loads(run("index", "alias", "--scope", "dcam", "--json"))
    uid = data["memories"][0]["memory_uid"]
    run("update", uid, "--content", "corrected", "--kind", "correction", "--reason", "Source read",
        "--source", "design.md", "--verified-at", datetime.now(timezone.utc).isoformat())
    report = json.loads(run("history", "claim"))
    assert report["history"][0]["content"] == "old"
    assert report["current"]["content"] == "corrected"
    assert len(run("context", "--query", "alias", "--max-chars", "100").rstrip("\n")) <= 100


def test_failed_write_reports_failure_and_no_success(tmp_path, monkeypatch, capsys):
    store = DeltaStore(storage_root=str(tmp_path / "store"))
    monkeypatch.setattr(cli, "get_store", lambda *a, **k: store)

    def denied(*args, **kwargs):
        raise PermissionError("Store is read-only")

    monkeypatch.setattr(store, "_write_table", denied)
    monkeypatch.setattr("sys.argv", ["dcam", "memory", "add", "claim", "--name", "blocked"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    result = capsys.readouterr()
    assert "read-only" in result.err and "Stored" not in result.out
    assert store.read_memories() == []


def test_empty_index_still_validates_output_bounds(tmp_path):
    store = DeltaStore(storage_root=str(tmp_path / "store"))
    with pytest.raises(ValueError, match="summary_chars"):
        store.memory_index(summary_chars=4097)


def test_read_only_compaction_and_range_fetch_never_open_store(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.md"
    source.write_text("# Heading\nSome text\n## Next\nMore text\n")
    monkeypatch.setattr(cli, "get_store", lambda *a, **k: pytest.fail("Preview opened store"))
    monkeypatch.setattr("sys.argv", ["dcam", "compact", str(source), "--read-only"])
    cli.main()
    assert "no catalog writes" in capsys.readouterr().out
    assert all(c.chunk_id is None for c in compact.preview_file(str(source)))
    monkeypatch.setattr("sys.argv", ["dcam", "fetch", "--file", str(source),
                                   "--start-line", "2", "--end-line", "2"])
    cli.main()
    assert capsys.readouterr().out.strip() == "Some text"
    with pytest.raises(ValueError):
        compact.fetch_lines(str(source), 3, 2)


def test_mcp_registered_index_update_history(tmp_path, monkeypatch):
    from dcam import mcp_server as server
    monkeypatch.setattr(server, "_store", DeltaStore(storage_root=str(tmp_path / "store")))
    server.dcam_store_memory("old", type="project", name="claim", keywords=["alias"])
    server.dcam_update_memory("claim", content="new", change_kind="state_transition")
    assert server.dcam_memory_history("claim")["history"][0]["content"] == "old"

    async def dispatch():
        tools = await server.mcp.list_tools()
        assert {"dcam_memory_index", "dcam_update_memory", "dcam_memory_history",
                "dcam_preview_file", "dcam_fetch_file"} <= {t.name for t in tools}
        result = await server.mcp.call_tool("dcam_memory_index", {"query": "alias"})
        assert "claim" in str(result)

    asyncio.run(dispatch())
