"""DeltaCAT storage backend — ACID-compliant with versioning and time-travel.

Optional backend. Install deltacat to use:
    pip install deltacat

Usage:
    store = DeltaStore(namespace="dcam", catalog_backend="deltacat")
"""

from typing import Optional

import pyarrow as pa


class DeltaCatCatalog:
    """DeltaCAT-backed catalog with ACID commits and high-frequency writes."""

    def __init__(self, catalog_name: Optional[str] = None):
        import deltacat
        self._dc = deltacat
        self.catalog_name = catalog_name

    def ensure_namespace(self, namespace: str):
        if not self._dc.namespace_exists(namespace, catalog=self.catalog_name):
            self._dc.create_namespace(namespace, permissions={}, catalog=self.catalog_name)

    def table_exists(self, namespace: str, table_name: str) -> bool:
        return self._dc.table_exists(table_name, namespace=namespace, catalog=self.catalog_name)

    def create_table(self, namespace: str, table_name: str, schema: pa.Schema):
        self.ensure_namespace(namespace)
        if not self.table_exists(namespace, table_name):
            self._dc.create_table(
                table_name, namespace=namespace, catalog=self.catalog_name,
                schema=schema, primary_keys=set(),
            )

    def read_table(self, namespace: str, table_name: str) -> Optional[pa.Table]:
        try:
            return self._dc.read_table(
                table_name, namespace=namespace, catalog=self.catalog_name
            ).to_arrow()
        except Exception:
            return None

    def write_table(self, namespace: str, table_name: str, table: pa.Table, append: bool = False):
        mode = self._dc.TableWriteMode.APPEND if append else self._dc.TableWriteMode.REPLACE
        self._dc.write_to_table(
            table, table_name, namespace=namespace,
            catalog=self.catalog_name, mode=mode,
        )

    def truncate_table(self, namespace: str, table_name: str, schema: pa.Schema):
        self._dc.truncate_table(table_name, namespace=namespace, catalog=self.catalog_name)
