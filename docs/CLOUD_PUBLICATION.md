# Cloud Publication Protocol — Milestone 3

> **Status: prepared, not deployed.** The protocol below was verified against a
> **local-backend cloud-lake emulation**, not live S3. Live acceptance is **pending
> approval** (Phase B/C). No AWS resources exist.

S3 provides **no atomic directory rename** — the primitive Milestone 2 used to make
a dataset visible in a single step. Milestone 3 reproduces the same
"readers see all-or-nothing" guarantee on object storage with a **content-addressed,
snapshot-isolated, catalog-pointer** protocol whose only commit point is a
**conditional compare-and-swap (CAS)** on one small pointer object.

Implementation: `ingestion/cloud/publication.py`, `catalog.py`, `verification.py`.

## 1. The 14-step protocol

Each publication runs under a `run_id` and allocates a `snapshot_id`.

| # | Step | Guarantee |
|---|------|-----------|
| 1 | Allocate `run_id` + `snapshot_id` | Run isolation; new immutable snapshot prefix. |
| 2 | Upload/verify immutable **content-addressed raw** (SHA-256) | Skip if already present (dedup); raw is never mutated. |
| 3 | Records already validated locally | Validation/quarantine happened before upload. |
| 4 | Upload candidate Parquet to **run-isolated staging** | Invisible to readers. |
| 5 | **Verify staged objects** (exist / size / sha256) | Byte-level upload integrity. |
| 6 | **Reconcile staged-vs-local**, decimal-exact | transaction_count, distinct_keys, net_obligation, per-partition. **FAIL blocks publish.** |
| 7 | **Cross-dataset transaction uniqueness** vs other active datasets | DuckDB distinct-key join. **Overlap blocks publish and is NOT bypassable by `--force`.** |
| 8 | Write **immutable candidate manifest** | `control/manifests/.../<snapshot_id>.json`. |
| 9 | **Publish into immutable curated snapshot prefix** (copy, create-only) | Existing snapshots never overwritten. |
| 10 | **Verify curated objects** | Confirms the committed copy. |
| 11 | **Build new catalog** (version + 1) | References **all active snapshots**, including exact `part_keys`. |
| 12 | **Advance `control/current.json` via conditional CAS** on prior etag | The **single commit point**. On `PreconditionFailed` → reload catalog, re-validate uniqueness, retry. |
| 13 | Record snapshot + reconciliation | Durable audit trail. |
| 14 | Delete staging objects | Cleanup; curated + catalog are now authoritative. |

Steps 1–10 touch only immutable/invisible keys. **Nothing a reader can see changes
until step 12**, and step 12 is atomic.

## 2. Concurrency control (CAS)

The pointer `control/current.json` is advanced **only** by a conditional write
keyed on the **prior catalog etag**. This is the concurrency control for
simultaneous publications:

- If two publications race, the first CAS wins and advances the pointer.
- The loser receives `PreconditionFailed`, **reloads** the now-current catalog,
  **re-validates cross-dataset uniqueness** against the new active set, and
  **retries**. It never blindly overwrites.

This guarantees that the catalog is always a consistent, append-only history and
that two concurrent publishers can never corrupt the active snapshot set.

## 3. Failure semantics

If publication fails **before the pointer moves** (steps 1–11, or a failed CAS at
12):

- The **previous catalog stays active**; all existing datasets remain readable.
- **Partial candidates are invisible** (staging + unreferenced curated snapshot).
- **Failure evidence is retained** (manifests, reconciliation records) for audit.
- The **retry is idempotent.**

An idempotent re-publish of **identical content** is a **no-op** reported as
`skipped` — the content-addressed raw already exists, the reconciliation matches,
and the catalog need not advance.

## 4. Rollback (append-only)

`catalog.rollback_to` does **not** delete anything. It writes a **new** catalog
version whose **dataset set equals a prior version's**, then advances the pointer
via the same CAS. Immutable snapshots referenced by the target version are retained
and simply become active again. History is therefore **append-only**: every
catalog version, including the rolled-back-to state, is preserved.

Rollback can target a prior catalog version (`--to-version`) or a specific snapshot
(`--snapshot-id`). See `docs/CLOUD_RUNBOOK.md`.

## 5. Why this is equivalent to the Milestone-2 atomic rename

| Milestone 2 (local disk) | Milestone 3 (S3) |
|--------------------------|------------------|
| Atomic directory rename | Conditional CAS on `control/current.json` |
| Reader sees old or new tree | Reader resolves old or new **catalog** (never globs snapshots) |
| Reconcile before rename | Reconcile (step 6) + uniqueness (step 7) before CAS |
| Replace, never append | New immutable snapshot + catalog that references the active set |
| Re-run replaces | Idempotent re-publish = `skipped` |

Readers **always** resolve the active snapshot via `control/current.json` and never
glob historical snapshot prefixes, which is what makes the switch all-or-nothing.

## 6. Reconciliation chain (independent readback)

Publication is followed by an independent cloud readback
(`ingestion/cloud/reconciliation.py`):

```
local manifest → candidate S3 objects → verified curated snapshot
              → published catalog → independent cloud readback
```

All money comparisons are `decimal128(23,2)` **decimal-exact** — no floats;
negatives/de-obligations preserved. See `docs/CLOUD_ARCHITECTURE.md` §5.

## Related documents

- `docs/CLOUD_ARCHITECTURE.md` — zones and lifecycle overview.
- `docs/CLOUD_MIGRATION.md` — migration order and verified emulation results.
- `docs/CLOUD_RUNBOOK.md` — commands, exit codes, and recovery.
