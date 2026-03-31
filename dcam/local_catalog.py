"""Lightweight local storage backend using PyArrow + Parquet files.

Replaces the full deltacat dependency with simple local parquet files.
This avoids the heavy deltacat/daft/ray dependency chain while keeping
the same DeltaStore API.
"""

import os
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

_DEFAULT_ROOT = Path.home() / ".dcam" / "tables"


class LocalCatalog:
    """Simple local parquet-based table catalog."""

    def __init__(self, root: Optional[str] = None):
        self.root = Path(root) if root else _DEFAULT_ROOT

    def _table_path(self, namespace: str, table_name: str) -> Path:
        return self.root / namespace / f"{table_name}.parquet"

    def ensure_namespace(self, namespace: str):
        (self.root / namespace).mkdir(parents=True, exist_ok=True)

    def table_exists(self, namespace: str, table_name: str) -> bool:
        return self._table_path(namespace, table_name).exists()

    def create_table(self, namespace: str, table_name: str, schema: pa.Schema):
        self.ensure_namespace(namespace)
        path = self._table_path(namespace, table_name)
        if not path.exists():
            empty = pa.table({f.name: pa.array([], type=f.type) for f in schema})
            pq.write_table(empty, path)

    def read_table(self, namespace: str, table_name: str) -> Optional[pa.Table]:
        path = self._table_path(namespace, table_name)
        if not path.exists():
            return None
        try:
            return pq.read_table(path)
        except Exception:
            return None

    def write_table(self, namespace: str, table_name: str, table: pa.Table, append: bool = False):
        path = self._table_path(namespace, table_name)
        self.ensure_namespace(namespace)
        if append and path.exists():
            existing = pq.read_table(path)
            table = pa.concat_tables([existing, table])
        pq.write_table(table, path)

    def truncate_table(self, namespace: str, table_name: str, schema: pa.Schema):
        path = self._table_path(namespace, table_name)
        empty = pa.table({f.name: pa.array([], type=f.type) for f in schema})
        pq.write_table(empty, path)
