"""Federal Spending Data Warehouse — ingestion package.

Milestone 1 scope: source extraction from the USAspending.gov bulk-download API,
schema-aware validation, immutable raw archival, and Parquet materialization.

Responsibilities are deliberately separated into single-purpose modules:

- ``config``    — environment-driven settings (no secrets in code).
- ``client``    — HTTP transport, retries, async job polling, safe downloads.
- ``extract``   — orchestration: request -> raw archive -> manifest.
- ``validate``  — schema/type/duplicate checks and quarantine routing.
- ``storage``   — decimal-safe Parquet materialization via PyArrow.
- ``manifest``  — provenance record for every extraction.
"""

__version__ = "0.1.0"
