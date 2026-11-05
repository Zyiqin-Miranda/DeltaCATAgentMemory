"""Markdown headings, code fences, preambles and actionable chunk handles."""

import pytest

from dcam.compact import compact_file, fetch_lines
from dcam.models import ChunkType
from dcam.store import DeltaStore


def test_markdown_sections_cover_file_and_ignore_fences(tmp_path):
    store = DeltaStore(storage_root=str(tmp_path / "store"))
    path = tmp_path / "guide.md"
    text = ("preamble\n# Guide\nintro\n## Search\nlookup\n```python\n"
            "# not a heading\n````\n~~~\n## also not a heading\n~~~\n"
            "Details\n=======\ntext\n### Next ###\nend\n")
    path.write_text(text)
    assert compact_file(store, str(path)) == 5
    chunks = store.read_chunks(str(path))
    assert [c.name for c in chunks] == ["guide (preamble)", "Guide", "Search", "Details", "Next"]
    assert all(c.chunk_type == ChunkType.SECTION for c in chunks)
    covered = [i for c in chunks for i in range(c.start_line, c.end_line + 1)]
    assert covered == list(range(1, len(text.splitlines()) + 1))
    search = chunks[2]
    assert "# not a heading" in fetch_lines(str(path), search.start_line, search.end_line)
    old_ids = {c.chunk_id for c in chunks}
    compact_file(store, str(path))
    assert len(store.read_chunks(str(path))) == 5
    assert not old_ids.intersection(c.chunk_id for c in store.read_chunks(str(path)))


@pytest.mark.parametrize("text,names", [
    ("# A\n    # indented code\n## B\n", ["A", "B"]),
    ("# A\n```text\n## unclosed fence\n", ["A"]),
    ("---\ntitle: test\n---\n# A\n", ["guide (preamble)", "A"]),
    ("hello without headings\n", ["guide"]),
    ("Title\n-----\nbody\n", ["Title"]),
    ("# Duplicate\ntext\n# Duplicate\ntext\n", ["Duplicate", "Duplicate"]),
    ("#not-a-heading\n## B\n", ["guide (preamble)", "B"]),
    ("# A\n```\ncode\n```\n---\n", ["A"]),
    ("", []),
])
def test_markdown_edge_cases(tmp_path, text, names):
    store = DeltaStore(storage_root=str(tmp_path / "store"))
    path = tmp_path / "guide.MD"
    path.write_text(text)
    compact_file(store, str(path))
    assert [c.name for c in store.read_chunks(str(path))] == names


def test_markdown_cli_shows_fetchable_ids(tmp_path, monkeypatch, capsys):
    from dcam import cli
    path = tmp_path / "guide.md"
    path.write_text("# Guide\n## Topic\nbody\n")
    root = str(tmp_path / "store")
    monkeypatch.setattr("sys.argv", ["dcam", "--root", root, "compact", str(path)])
    cli.main()
    assert "#" in capsys.readouterr().out
    store = DeltaStore(storage_root=root)
    topic = store.read_chunks(str(path))[1]
    monkeypatch.setattr("sys.argv", ["dcam", "--root", root, "fetch", str(topic.chunk_id)])
    cli.main()
    result = capsys.readouterr().out
    assert "## Topic\nbody" in result
    assert "\n# Guide\n" not in result
