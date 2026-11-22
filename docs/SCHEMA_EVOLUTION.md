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

## Remediation guidance

When a comparison returns something other than `compatible`:

| Classification | Recommended action |
|----------------|--------------------|
| `compatible` | Proceed. New columns are preserved as `string`; no code change required. |
| `potentially_breaking` | Review which non-required columns changed. If a downstream consumer depends on one, update that consumer. Re-declare the schema once accepted. |
| `breaking` | **Stop.** A required contract column was removed or retyped. Update `ingestion/models.py` / `ingestion/storage.py` to handle the new shape, add/adjust tests, re-declare the schema, and only then re-ingest. Do not force-publish over a breaking change without deliberate review. |

## Limitations

- The "full archive" processed is the **API-generated bulk-download** full
  fiscal-year file. A **manually-downloaded Award Data Archive** file may have a
  different schema. The code is schema-agnostic and will fingerprint and classify
  such a file, but a real manual-archive file **was not processed** in
  Milestone 2.
- Automatic remediation (e.g. column-level migrations) is out of scope; the
  registry **detects and classifies** change, remediation is a deliberate
  engineering step.
