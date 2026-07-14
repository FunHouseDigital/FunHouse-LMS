"""Sync_Service package: idempotent batch sync with last-write-wins (Req 4, 5).

The API is the offline-first sync target for the future PWA. Field devices queue
writes locally and submit them in a :class:`~funhouse_api.sync.service.SyncBatch`;
the Sync_Service applies each action server-side, idempotently and
deterministically, reusing the Phase 0 Load logic for every write (see
:mod:`funhouse_api.sync.mapping`).
"""
