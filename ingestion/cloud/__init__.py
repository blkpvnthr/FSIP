"""Milestone 3 — AWS S3 cloud data lake.

A thin, backend-agnostic layer on top of :mod:`ingestion.storage_backends` that
implements content-addressed raw storage, run-isolated staging, versioned curated
snapshots, and a manifest-based publication protocol with an optimistic-concurrency
catalog pointer. Every module works identically against the local filesystem
backend (default, used for offline tests) and S3.
"""

from __future__ import annotations

__all__ = ["keys"]
