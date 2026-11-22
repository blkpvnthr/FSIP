"""Unit tests for fiscal partition keys and the partitioned Parquet writer."""

from __future__ import annotations

from dataclasses import replace

import pyarrow.parquet as pq
from ingestion.config import Settings
from ingestion.partitioning import (
    PartitionedParquetWriter,
    PartitionKey,
    partition_key_for_row,
)


def test_partition_key_from_action_date():
    row = {"action_date": "2026-01-10"}
    key = partition_key_for_row(row, "NASA")
    assert key == PartitionKey("NASA", 2026, 4)
    assert key.relative_path.as_posix() == "awarding_agency=NASA/fiscal_year=2026/fiscal_month=04"


def test_partition_key_none_for_missing_action_date():
    assert partition_key_for_row({"action_date": ""}, "NASA") is None
    assert partition_key_for_row({}, "NASA") is None


def test_writer_creates_hive_layout(settings: Settings):
    header = ["contract_transaction_unique_key", "federal_action_obligation", "action_date"]
    base = settings.parquet_dir / "test"
    writer = PartitionedParquetWriter(base, header, settings)
    k1 = PartitionKey("NASA", 2026, 1)
    k2 = PartitionKey("NASA", 2026, 4)
    writer.write_rows(
        k1,
        [
            {
                "contract_transaction_unique_key": "T1",
                "federal_action_obligation": "100.00",
                "action_date": "2025-10-01",
            }
        ],
    )
    writer.write_rows(
        k2,
        [
            {
                "contract_transaction_unique_key": "T2",
                "federal_action_obligation": "5.00",
                "action_date": "2026-01-01",
            }
        ],
    )
    stats = writer.close()
    assert len(stats) == 2
    f1 = base / k1.relative_path / "part-00000.parquet"
    assert f1.exists()
    assert pq.read_metadata(f1).num_rows == 1


def test_writer_rolls_part_files_at_target(settings: Settings):
    small = replace(settings, target_partition_file_rows=2)
    header = ["contract_transaction_unique_key", "federal_action_obligation", "action_date"]
    base = small.parquet_dir / "roll"
    writer = PartitionedParquetWriter(base, header, small)
    key = PartitionKey("NASA", 2026, 1)
    for i in range(3):
        writer.write_rows(
            key,
            [
                {
                    "contract_transaction_unique_key": f"T{i}",
                    "federal_action_obligation": "1.00",
                    "action_date": "2025-10-01",
                }
            ],
        )
    stats = writer.close()
    files = sorted((base / key.relative_path).glob("*.parquet"))
    # 3 rows with a 2-row target split across at least two part-files.
    assert len(files) >= 2
    assert sum(pq.read_metadata(f).num_rows for f in files) == 3
    assert stats[0].rows == 3
