"""Snapshot collection — daily capture of overwrite-in-place upstreams."""

from ingestion.snapshot.collector import (
    SnapshotCollectionError,
    SnapshotCollector,
    SnapshotResult,
)

__all__ = ["SnapshotCollectionError", "SnapshotCollector", "SnapshotResult"]
