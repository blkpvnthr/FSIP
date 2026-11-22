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

# PEP 440 version. Phase A baseline == v0.3.0-alpha.1 (0.3.0a1). It is NOT bumped
# to 0.3.0a2 until Phase B AWS infrastructure is actually deployed and verified.
__version__ = "0.3.0a1"
