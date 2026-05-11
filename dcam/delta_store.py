"""Delta-native storage for dcam — partitioned by session, append-only deltas.

Storage layout:
    ~/.dcam/delta/
      └── {namespace}/
          └── {table}/
              ├── __manifest__.json          # Table metadata + stream version
              ├── _global/                   # Global partition
              │   ├── delta_000001.parquet
              │   └── delta_000002.parquet
              └── {session_id}/              # Session partition
                  ├── delta_000001.parquet
                  └── delta_000002.parquet

Each delta is an append-only parquet file. Current state is materialized
by replaying deltas in order. Compaction merges old deltas into a snapshot.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

GLOBAL_PARTITION = "__global__"


class DeltaManifest:
    """Tracks table metadata and stream version."""
    def __init__(self, path: Path):
        self._path = path / "__manifest__.json"
        self.version = 0
        self.created_at = datetime.now().isoformat()
        self._load()

    def _load(self):
        if self._path.exists():
            data = json.loads(self._path.read_text())
            self.version = data.get("version", 0)
            self.created_at = data.get("created_at", self.created_at)

    def bump(self) -> int:
        self.version += 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(),
        }))
        return self.version


class DeltaNativeStore:
    """Partitioned, delta-based storage using local parquet files.

    Maps DeltaCAT concepts to local filesystem:
    - Namespace → top-level directory
    - Table → subdirectory
    - Partition → subdirectory per session_id (or __global__)
    - Delta → individual parquet file (append-only)
    - Stream → ordered sequence of deltas (version tracked in manifest)
    """

    def __init__(self, namespace: str = "dcam", root: Optional[str] = None):
        self.namespace = namespace
        self.root = Path(root) if root else Path.home() / ".dcam" / "delta"
        self._manifests: Dict[str, DeltaManifest] = {}

    def _table_dir(self, table: str) -> Path:
        return self.root / self.namespace / table

    def _partition_dir(self, table: str, partition: str) -> Path:
        return self._table_dir(table) / partition

    def _manifest(self, table: str) -> DeltaManifest:
        if table not in self._manifests:
            self._manifests[table] = DeltaManifest(self._table_dir(table))
        return self._manifests[table]

    # --- Delta Operations ---

    def append_delta(self, table: str, data: pa.Table,
                     partition: str = GLOBAL_PARTITION) -> int:
        """Append a delta (parquet file) to a partition. Returns delta number."""
        part_dir = self._partition_dir(table, partition)
        part_dir.mkdir(parents=True, exist_ok=True)

        # Find next delta number
        existing = sorted(part_dir.glob("delta_*.parquet"))
        next_num = len(existing) + 1
        delta_path = part_dir / f"delta_{next_num:06d}.parquet"

        pq.write_table(data, delta_path)
        self._manifest(table).bump()
        return next_num

    def read_partition(self, table: str,
                       partition: str = GLOBAL_PARTITION) -> Optional[pa.Table]:
        """Materialize current state by reading all deltas in a partition."""
        part_dir = self._partition_dir(table, partition)
        if not part_dir.exists():
            return None

        deltas = sorted(part_dir.glob("delta_*.parquet"))
        if not deltas:
            # Check for compacted snapshot
            snapshot = part_dir / "snapshot.parquet"
            if snapshot.exists():
                return pq.read_table(snapshot)
            return None

        tables = []
        # Read snapshot first if exists
        snapshot = part_dir / "snapshot.parquet"
        if snapshot.exists():
            tables.append(pq.read_table(snapshot))

        for d in deltas:
            tables.append(pq.read_table(d))

        return pa.concat_tables(tables) if tables else None

    def list_partitions(self, table: str) -> List[str]:
        """List all partitions for a table."""
        table_dir = self._table_dir(table)
        if not table_dir.exists():
            return []
        return [d.name for d in table_dir.iterdir()
                if d.is_dir() and d.name != "__pycache__"]

    def read_all(self, table: str) -> Optional[pa.Table]:
        """Read all partitions merged into one table."""
        partitions = self.list_partitions(table)
        if not partitions:
            return None

        tables = []
        for p in partitions:
            t = self.read_partition(table, p)
            if t and t.num_rows > 0:
                tables.append(t)

        return pa.concat_tables(tables) if tables else None

    # --- Compaction ---

    def compact_partition(self, table: str,
                          partition: str = GLOBAL_PARTITION) -> int:
        """Merge all deltas into a single snapshot. Returns rows compacted."""
        merged = self.read_partition(table, partition)
        if merged is None or merged.num_rows == 0:
            return 0

        part_dir = self._partition_dir(table, partition)

        # Write snapshot
        pq.write_table(merged, part_dir / "snapshot.parquet")

        # Remove old deltas
        for d in part_dir.glob("delta_*.parquet"):
            d.unlink()

        self._manifest(table).bump()
        return merged.num_rows

    # --- Time Travel ---

    def read_at_delta(self, table: str, partition: str,
                      up_to_delta: int) -> Optional[pa.Table]:
        """Read partition state up to a specific delta number (time-travel)."""
        part_dir = self._partition_dir(table, partition)
        if not part_dir.exists():
            return None

        tables = []
        snapshot = part_dir / "snapshot.parquet"
        if snapshot.exists():
            tables.append(pq.read_table(snapshot))

        for i in range(1, up_to_delta + 1):
            delta_path = part_dir / f"delta_{i:06d}.parquet"
            if delta_path.exists():
                tables.append(pq.read_table(delta_path))

        return pa.concat_tables(tables) if tables else None

    def get_version(self, table: str) -> int:
        """Get current stream version for a table."""
        return self._manifest(table).version

    # --- Table Management ---

    def ensure_table(self, table: str):
        """Ensure table directory exists."""
        self._table_dir(table).mkdir(parents=True, exist_ok=True)

    def table_exists(self, table: str) -> bool:
        return self._table_dir(table).exists()

    def delta_count(self, table: str, partition: str = GLOBAL_PARTITION) -> int:
        """Count deltas in a partition."""
        part_dir = self._partition_dir(table, partition)
        if not part_dir.exists():
            return 0
        return len(list(part_dir.glob("delta_*.parquet")))
