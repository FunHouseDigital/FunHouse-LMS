"""Run manifest: resumable per-file/per-record run state (Task 14.1, Req 13.1/13.3).

The orchestrator threads a **run manifest** through every stage so the run is
*resumable* and *idempotent* (design § Idempotency & Re-Runnability, point 4).
The manifest is a plain JSON document persisted to a local ``.pipeline-state/``
directory as ``<run_id>.json``. It records, for one run:

* identity + provenance (``run_id``, ``source_folder``, ``config_path``, timestamps);
* per-stage lifecycle (``pending`` -> ``running`` -> ``completed`` / ``failed``);
* per-file status keyed by absolute source path, per stage
  (so ``--resume`` can skip files already completed for a stage);
* per-record disposition (``loaded`` / ``skipped`` / ``flagged``);
* an explicit list of every skip and every failure (nothing disappears silently);
* the final run summary counts.

Because the manifest is persisted after every stage (and is safe to re-read),
a re-run with ``--resume <run_id>`` reconstitutes prior state and re-attempts
only work that is not yet ``done``. Database-level idempotency (natural keys,
``dedup_key``, archive content hashes) guarantees correctness even if a file is
reprocessed, so the manifest is an *optimization + audit record*, not the sole
correctness mechanism.

This module performs only local file I/O -- no network, no database.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Default directory (relative to the working dir) where manifests are stored.
DEFAULT_STATE_DIR = ".pipeline-state"

#: The five pipeline stages, in execution order.
STAGES: tuple[str, ...] = ("collect", "extract", "validate", "load", "archive")

# Stage lifecycle states.
STAGE_PENDING = "pending"
STAGE_RUNNING = "running"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

# Per-file / per-record dispositions.
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunManifest:
    """Mutable, JSON-serializable state for a single pipeline run."""

    run_id: str
    source_folder: str = ""
    config_path: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    #: stage -> {status, started_at, completed_at, error}
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: absolute source path -> {handler, subfolder, stages: {stage: status}}
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: record_id -> {table, status, reason, source_file}
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: append-only lists so nothing is lost (design § Error Handling).
    skips: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: final run summary counts.
    summary: dict[str, int] = field(default_factory=dict)
    #: path this manifest was last saved to (not serialized).
    _path: str | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    # Construction / persistence
    # ------------------------------------------------------------------ #
    @staticmethod
    def state_dir(base: str | Path | None = None) -> Path:
        """Return the manifest directory, creating it if needed."""
        directory = Path(base) if base is not None else Path(DEFAULT_STATE_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @classmethod
    def path_for(cls, run_id: str, base: str | Path | None = None) -> Path:
        return cls.state_dir(base) / f"{run_id}.json"

    @classmethod
    def create(
        cls,
        run_id: str,
        *,
        source_folder: str = "",
        config_path: str = "",
        base: str | Path | None = None,
    ) -> "RunManifest":
        """Create a fresh manifest with every stage marked pending."""
        manifest = cls(
            run_id=run_id,
            source_folder=str(source_folder),
            config_path=str(config_path),
        )
        for stage in STAGES:
            manifest.stages[stage] = {"status": STAGE_PENDING}
        manifest._path = str(cls.path_for(run_id, base))
        return manifest

    @classmethod
    def load(cls, run_id: str, base: str | Path | None = None) -> "RunManifest":
        """Load an existing manifest for ``run_id`` (for ``--resume``)."""
        path = cls.path_for(run_id, base)
        if not path.exists():
            raise FileNotFoundError(
                f"No run manifest found for run_id {run_id!r} at {path}. "
                "Cannot resume an unknown run."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = cls(
            run_id=data.get("run_id", run_id),
            source_folder=data.get("source_folder", ""),
            config_path=data.get("config_path", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            stages=data.get("stages", {}),
            files=data.get("files", {}),
            records=data.get("records", {}),
            skips=data.get("skips", []),
            failures=data.get("failures", []),
            summary=data.get("summary", {}),
        )
        manifest._path = str(path)
        return manifest

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_path", None)
        return data

    def save(self, base: str | Path | None = None) -> Path:
        """Atomically persist the manifest to disk and return its path."""
        self.updated_at = _now_iso()
        if self._path is None:
            self._path = str(self.path_for(self.run_id, base))
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in the same dir, then rename.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, sort_keys=True, default=str)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    # ------------------------------------------------------------------ #
    # Stage lifecycle
    # ------------------------------------------------------------------ #
    def start_stage(self, stage: str) -> None:
        self.stages.setdefault(stage, {})
        self.stages[stage]["status"] = STAGE_RUNNING
        self.stages[stage]["started_at"] = _now_iso()

    def complete_stage(self, stage: str) -> None:
        self.stages.setdefault(stage, {})
        self.stages[stage]["status"] = STAGE_COMPLETED
        self.stages[stage]["completed_at"] = _now_iso()

    def fail_stage(self, stage: str, error: str) -> None:
        self.stages.setdefault(stage, {})
        self.stages[stage]["status"] = STAGE_FAILED
        self.stages[stage]["completed_at"] = _now_iso()
        self.stages[stage]["error"] = error

    def stage_status(self, stage: str) -> str:
        return self.stages.get(stage, {}).get("status", STAGE_PENDING)

    # ------------------------------------------------------------------ #
    # Files
    # ------------------------------------------------------------------ #
    def register_file(self, path: str, *, handler: str = "", subfolder: str = "") -> None:
        entry = self.files.setdefault(path, {"stages": {}})
        if handler:
            entry["handler"] = handler
        if subfolder:
            entry["subfolder"] = subfolder

    def set_file_status(self, path: str, stage: str, status: str) -> None:
        entry = self.files.setdefault(path, {"stages": {}})
        entry.setdefault("stages", {})[stage] = status

    def file_status(self, path: str, stage: str) -> str | None:
        return self.files.get(path, {}).get("stages", {}).get(stage)

    def is_file_done(self, path: str, stage: str) -> bool:
        return self.file_status(path, stage) == STATUS_DONE

    # ------------------------------------------------------------------ #
    # Records
    # ------------------------------------------------------------------ #
    def mark_record(
        self,
        record_id: str,
        *,
        table: str = "",
        status: str = "",
        reason: str = "",
        source_file: str = "",
    ) -> None:
        entry = self.records.setdefault(record_id, {})
        if table:
            entry["table"] = table
        if status:
            entry["status"] = status
        if reason:
            entry["reason"] = reason
        if source_file:
            entry["source_file"] = source_file

    # ------------------------------------------------------------------ #
    # Skips / failures (append-only audit)
    # ------------------------------------------------------------------ #
    def record_skip(self, stage: str, target: str, reason: str) -> None:
        self.skips.append({"stage": stage, "target": target, "reason": reason})

    def record_failure(self, stage: str, target: str, reason: str) -> None:
        self.failures.append({"stage": stage, "target": target, "reason": reason})
