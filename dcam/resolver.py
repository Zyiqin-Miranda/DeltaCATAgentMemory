"""Context resolver — auto-index + inject relevant chunks."""

import re
from pathlib import Path
from typing import List

from dcam.compact import compact_file, lookup
from dcam.models import FileChunk
from dcam.store import DeltaStore

FILE_REFS = [
    re.compile(r"(?:in|from|file|edit|fix|update|check)\s+[`'\"]?([^\s`'\"]+\.\w{1,4})[`'\"]?", re.I),
    re.compile(r"[`'\"]([^\s`'\"]+\.\w{1,4})[`'\"]"),
    re.compile(r"(\S+/\S+\.\w{1,4})"),
]
SYMBOL_REFS = [
    re.compile(r"(?:function|class|def|func|type)\s+[`'\"]?(\w+)[`'\"]?", re.I),
    re.compile(r"`(\w{2,})`"),
]


def resolve(store: DeltaStore, message: str, project_root: str = ".", max_chunks: int = 10) -> str:
    """Parse message, auto-index files, return compact context block."""
    root = Path(project_root)

    # Auto-index referenced files
    for pat in FILE_REFS:
        for m in pat.finditer(message):
            p = root / m.group(1)
            if p.is_file():
                compact_file(store, str(p))

    # Lookup symbols
    chunks: List[FileChunk] = []
    for pat in SYMBOL_REFS:
        for m in pat.finditer(message):
            sym = m.group(1)
            if len(sym) >= 2:
                chunks.extend(lookup(store, sym))

    # Deduplicate
    seen = set()
    unique = []
    for c in chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            unique.append(c)

    if not unique:
        return ""

    lines = ["## Indexed Code Context\n"]
    by_file = {}
    for c in unique[:max_chunks]:
        by_file.setdefault(c.file_path, []).append(c)
    for fpath, fchunks in by_file.items():
        lines.append(f"### {Path(fpath).name}")
        for c in fchunks:
            lines.append(f"- `{c.name}` ({c.chunk_type.value}, L{c.start_line}-{c.end_line}): {c.summary}")
        lines.append("")
    return "\n".join(lines)
