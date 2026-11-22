# Schema Evolution

Federal source files change over time: columns get added, renamed, or retyped
between fiscal years and between archive generations. This platform treats the
source schema as a **versioned contract**: every ingested archive's schema is
recorded, fingerprinted, and compared against the previously declared schema, and
every change is **classified by compatibility** so that a breaking change fails
loud instead of silently corrupting the data asset.

Schema logic lives in `ingestion/schema_registry.py`.

## Schema registry

For each dataset the registry records a declared schema snapshot under
`data/schemas/`:

| Recorded element | Purpose |
|------------------|---------|
| Ordered column list | Detects additions, removals, and reordering. |
| SHA-256 fingerprint | Single-value drift signal (the fingerprint of the ordered 297-column header). |
| Per-column Arrow type | Detects type changes on columns the platform types explicitly. |

The fingerprint is also written into the dataset manifest
(`schema_fingerprint`), so provenance and drift detection share one value.

## Change classification policy

`compare_schemas` compares a new schema against the declared schema and classifies
each difference into one of three categories:

| Category | Triggering change | Meaning |
|----------|-------------------|---------|
| `compatible` | Column **added**; columns **reordered** | Safe. Readers of the stable contract are unaffected; new columns are preserved verbatim. |
| `potentially_breaking` | **Removed** or **type-changed** column that is **not** a required contract column | Needs review. May affect optional/ancillary consumers but not the core contract. |
| `breaking` | **Removed** or **type-changed** column that **is** a required contract column | Must not proceed silently. The core data contract is violated. |

In short:

- **Additions and reordering → `compatible`.**
- **Removal / type change of a non-required column → `potentially_breaking`.**
- **Removal / type change of a REQUIRED column → `breaking`.**

The "required" set is the stable data contract documented in
`docs/DATA_DICTIONARY.md` (identifiers, financial, dates/timestamps, agency
identifiers).

## Verified result: FY2025 → FY2026 (NASA, real data)

The two NASA archives actually processed were compared:

| Comparison | Result |
|------------|--------|
| FY2025 → FY2026 column sets | **Identical** — 297 columns, same order |
| Classification | **`compatible`** (no added/removed/reordered/type-changed columns) |
| FY2026 schema fingerprint | `36933b682c9a827618a999358cfb091e8938f388c910aaf10e65479fe6f85a03` |

Both fiscal years share the same schema fingerprint over the 297-column header,
confirming no drift across the year boundary.

## Verified result: FY2022 → FY2026 (NASA, 5 fiscal years, real data)

Milestone 2.1 extended the comparison across the full five-year NASA history now in
the lake:

| Comparison | Result |
|------------|--------|
| NASA FY2022 → FY2026 column sets | **Identical = True** — 297 columns stable across all 5 fiscal years |
| Classification | **`compatible`** |

The 297-column contract is stable across every NASA fiscal year processed
(FY2022–FY2026), spanning both **manual Award Data Archive** sources (FY2022–FY2024)
and **API bulk-download** sources (FY2025–FY2026). The classification policy
(`compatible` / `potentially_breaking` / `breaking`) is **unchanged** from
Milestone 2.

### Manual vs API archive schema

A key Milestone-2.1 finding: the manual "Contracts Full" archive and the API
bulk-download produce an **identical 297-column schema** (0 columns differ, same
types/formats), so **no schema normalization is required** to mix the two sources
in one lake. They differ only in member file naming and in award-type content
(the manual full archive also contains IDVs, which the award-type scope filter
removes) — **not** in schema. This retires the Milestone-2 open question of whether
a real manual-archive file would have a different shape. See `docs/DATA_PROFILING.md`.

## Dataset-wide integrity (Milestone 2.1)

Schema fingerprinting protects a *single* dataset's contract. Milestone 2.1 adds a
complementary protection across the *whole lake*: **cross-dataset transaction-identity
validation**, so the same obligation can never be counted twice in two different
published datasets.

Integrity logic lives in `ingestion/integrity.py`.

### Pre-publish integrity gate

`cross_dataset_overlap(...)` runs **after reconciliation passes and before the
atomic publish**. It checks the candidate dataset's `contract_transaction_unique_key`
values against **all already-published OTHER datasets**, deliberately **excluding the
same dataset's own prior published version** so an idempotent re-publish never
self-collides. Any overlap sets status **`integrity_conflict`** and **blocks
publication** unless `--force` is given. This is the structural guarantee against
double-counting obligations across datasets.

### Dataset-wide audit

`audit_integrity(settings)` scans the entire published lake (via DuckDB) for
duplicate transaction keys across datasets and classifies any cross-dataset
duplicate as **identical** vs **conflicting** (differing `federal_action_obligation`
or `last_modified_date`). Run it with `fsdw audit-integrity` (see `docs/RUNBOOK.md`).

Verified result over the 7-dataset lake (DOE FY2026, GSA FY2026, NASA FY2022–FY2026):

| Metric | Value |
|--------|------:|
| `total_published_rows` | 1,413,894 |
| `distinct_transaction_keys` | 1,413,894 |
| `duplicate_key_count` | 0 |
| `cross_dataset_key_count` | 0 |
| `conflicting_key_count` | 0 |
| Result | **PASSED** |

Every published transaction key is globally unique across the lake — no duplicates,
no cross-dataset overlap, no conflicts.

> **Scope note:** Milestone 2.1 only **detects and reports** cross-dataset
> conflicts. The correction/deletion lifecycle (superseding versions, applying
> deltas to resolve a conflict) is **deferred to the CDC milestone**.

## Remediation guidance

When a comparison returns something other than `compatible`:

| Classification | Recommended action |
|----------------|--------------------|
| `compatible` | Proceed. New columns are preserved as `string`; no code change required. |
| `potentially_breaking` | Review which non-required columns changed. If a downstream consumer depends on one, update that consumer. Re-declare the schema once accepted. |
| `breaking` | **Stop.** A required contract column was removed or retyped. Update `ingestion/models.py` / `ingestion/storage.py` to handle the new shape, add/adjust tests, re-declare the schema, and only then re-ingest. Do not force-publish over a breaking change without deliberate review. |

## Limitations

- In Milestone 2 only the **API-generated bulk-download** was processed, leaving
  open whether a **manually-downloaded Award Data Archive** might differ in schema.
  **Milestone 2.1 resolved this:** real manual "Contracts Full" archives
  (NASA FY2022–FY2024, DOE FY2026, GSA FY2026) were processed and found to carry
  the **identical 297-column schema** (`compatible`) — no normalization needed.
- Automatic remediation (e.g. column-level migrations) is out of scope; the
  registry **detects and classifies** change, remediation is a deliberate
  engineering step.
- Cross-dataset integrity is **detect-and-report only** in Milestone 2.1;
  conflict resolution is deferred to the CDC milestone.

---

## Milestone 3 — cloud (status)

Schema handling is unchanged in the cloud lake: schemas are still declared,
fingerprinted, and classified exactly as above. In S3 these records live under the
`control/` zone (schemas, manifests, snapshots) instead of `data/schemas/`, and
they are **immutable** — IAM explicitly denies deletes on control manifests and
snapshots, and the publication catalog pins the exact `part_keys` of each active
snapshot. The compatibility classifier and the fail-loud-on-breaking-change policy
carry over without change.

**Status:** AWS is **prepared but not deployed** — no AWS resources exist; verified
against a **local-backend cloud-lake emulation**; **live S3 pending approval**. See
`docs/CLOUD_ARCHITECTURE.md` and `docs/CLOUD_SECURITY.md`.
