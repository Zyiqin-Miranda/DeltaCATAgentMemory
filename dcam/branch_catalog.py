"""Branch-aware catalog adapter — same interface as other catalogs."""

from typing import Optional

import pyarrow as pa

from dcam.branch_store import BranchStore, MAIN_BRANCH


class BranchCatalog:
    """Catalog that writes to a feature branch, reads from branch + main merged."""

    PARTITIONED_TABLES = {"chat_messages"}

    def __init__(self, branch: str = MAIN_BRANCH, root: Optional[str] = None):
        self._branch = branch
        self._root = root
        self._bs: Optional[BranchStore] = None

    def _get_bs(self, namespace: str) -> BranchStore:
        if self._bs is None or self._bs.namespace != namespace:
            self._bs = BranchStore(namespace=namespace, branch=self._branch, root=self._root)
        return self._bs

    def ensure_namespace(self, namespace: str):
        self._get_bs(namespace)

    def table_exists(self, namespace: str, table_name: str) -> bool:
        return self._get_bs(namespace).table_exists(table_name)

    def create_table(self, namespace: str, table_name: str, schema: pa.Schema):
        self._get_bs(namespace).ensure_table(table_name)

    def read_table(self, namespace: str, table_name: str) -> Optional[pa.Table]:
        return self._get_bs(namespace).read_all(table_name)

    def write_table(self, namespace: str, table_name: str, table: pa.Table,
                    append: bool = False, partition_col: Optional[str] = None):
        bs = self._get_bs(namespace)
        if append and table_name in self.PARTITIONED_TABLES and partition_col:
            col = table.column(partition_col)
            for val in col.unique().to_pylist():
                mask = pa.compute.equal(col, val)
                bs.append_delta(table_name, table.filter(mask), str(val) or "__global__")
        elif append:
            bs.append_delta(table_name, table)
        else:
            bs.append_delta(table_name, table)

    def truncate_table(self, namespace: str, table_name: str, schema: pa.Schema):
        pass  # No-op for branch store
