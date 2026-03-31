"""Tests for compact module."""

import tempfile
from pathlib import Path

from dcam.compact import compact_file, lookup, fetch_lines
from dcam.store import DeltaStore


SAMPLE_PY = '''
class AuthManager:
    """Manages authentication."""

    def login(self, user, password):
        return True

    def logout(self):
        pass

def standalone_func():
    return 42
'''


def test_compact_python_file():
    store = DeltaStore.__new__(DeltaStore)
    store.namespace = "test"
    store._counters = {}

    # Mock read_chunks/write_chunks to use in-memory list
    _chunks = []
    store.read_chunks = lambda file_path=None: [c for c in _chunks if not file_path or c.file_path == file_path]
    store.write_chunks = lambda cs: _chunks.clear() or _chunks.extend(cs)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(SAMPLE_PY)
        f.flush()
        count = compact_file(store, f.name)

    # Should find class + standalone function (methods inside class are skipped in dcam)
    assert count >= 1
    assert len(_chunks) >= 1


def test_fetch_lines():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("line1\nline2\nline3\nline4\nline5\n")
        f.flush()
        result = fetch_lines(f.name, 2, 4)

    assert "line2" in result
    assert "line4" in result
    assert "line1" not in result


def test_fetch_lines_missing_file():
    assert fetch_lines("/nonexistent/file.py", 1, 10) == ""
