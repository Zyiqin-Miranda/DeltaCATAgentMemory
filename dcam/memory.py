"""Memory presentation and read-only reference validation."""

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def utc_timestamp(value):
    """Normalize new aware and legacy local-time timestamps for comparison."""
    return value.astimezone(timezone.utc)


def memory_record(memory, memories=(), stale_after_days=30, now=None):
    """Machine-readable identity, provenance and advisory freshness metadata."""
    now = now or datetime.now(timezone.utc)
    age = max(0, (utc_timestamp(now) - utc_timestamp(memory.updated_at)).days)
    replacements = [m.memory_uid for m in memories
                    if m.active and m.supersedes_uid == memory.memory_uid]
    ids = Counter(m.id for m in memories)
    return {
        "memory_uid": memory.memory_uid,
        "legacy_id": memory.id,
        "legacy_id_ambiguous": ids[memory.id] > 1,
        "name": memory.name,
        "type": memory.type.value,
        "topic": memory.topic,
        "category": memory.category,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        "age_days": age,
        "stale": age >= stale_after_days,
        "active": memory.active,
        "source": memory.source,
        "source_session_id": memory.source_session_id,
        "supersedes_uid": memory.supersedes_uid,
        "superseded_by": replacements,
        "content": memory.content,
    }


def render_memory(memory, memories=(), stale_after_days=30, now=None, preview=None):
    r = memory_record(memory, memories, stale_after_days, now)
    label = f" [{memory.name}]" if memory.name else ""
    flags = []
    if not r["active"]:
        flags.append("inactive")
    if r["legacy_id_ambiguous"]:
        flags.append("legacy ID ambiguous; use handle")
    if r["stale"]:
        flags.append(f"STALE ({r['age_days']} days; verify current state)")
    if r["superseded_by"]:
        flags.append("SUPERSEDED BY " + ", ".join(r["superseded_by"]))
    source = memory.source or (
        f"session:{memory.source_session_id}" if memory.source_session_id else "unknown (legacy/unrecorded)")
    text = memory.content if preview is None else memory.content[:preview]
    lines = [
        f"{memory.memory_uid}{label} (legacy ID {memory.id}, {memory.type.value})",
        f"  created={r['created_at']} updated={r['updated_at']} "
        f"age={r['age_days']}d source={source}",
    ]
    if memory.topic or memory.category:
        lines.append(f"  topic={memory.topic or '-'} category={memory.category or '-'}")
    if memory.supersedes_uid:
        lines.append(f"  supersedes={memory.supersedes_uid}")
    if flags:
        lines.append("  " + "; ".join(flags))
    lines.append(text)
    return "\n".join(lines)


def store_record(store):
    return {
        "root": str(store.storage_root),
        "namespace": store.namespace,
        "catalog": store.catalog_backend,
        "branch": store.branch if store.catalog_backend == "branch" else None,
    }


REFERENCE_PATTERN = re.compile(
    r"(?<![\w-])(?:(?:L|CP|DEC|D|H|M)-\d+|mem-[0-9a-f]{32})(?![\w-])")


def validate_references(store, paths):
    """Check typed references in files/directories against the selected store.

    IDs are catalog-scoped. A missing reference is not proof that it never
    existed in another root, namespace or branch. This function never writes.
    """
    files = set()
    for name in paths:
        path = Path(name)
        if path.is_dir():
            files.update(p for p in path.rglob("*")
                         if p.is_file() and p.suffix.lower() == ".md")
        elif path.is_file():
            files.add(path)
        else:
            raise ValueError(f"Reference input does not exist: {path}")
    occurrences = []
    for path in sorted(files):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in REFERENCE_PATTERN.finditer(line):
                occurrences.append((str(path), line_no, match.group()))
    # Only read the referenced tables, including retired/superseded rows.
    readers = {
        "L": ("lessons", store.read_lessons),
        "CP": ("critical_points", store.read_critical_points),
        "DEC": ("decisions", store.read_decisions),
        "D": ("decisions", store.read_decisions),
        "H": ("handoffs", store.read_handoffs),
        "M": ("memories", store.read_memories),
        "mem": ("memories", store.read_memories),
    }
    rows = {}
    findings = []
    for path, line_no, reference in occurrences:
        prefix, value = reference.split("-", 1)
        table, reader = readers[prefix]
        if table not in rows:
            rows[table] = reader()
            if store.catalog.table_exists(store.namespace, table):
                raw = store._read_table(table)
                if raw is None:
                    raise ValueError(f"Cannot read {table}; do not interpret unreadable data as missing")
        matches = [r for r in rows[table]
                   if (getattr(r, "memory_uid", None) == reference if prefix == "mem"
                       else r.id == int(value))]
        state = "resolved" if len(matches) == 1 else "ambiguous" if matches else "missing"
        finding = {"file": path, "line": line_no, "reference": reference,
                   "table": table, "result": state, "matches": len(matches)}
        if len(matches) == 1:
            row = matches[0]
            status = getattr(row, "status", None)
            finding["record_status"] = status.value if status else (
                "active" if getattr(row, "active", True) else "inactive")
            if table == "memories":
                finding["memory_uid"] = row.memory_uid
        findings.append(finding)
    return {
        "store": store_record(store),
        "files_scanned": len(files),
        "references": findings,
        "missing": sum(f["result"] == "missing" for f in findings),
        "ambiguous": sum(f["result"] == "ambiguous" for f in findings),
        "note": "References are scoped to this store. Check the original root, namespace, "
                "catalog and branch for missing records; do not invent replacements.",
    }
