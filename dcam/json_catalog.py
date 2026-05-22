"""JSON-as-primary catalog for project-scoped DCAM storage.

Used when the active root is a `.dcam/` directory inside a repo (i.e.
discovered via env or filesystem walk, not the `~/.dcam/` global fallback).

Design rationale: see ``dcam/project.py`` for the layout. Briefly: three
tables (decisions, lessons, chat_sessions) are committed alongside source
code, so they live as JSON for diff/merge sanity. Everything else
(chat_messages, memories, compact_*) stays in parquet under `tables/`
because those are large, ephemeral, and gitignored.

The catalog presents the same interface as ``LocalCatalog`` so DeltaStore
doesn't care which one it's talking to.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq


# Tables stored as flat JSON files at the root of `.dcam/`.
JSON_TABLES = {"decisions", "lessons", "chat_sessions", "critical_points",
               "review_requests", "reviews", "handoffs", "specs"}

# Map our DCAM table names to JSON file names.
JSON_FILENAMES = {
    "decisions": "decisions.json",
    "lessons": "lessons.json",
    "chat_sessions": "sessions.json",
    "critical_points": "critical_points.json",
    "review_requests": "review_requests.json",
    "reviews": "reviews.json",
    "handoffs": "handoffs.json",
    "specs": "specs.json",
}


def _arrow_to_python(value: Any) -> Any:
    """Convert an arrow scalar to a JSON-serializable Python value.

    Most values come through ``Table.column(c)[i].as_py()`` already as
    Python primitives; this is a defensive layer for anything else.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _table_to_records(table: pa.Table) -> List[Dict[str, Any]]:
    cols = table.column_names
    out = []
    for i in range(table.num_rows):
        out.append({c: _arrow_to_python(table.column(c)[i].as_py()) for c in cols})
    return out


def _records_to_table(records: List[Dict[str, Any]],
                      schema: Optional[pa.Schema]) -> pa.Table:
    """Materialize JSON records back into an arrow table.

    If a schema is provided we project records to that schema so missing
    fields default to None and column order is stable.
    """
    if not records:
        if schema is not None:
            return pa.table({f.name: pa.array([], type=f.type) for f in schema})
        return pa.table({})

    if schema is not None:
        cols = {f.name: [] for f in schema}
        types = {f.name: f.type for f in schema}
        for r in records:
            for name in cols:
                cols[name].append(r.get(name))
        return pa.table({n: pa.array(v, type=types[n]) for n, v in cols.items()})

    # No schema — best-effort
    cols = {}
    for r in records:
        for k, v in r.items():
            cols.setdefault(k, []).append(v)
    return pa.table(cols)


class JsonCatalog:
    """Catalog that splits storage between project-root JSON files and
    namespace-scoped parquet files.

    The three "committable" tables (decisions, lessons, chat_sessions)
    write to ``<root>/decisions.json`` etc. — flat, root-level, namespace
    is recorded inside each row. All other tables fall through to parquet
    under ``<root>/tables/<namespace>/<table>.parquet``, matching the
    historical LocalCatalog layout.

    The namespace argument is ignored for JSON tables (one project, one
    review surface). It still scopes parquet tables.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.parquet_root = self.root / "tables"
        # Schemas registered on create_table so we can re-project on read.
        self._schemas: Dict[str, pa.Schema] = {}

    # --- Path helpers ---

    def _json_path(self, table_name: str) -> Path:
        return self.root / JSON_FILENAMES[table_name]

    def _parquet_path(self, namespace: str, table_name: str) -> Path:
        return self.parquet_root / namespace / f"{table_name}.parquet"

    # --- Catalog API (mirrors LocalCatalog) ---

    def ensure_namespace(self, namespace: str):
        # JSON files live at root, but parquet still needs a namespace dir.
        (self.parquet_root / namespace).mkdir(parents=True, exist_ok=True)
        # Make sure root exists too so JSON writes don't fail on a fresh repo.
        self.root.mkdir(parents=True, exist_ok=True)

    def table_exists(self, namespace: str, table_name: str) -> bool:
        if table_name in JSON_TABLES:
            return self._json_path(table_name).exists()
        return self._parquet_path(namespace, table_name).exists()

    def create_table(self, namespace: str, table_name: str,
                     schema: pa.Schema):
        self.ensure_namespace(namespace)
        self._schemas[table_name] = schema
        if table_name in JSON_TABLES:
            path = self._json_path(table_name)
            if not path.exists():
                path.write_text("[]\n")
            return
        path = self._parquet_path(namespace, table_name)
        if not path.exists():
            empty = pa.table({f.name: pa.array([], type=f.type) for f in schema})
            pq.write_table(empty, path)

    def read_table(self, namespace: str, table_name: str) -> Optional[pa.Table]:
        if table_name in JSON_TABLES:
            path = self._json_path(table_name)
            if not path.exists():
                return None
            try:
                records = json.loads(path.read_text() or "[]")
            except json.JSONDecodeError:
                return None
            schema = self._schemas.get(table_name)
            return _records_to_table(records, schema)
        path = self._parquet_path(namespace, table_name)
        if not path.exists():
            return None
        try:
            return pq.read_table(path)
        except Exception:
            return None

    def write_table(self, namespace: str, table_name: str, table: pa.Table,
                    append: bool = False):
        if table_name in JSON_TABLES:
            path = self._json_path(table_name)
            self.ensure_namespace(namespace)
            existing = []
            if append and path.exists():
                try:
                    existing = json.loads(path.read_text() or "[]")
                except json.JSONDecodeError:
                    existing = []
            new_records = _table_to_records(table)
            records = existing + new_records if append else new_records
            path.write_text(json.dumps(records, indent=2, default=str) + "\n")
            return
        path = self._parquet_path(namespace, table_name)
        self.ensure_namespace(namespace)
        if append and path.exists():
            existing = pq.read_table(path)
            table = pa.concat_tables([existing, table])
        pq.write_table(table, path)

    def truncate_table(self, namespace: str, table_name: str,
                       schema: pa.Schema):
        if table_name in JSON_TABLES:
            self._json_path(table_name).write_text("[]\n")
            return
        path = self._parquet_path(namespace, table_name)
        empty = pa.table({f.name: pa.array([], type=f.type) for f in schema})
        pq.write_table(empty, path)
