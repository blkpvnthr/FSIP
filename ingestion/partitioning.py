"""Fiscal partitioning and bounded-memory Hive-partitioned Parquet writing.

Validated transactions are partitioned by awarding agency and federal fiscal
year/month into a Hive-compatible directory layout::

    <base>/awarding_agency=<KEY>/fiscal_year=<YYYY>/fiscal_month=<MM>/part-NNNNN.parquet

Design notes
------------
* **Bounded memory.** Each partition is written through a streaming
  ``pyarrow.parquet.ParquetWriter`` opened lazily on first use; only one batch of
  rows is materialized in memory at a time. Writers roll to a new ``part-NNNNN``
  file once a configurable row count is reached, which also bounds single-file size
  and avoids a single unbounded file.
* **Determinism.** Partition keys derive from each record's ``action_date`` (fiscal
  year/month) and the extraction-scope awarding agency, so the same input always
  produces the same layout.
* **Typing is reused.** Rows are converted with :func:`ingestion.storage.rows_to_table`,
  so partitioned output carries the exact same decimal/date/timestamp types and
  full-column fidelity as Milestone-1 output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from .config import Settings
from .logging_config import get_logger
from .models import (
    ACTION_DATE_COLUMN,
    federal_fiscal_month,
    federal_fiscal_year,
)
from .storage import rows_to_table
from .validate import parse_date

log = get_logger(__name__)


@dataclass(frozen=True)
class PartitionKey:
    """A Hive partition identity."""

    awarding_agency: str
    fiscal_year: int
    fiscal_month: int

    @property
    def relative_path(self) -> Path:
        return Path(
            f"awarding_agency={self.awarding_agency}",
            f"fiscal_year={self.fiscal_year}",
            f"fiscal_month={self.fiscal_month:02d}",
        )


@dataclass
class PartitionStat:
    """Per-partition accounting produced by the writer."""

    key: PartitionKey
    rows: int = 0
    files: list[str] = field(default_factory=list)


def partition_key_for_row(row: dict[str, str], awarding_agency: str) -> PartitionKey | None:
    """Compute a row's partition key from its ``action_date``.

    Returns ``None`` if the action date is missing/unparseable (the caller routes
    such rows to quarantine rather than guessing a partition).
    """
    try:
        d: date | None = parse_date(row.get(ACTION_DATE_COLUMN))
    except ValueError:
        return None
    if d is None:
        return None
    return PartitionKey(
        awarding_agency=awarding_agency,
        fiscal_year=federal_fiscal_year(d),
        fiscal_month=federal_fiscal_month(d),
    )


class PartitionedParquetWriter:
    """Streaming writer that fans validated rows out into fiscal partitions.

    One :class:`pyarrow.parquet.ParquetWriter` is kept open per active partition
    part-file. Call :meth:`write_rows` with batches and :meth:`close` at the end.
    """

    def __init__(self, base_dir: Path, header: list[str], settings: Settings) -> None:
        self.base_dir = base_dir
        self.header = header
        self.settings = settings
        self._writers: dict[PartitionKey, pq.ParquetWriter] = {}
        self._part_index: dict[PartitionKey, int] = {}
        self._rows_in_part: dict[PartitionKey, int] = {}
        self.stats: dict[PartitionKey, PartitionStat] = {}

    def _part_path(self, key: PartitionKey) -> Path:
        idx = self._part_index.get(key, 0)
        return self.base_dir / key.relative_path / f"part-{idx:05d}.parquet"

    def _open_writer(self, key: PartitionKey, schema) -> pq.ParquetWriter:
        path = self._part_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = pq.ParquetWriter(path, schema, compression=self.settings.parquet_compression)
        self._writers[key] = writer
        self._rows_in_part[key] = 0
        self.stats.setdefault(key, PartitionStat(key=key)).files.append(str(path))
        return writer

    def write_rows(self, key: PartitionKey, rows: list[dict[str, str]]) -> None:
        """Append ``rows`` to the given partition (rolling part-files as needed)."""
        if not rows:
            return
        table = rows_to_table(rows, self.header)
        schema = table.schema

        writer = self._writers.get(key)
        if writer is None:
            writer = self._open_writer(key, schema)

        # Roll to a new part-file if this batch would exceed the target rows.
        if (
            self._rows_in_part[key] + table.num_rows > self.settings.target_partition_file_rows
            and self._rows_in_part[key] > 0
        ):
            writer.close()
            self._part_index[key] = self._part_index.get(key, 0) + 1
            writer = self._open_writer(key, schema)

        writer.write_table(table, row_group_size=self.settings.parquet_row_group_size)
        self._rows_in_part[key] += table.num_rows
        self.stats[key].rows += table.num_rows

    def close(self) -> list[PartitionStat]:
        """Close all writers; return per-partition stats sorted by key."""
        for writer in self._writers.values():
            writer.close()
        self._writers.clear()
        log.info(
            "partitioning.closed",
            extra={
                "context": {
                    "partitions": len(self.stats),
                    "total_rows": sum(s.rows for s in self.stats.values()),
                }
            },
        )
        return [
            self.stats[k]
            for k in sorted(
                self.stats,
                key=lambda k: (k.awarding_agency, k.fiscal_year, k.fiscal_month),
            )
        ]
