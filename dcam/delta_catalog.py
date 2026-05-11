"""Delta-native catalog adapter — same interface as local_catalog/deltacat_catalog.

Uses DeltaNativeStore under the hood with partitioned, append-only storage.
Chat messages are partitioned by session_id for efficient per-session reads.
"""

from typing import Optional

import pyarrow as pa

from dcam.delta_store import DeltaNativeStore, GLOBAL_PARTITION


class DeltaNativeCatalog:
    """Catalog backed by delta-native partitioned storage.

    Tables that benefit from partitioning (chat_messages) are stored
    with session_id as partition key. Others use the global partition.
    """

    PARTITIONED_TABLES = {"chat_messages"}  # Partitioned by session_id

    def __init__(self, root: Optional[str] = None):
        self._ds: Optional[DeltaNativeStore] = None
        self._root = root

    def _get_ds(self, namespace: str) -> DeltaNativeStore:
        if self._ds is None or self._ds.namespace != namespace:
            self._ds = DeltaNativeStore(namespace=namespace, root=self._root)
        return self._ds

    def ensure_namespace(self, namespace: str):
        self._get_ds(namespace)  # Creates dirs on first write

    def table_exists(self, namespace: str, table_name: str) -> bool:
        return self._get_ds(namespace).table_exists(table_name)

    def create_table(self, namespace: str, table_name: str, schema: pa.Schema):
        self._get_ds(namespace).ensure_table(table_name)

    def read_table(self, namespace: str, table_name: str) -> Optional[pa.Table]:
        return self._get_ds(namespace).read_all(table_name)

    def write_table(self, namespace: str, table_name: str, table: pa.Table,
                    append: bool = False, partition_col: Optional[str] = None):
        ds = self._get_ds(namespace)

        if append and table_name in self.PARTITIONED_TABLES and partition_col:
            # Write to per-partition deltas
            self._write_partitioned(ds, table_name, table, partition_col)
        elif append:
            ds.append_delta(table_name, table, GLOBAL_PARTITION)
        else:
            # Replace: compact into snapshot then write as new delta
            # Clear existing by compacting to empty + writing new
            for p in ds.list_partitions(table_name):
                ds.compact_partition(table_name, p)
            if table_name in self.PARTITIONED_TABLES and partition_col:
                self._write_partitioned(ds, table_name, table, partition_col)
            else:
                ds.compact_partition(table_name, GLOBAL_PARTITION)
                ds.append_delta(table_name, table, GLOBAL_PARTITION)

    def _write_partitioned(self, ds: DeltaNativeStore, table_name: str,
                           table: pa.Table, partition_col: str):
        """Split table by partition column and write each partition separately."""
        if partition_col not in table.column_names:
            ds.append_delta(table_name, table, GLOBAL_PARTITION)
            return

        col = table.column(partition_col)
        unique_vals = col.unique().to_pylist()
        for val in unique_vals:
            mask = pa.compute.equal(col, val)
            partition_table = table.filter(mask)
            partition_key = str(val) if val else GLOBAL_PARTITION
            ds.append_delta(table_name, partition_table, partition_key)

    def read_partition(self, namespace: str, table_name: str,
                       partition: str) -> Optional[pa.Table]:
        """Read a single partition (e.g., one session's messages)."""
        return self._get_ds(namespace).read_partition(table_name, partition)

    def compact(self, namespace: str, table_name: str,
                partition: Optional[str] = None) -> int:
        """Compact deltas into snapshot. Returns rows compacted."""
        ds = self._get_ds(namespace)
        if partition:
            return ds.compact_partition(table_name, partition)
        total = 0
        for p in ds.list_partitions(table_name):
            total += ds.compact_partition(table_name, p)
        return total

    def read_at_version(self, namespace: str, table_name: str,
                        partition: str, delta_num: int) -> Optional[pa.Table]:
        """Time-travel: read state at a specific delta version."""
        return self._get_ds(namespace).read_at_delta(table_name, partition, delta_num)

    def truncate_table(self, namespace: str, table_name: str, schema: pa.Schema):
        ds = self._get_ds(namespace)
        for p in ds.list_partitions(table_name):
            ds.compact_partition(table_name, p)
