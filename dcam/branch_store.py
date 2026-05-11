"""Branch-aware memory store — writes go to feature branches, merge to main.

Storage layout:
    ~/.dcam/delta/
      └── {namespace}/
          ├── __branches__.json              # Branch metadata
          ├── main/                          # Mainline branch
          │   └── {table}/
          │       └── {partition}/
          │           └── delta_*.parquet
          └── {branch_name}/                 # Feature branch
              └── {table}/
                  └── {partition}/
                      └── delta_*.parquet

Usage:
    dcam --branch feat/auth-fix chat start --title "Fix auth"
    dcam --branch feat/auth-fix memory add "auth uses JWT" --name auth-info
    dcam branch list
    dcam branch merge feat/auth-fix          # Merge to main
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

MAIN_BRANCH = "main"


class BranchMeta:
    """Tracks branch metadata."""

    def __init__(self, root: Path):
        self._path = root / "__branches__.json"
        self.branches: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            self.branches = json.loads(self._path.read_text())

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self.branches, indent=2))

    def create(self, name: str, parent: str = MAIN_BRANCH):
        self.branches[name] = {
            "parent": parent,
            "created_at": datetime.now().isoformat(),
            "merged": False,
        }
        self._save()

    def mark_merged(self, name: str):
        if name in self.branches:
            self.branches[name]["merged"] = True
            self.branches[name]["merged_at"] = datetime.now().isoformat()
            self._save()

    def exists(self, name: str) -> bool:
        return name == MAIN_BRANCH or name in self.branches

    def list_all(self) -> List[dict]:
        result = [{"name": MAIN_BRANCH, "parent": None, "merged": False}]
        for name, meta in self.branches.items():
            result.append({"name": name, **meta})
        return result


class BranchStore:
    """Branch-aware delta store. All reads merge branch + main. Writes go to branch only."""

    def __init__(self, namespace: str = "dcam", branch: str = MAIN_BRANCH,
                 root: Optional[str] = None):
        self.namespace = namespace
        self.branch = branch
        self.root = Path(root) if root else Path.home() / ".dcam" / "delta"
        self._ns_root = self.root / namespace
        self.meta = BranchMeta(self._ns_root)

        # Auto-create branch if it doesn't exist
        if branch != MAIN_BRANCH and not self.meta.exists(branch):
            self.meta.create(branch)

    def _branch_dir(self, branch: Optional[str] = None) -> Path:
        return self._ns_root / (branch or self.branch)

    def _table_dir(self, table: str, branch: Optional[str] = None) -> Path:
        return self._branch_dir(branch) / table

    def _partition_dir(self, table: str, partition: str, branch: Optional[str] = None) -> Path:
        return self._table_dir(table, branch) / partition

    # --- Write (always to current branch) ---

    def append_delta(self, table: str, data: pa.Table, partition: str = "__global__") -> int:
        part_dir = self._partition_dir(table, partition)
        part_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(part_dir.glob("delta_*.parquet"))
        next_num = len(existing) + 1
        pq.write_table(data, part_dir / f"delta_{next_num:06d}.parquet")
        return next_num

    # --- Read (merge branch + main) ---

    def read_partition(self, table: str, partition: str) -> Optional[pa.Table]:
        """Read from main + current branch merged."""
        tables = []

        # Read main first
        if self.branch != MAIN_BRANCH:
            main_table = self._read_partition_raw(table, partition, MAIN_BRANCH)
            if main_table:
                tables.append(main_table)

        # Then branch (overlays main)
        branch_table = self._read_partition_raw(table, partition, self.branch)
        if branch_table:
            tables.append(branch_table)

        return pa.concat_tables(tables) if tables else None

    def _read_partition_raw(self, table: str, partition: str,
                            branch: str) -> Optional[pa.Table]:
        part_dir = self._partition_dir(table, partition, branch)
        if not part_dir.exists():
            return None

        parts = []
        snapshot = part_dir / "snapshot.parquet"
        if snapshot.exists():
            parts.append(pq.read_table(snapshot))
        for d in sorted(part_dir.glob("delta_*.parquet")):
            parts.append(pq.read_table(d))

        return pa.concat_tables(parts) if parts else None

    def read_all(self, table: str) -> Optional[pa.Table]:
        """Read all partitions from main + branch."""
        partitions = set()
        for branch in ([MAIN_BRANCH, self.branch] if self.branch != MAIN_BRANCH else [MAIN_BRANCH]):
            table_dir = self._table_dir(table, branch)
            if table_dir.exists():
                for d in table_dir.iterdir():
                    if d.is_dir() and d.name != "__pycache__":
                        partitions.add(d.name)

        tables = []
        for p in partitions:
            t = self.read_partition(table, p)
            if t and t.num_rows > 0:
                tables.append(t)

        return pa.concat_tables(tables) if tables else None

    def list_partitions(self, table: str) -> List[str]:
        partitions = set()
        for branch in ([MAIN_BRANCH, self.branch] if self.branch != MAIN_BRANCH else [MAIN_BRANCH]):
            table_dir = self._table_dir(table, branch)
            if table_dir.exists():
                for d in table_dir.iterdir():
                    if d.is_dir() and d.name != "__pycache__":
                        partitions.add(d.name)
        return list(partitions)

    # --- Branch operations ---

    def merge_to_main(self) -> int:
        """Merge current branch deltas into main. Returns files merged."""
        if self.branch == MAIN_BRANCH:
            return 0

        branch_dir = self._branch_dir()
        if not branch_dir.exists():
            return 0

        count = 0
        for table_dir in branch_dir.iterdir():
            if not table_dir.is_dir():
                continue
            table = table_dir.name
            for part_dir in table_dir.iterdir():
                if not part_dir.is_dir():
                    continue
                partition = part_dir.name
                main_part = self._partition_dir(table, partition, MAIN_BRANCH)
                main_part.mkdir(parents=True, exist_ok=True)

                # Find next delta number in main
                existing_main = sorted(main_part.glob("delta_*.parquet"))
                next_num = len(existing_main) + 1

                # Copy branch deltas to main
                for delta in sorted(part_dir.glob("delta_*.parquet")):
                    dest = main_part / f"delta_{next_num:06d}.parquet"
                    shutil.copy2(delta, dest)
                    next_num += 1
                    count += 1

                # Copy snapshot if exists and main doesn't have one
                snapshot = part_dir / "snapshot.parquet"
                main_snapshot = main_part / "snapshot.parquet"
                if snapshot.exists() and not main_snapshot.exists():
                    shutil.copy2(snapshot, main_snapshot)

        self.meta.mark_merged(self.branch)
        return count

    def delete_branch(self) -> bool:
        """Delete current branch (after merge)."""
        if self.branch == MAIN_BRANCH:
            return False
        branch_dir = self._branch_dir()
        if branch_dir.exists():
            shutil.rmtree(branch_dir)
        return True

    def ensure_table(self, table: str):
        self._table_dir(table).mkdir(parents=True, exist_ok=True)

    def table_exists(self, table: str) -> bool:
        return (self._table_dir(table).exists() or
                self._table_dir(table, MAIN_BRANCH).exists())
