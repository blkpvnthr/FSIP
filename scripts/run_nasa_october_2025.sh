#!/usr/bin/env bash
#
# Reproduce the Milestone-1 deliverable end to end:
#   1. extract NASA prime contracts for October 2025 from USAspending.gov
#   2. reconcile the resulting Parquet against the manifest with DuckDB
#
# Requires: an activated virtualenv with the package installed (`pip install -e ".[dev]"`),
# or adjust PYTHON below. Requires network access for the live extraction.

set -euo pipefail

PYTHON="${PYTHON:-python}"
AGENCY="NASA"
START="2025-10-01"
END="2025-10-31"
SLUG="nasa_contracts_action_date_20251001_20251031"
DATA_DIR="${FSDW_DATA_DIR:-data}"

echo ">>> [1/2] Extracting ${AGENCY} prime contracts ${START}..${END}"
"${PYTHON}" -m ingestion.cli extract --agency "${AGENCY}" --start "${START}" --end "${END}" "$@"

echo
echo ">>> [2/2] Reconciling Parquet against manifest (DuckDB)"
"${PYTHON}" analytics/reconcile.py \
  --parquet  "${DATA_DIR}/parquet/${SLUG}.parquet" \
  --manifest "${DATA_DIR}/manifests/${SLUG}.json"

echo
echo ">>> Done. Artifacts:"
echo "    raw        : ${DATA_DIR}/raw/${SLUG}/"
echo "    parquet    : ${DATA_DIR}/parquet/${SLUG}.parquet"
echo "    manifest   : ${DATA_DIR}/manifests/${SLUG}.json"
