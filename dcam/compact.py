"""File compaction — parse files into indexed chunks."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dcam.models import ChunkType, FileChunk
from dcam.store import DeltaStore

LANG_MAP = {
    ".py": "python", ".go": "go", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".java": "java", ".rs": "rust", ".rb": "ruby",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".md": "markdown",
}

PATTERNS = {
    "python": [
        (ChunkType.CLASS, re.compile(r"^class\s+(\w+)")),
        (ChunkType.FUNCTION, re.compile(r"^(?:async\s+)?def\s+(\w+)")),
    ],
    "go": [
        (ChunkType.FUNCTION, re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)")),
        (ChunkType.CLASS, re.compile(r"^type\s+(\w+)\s+(?:struct|interface)")),
    ],
    "typescript": [
        (ChunkType.CLASS, re.compile(r"^(?:export\s+)?class\s+(\w+)")),
        (ChunkType.FUNCTION, re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
        (ChunkType.FUNCTION, re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(")),
    ],
}
PATTERNS["javascript"] = PATTERNS["typescript"]


def compact_file(store: DeltaStore, file_path: str) -> int:
    """Index a file into chunks. Returns chunk count."""
    path = Path(file_path)
    if not path.is_file():
        return 0

    lines = path.read_text(errors="replace").splitlines()
    lang = LANG_MAP.get(path.suffix, "")
    pats = PATTERNS.get(lang, [])

    # Remove old chunks for this file
    existing = [c for c in store.read_chunks() if c.file_path != file_path]

    # Parse chunks
    chunks = []
    cur_start = cur_name = cur_type = None

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        for ctype, pat in pats:
            m = pat.match(stripped)
            if m:
                if cur_start is not None:
                    chunks.append(_make_chunk(store, file_path, cur_type, cur_name, cur_start, i - 1, lines))
                cur_start, cur_type, cur_name = i, ctype, m.group(1)
                break

    if cur_start is not None:
        chunks.append(_make_chunk(store, file_path, cur_type, cur_name, cur_start, len(lines), lines))

    if not chunks and lines:
        chunks.append(_make_chunk(store, file_path, ChunkType.BLOCK, path.stem, 1, len(lines), lines))

    existing.extend(chunks)
    store.write_chunks(existing)
    return len(chunks)


def compact_directory(store: DeltaStore, dir_path: str) -> int:
    count = 0
    for root, _, files in os.walk(dir_path):
        for f in files:
            if Path(f).suffix in LANG_MAP:
                try:
                    compact_file(store, os.path.join(root, f))
                    count += 1
                except Exception:
                    pass
    return count


def lookup(store: DeltaStore, symbol: str) -> List[FileChunk]:
    q = symbol.lower()
    return [c for c in store.read_chunks() if q in c.name.lower() or q in c.summary.lower()]


def fetch_lines(file_path: str, start: int, end: int) -> str:
    p = Path(file_path)
    if not p.is_file():
        return ""
    lines = p.read_text(errors="replace").splitlines()
    return "\n".join(lines[max(0, start - 1):end])


def _make_chunk(store, fpath, ctype, name, start, end, lines) -> FileChunk:
    code_lines = lines[start - 1:min(end, start + 4)]
    summary = code_lines[0].strip()[:120] if code_lines else name
    return FileChunk(
        chunk_id=store._next_id("compact_chunks"),
        file_path=fpath, chunk_type=ctype, name=name,
        summary=summary, start_line=start, end_line=end,
        last_indexed=datetime.now(),
    )
