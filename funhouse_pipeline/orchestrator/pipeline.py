"""End-to-end pipeline orchestration (Task 14.1/14.2, Req 13.1/13.3/13.4).

Wires the five stages -- **Collect -> Extract -> Validate -> Load -> Archive** --
into one re-runnable flow behind a run manifest. This module is the programmatic
core the CLI (:mod:`funhouse_pipeline.orchestrator.cli`) is a thin wrapper over.

Design references:
- One command runs all five stages end to end (Req 13.1).
- A ``run_id`` + run manifest in ``.pipeline-state/`` make the run resumable and
  idempotent (design § Idempotency & Re-Runnability).
- Recoverable per-item errors are recorded and the run continues; unrecoverable
  run errors halt with an actionable message (design § Error Handling).
- Bedrock Batch (Extract) and S3 (Archive) calls get exponential-backoff retries
  (:mod:`funhouse_pipeline.orchestrator.retry`).
- A run summary reports counts: collected, extracted, flagged, loaded, skipped,
  archived.

Every external seam is injectable (``llm_generate_fn``, ``s3_client``,
``connection_factory``, ``sleep``, ``reference_date``, ``now``) so the whole
pipeline runs under test with a fake extractor + moto S3 + ephemeral PostgreSQL
and no live AWS.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from funhouse_pipeline.archive import Archiver, ArchivedObject, ArchiveResult, ArchiveStatus
from funhouse_pipeline.collect import CollectResult, HandlerTarget, collect
from funhouse_pipeline.config import Config
from funhouse_pipeline.extract import (
    ExtractedRecord,
    build_business_rules,
    extract_images,
    extract_lessons,
)
from funhouse_pipeline.extract.context import BusinessRules
from funhouse_pipeline.llm import llm_generate as _default_llm_generate
from funhouse_pipeline.orchestrator.manifest import (
    STATUS_DONE,
    STATUS_FAILED,
    RunManifest,
)
from funhouse_pipeline.orchestrator.retry import RetryPolicy, retry_call, with_retry
from funhouse_pipeline.validate import Partition, partition, write_review_artifact

# Ordered stages of the linear pipeline (archive branches off collect).
_LINEAR_STAGES = ("collect", "extract", "validate", "load")
ALL_STAGES = ("collect", "extract", "validate", "load", "archive")


class UnrecoverablePipelineError(RuntimeError):
    """Raised for an unrecoverable run error that must halt the pipeline.

    Carries an actionable message (design § Error Handling: "halt with a clear
    message"). The CLI turns this into a non-zero exit and prints the message.
    """


@dataclass
class PipelineResult:
    """Outcome of a pipeline run, with per-stage results and summary counts."""

    run_id: str
    manifest: RunManifest
    stages_run: tuple[str, ...] = ()
    collect_result: CollectResult | None = None
    records: list[ExtractedRecord] = field(default_factory=list)
    partition: Partition | None = None
    review_artifact: str | None = None
    load_result: Any | None = None
    archive_result: ArchiveResult | None = None
    summary: dict[str, int] = field(default_factory=dict)

    def summary_text(self) -> str:
        s = self.summary
        return (
            f"Run {self.run_id} summary "
            f"(stages: {', '.join(self.stages_run)}):\n"
            f"  collected: {s.get('collected', 0)}\n"
            f"  extracted: {s.get('extracted', 0)}\n"
            f"  flagged:   {s.get('flagged', 0)}\n"
            f"  loaded:    {s.get('loaded', 0)}\n"
            f"  skipped:   {s.get('skipped', 0)}\n"
            f"  archived:  {s.get('archived', 0)}"
        )


def _stages_to_run(selected: str | None) -> tuple[str, ...]:
    """Resolve which stages run for a ``--stage`` selection.

    With no selection, all five run. A single-stage selection runs that stage
    plus the cheap/offline prerequisites its in-memory inputs require, so each
    invocation is self-consistent:

    * ``collect``  -> collect
    * ``extract``  -> collect, extract
    * ``validate`` -> collect, extract, validate
    * ``load``     -> collect, extract, validate, load
    * ``archive``  -> collect, archive  (archive only needs the collected originals)
    """
    if selected is None:
        return ALL_STAGES
    selected = selected.lower()
    if selected == "archive":
        return ("collect", "archive")
    if selected in _LINEAR_STAGES:
        idx = _LINEAR_STAGES.index(selected)
        return _LINEAR_STAGES[: idx + 1]
    raise ValueError(
        f"Unknown stage {selected!r}. Choose one of: {', '.join(ALL_STAGES)}."
    )


def _known_player_names(conn: Any) -> tuple[str, ...]:
    """Best-effort known player names from the DB (for the extraction prompt)."""
    names: list[str] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT first_name, last_name FROM players")
            for first, last in cursor.fetchall():
                full = f"{first or ''} {last or ''}".strip()
                if full:
                    names.append(full)
    except Exception:
        return ()
    return tuple(names)


def _lookup_id(conn: Any, sql: str, params: Sequence[Any]) -> Any | None:
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, list(params))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def run_pipeline(
    config: Config,
    source_folder: str | Path,
    *,
    run_id: str | None = None,
    resume: bool = False,
    stage: str | None = None,
    state_dir: str | Path | None = None,
    migrate: bool = True,
    seed_data: bool = True,
    review_dir: str | Path | None = None,
    llm_generate_fn: Callable[..., Any] = _default_llm_generate,
    s3_client: Any | None = None,
    connection_factory: Callable[[Config], Any] | None = None,
    retry_policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] | None = None,
    reference_date: date | None = None,
    now: Callable[[], datetime] | None = None,
) -> PipelineResult:
    """Execute the pipeline end to end (or a single stage) behind a run manifest.

    Args:
        config: Loaded pipeline configuration.
        source_folder: The ``Source_Folder`` to process.
        run_id: Explicit run id; generated when omitted (or loaded on resume).
        resume: When ``True``, load the existing manifest for ``run_id`` and skip
            files already completed for a stage.
        stage: Optional single stage to run (see :func:`_stages_to_run`).
        state_dir: Directory for the run manifest (default ``.pipeline-state/``).
        migrate: Apply schema migrations idempotently before Load (default True).
        seed_data: Apply reference-data seed idempotently before Load (default True).
        review_dir: Directory for the flagged-record review artifact (default
            alongside the manifest state dir).
        llm_generate_fn: Model entry point for image extraction (injectable).
        s3_client: Injectable S3 client for Archive (moto in tests).
        connection_factory: Builds a DB connection from config (defaults to
            :func:`funhouse_pipeline.db.connect`). Injectable for tests.
        retry_policy: Backoff policy for Bedrock/S3 calls.
        sleep: Sleep function for retries (tests pass a no-op).
        reference_date: "Today" for date validation (reproducible when set).
        now: Clock for record timestamps (injectable).

    Returns:
        A :class:`PipelineResult` with per-stage outputs and summary counts.

    Raises:
        UnrecoverablePipelineError: On an unrecoverable run error (e.g. DB
            connection/auth failure when Load is in scope).
    """
    stages = _stages_to_run(stage)
    policy = retry_policy or RetryPolicy()
    sleep_fn = sleep or __import__("time").sleep
    clock = now or (lambda: datetime.now(timezone.utc))

    # --- Manifest: create fresh or resume an existing run --------------------
    if resume:
        if not run_id:
            raise UnrecoverablePipelineError(
                "--resume requires a --resume <run_id> value to locate the manifest."
            )
        manifest = RunManifest.load(run_id, state_dir)
    else:
        run_id = run_id or uuid.uuid4().hex
        manifest = RunManifest.create(
            run_id,
            source_folder=str(source_folder),
            config_path="",
            base=state_dir,
        )
    manifest.save(state_dir)

    result = PipelineResult(run_id=run_id, manifest=manifest, stages_run=stages)
    summary = {
        "collected": 0,
        "extracted": 0,
        "flagged": 0,
        "loaded": 0,
        "skipped": 0,
        "archived": 0,
    }

    conn_factory = connection_factory or _default_connection_factory
    conn: Any | None = None

    def _need_db() -> bool:
        return "load" in stages or "extract" in stages

    try:
        # ------------------------------------------------------------------ #
        # DB connection + idempotent migrate/seed (needed by Extract for known
        # names and by Load). Failure here is unrecoverable only if Load is in
        # scope; for extract-only known-name lookups it degrades gracefully.
        # ------------------------------------------------------------------ #
        if _need_db():
            try:
                conn = conn_factory(config)
            except Exception as exc:  # noqa: BLE001
                if "load" in stages:
                    raise UnrecoverablePipelineError(
                        "Could not connect to PostgreSQL for the Load stage: "
                        f"{exc}. Check the database configuration (host/port/"
                        "credentials) and that the server is reachable."
                    ) from exc
                conn = None  # extract can proceed with no known names
            if conn is not None and "load" in stages:
                _prepare_database(conn, migrate=migrate, seed_data=seed_data)

        # ------------------------------------------------------------------ #
        # 1. COLLECT (offline)
        # ------------------------------------------------------------------ #
        collect_result = _run_collect(source_folder, manifest, summary)
        result.collect_result = collect_result
        manifest.save(state_dir)

        # ------------------------------------------------------------------ #
        # 2. EXTRACT (Bedrock image path + .docx text path)
        # ------------------------------------------------------------------ #
        records: list[ExtractedRecord] = []
        if "extract" in stages:
            rules = _build_rules(conn)
            records = _run_extract(
                collect_result,
                manifest,
                summary,
                rules=rules,
                config=config,
                llm_generate_fn=llm_generate_fn,
                policy=policy,
                sleep_fn=sleep_fn,
                clock=clock,
                resume=resume,
            )
            result.records = records
            manifest.save(state_dir)

        # ------------------------------------------------------------------ #
        # 3. VALIDATE (deterministic, offline)
        # ------------------------------------------------------------------ #
        part: Partition | None = None
        if "validate" in stages:
            rules = _build_rules(conn)
            part = partition(
                records,
                rules,
                threshold=config.confidence_threshold,
                reference_date=reference_date,
            )
            result.partition = part
            summary["flagged"] = len(part.flagged)
            for vr in part.flagged:
                manifest.mark_record(
                    vr.record.record_id,
                    table=vr.record.target_table,
                    status="flagged",
                    reason=";".join(vr.reasons),
                    source_file=vr.record.source_file,
                )
                manifest.record_skip(
                    "validate", vr.record.record_id, ";".join(vr.reasons)
                )
            review_base = review_dir if review_dir is not None else RunManifest.state_dir(state_dir)
            artifact = write_review_artifact(part, run_id, review_base)
            result.review_artifact = str(artifact)
            manifest.complete_stage("validate")
            manifest.save(state_dir)

        # ------------------------------------------------------------------ #
        # 4. LOAD (deterministic, idempotent, no LLM)
        # ------------------------------------------------------------------ #
        if "load" in stages:
            if conn is None:
                raise UnrecoverablePipelineError(
                    "Load stage requires a database connection but none is available."
                )
            clean = list(part.clean) if part is not None else []
            result.load_result = _run_load(
                clean, conn, manifest, summary, config=config
            )
            manifest.save(state_dir)

        # ------------------------------------------------------------------ #
        # 5. ARCHIVE (S3 raw/ prefix, idempotent, retried)
        # ------------------------------------------------------------------ #
        if "archive" in stages:
            result.archive_result = _run_archive(
                collect_result,
                manifest,
                summary,
                config=config,
                s3_client=s3_client,
                policy=policy,
                sleep_fn=sleep_fn,
                resume=resume,
            )
            manifest.save(state_dir)

        manifest.summary = dict(summary)
        result.summary = dict(summary)
        manifest.save(state_dir)
        return result
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Stage helpers
# --------------------------------------------------------------------------- #


def _default_connection_factory(config: Config) -> Any:
    from funhouse_pipeline.db.connection import connect

    # Autocommit so each loader ``conn.transaction()`` block is a real per-record
    # transaction that persists (design § Error Handling: "each record loads in
    # its own transaction"). Migrations/seed call ``commit()`` which is a no-op
    # under autocommit; their DDL/DML commits statement-by-statement.
    return connect(config, autocommit=True)


def _prepare_database(conn: Any, *, migrate: bool, seed_data: bool) -> None:
    """Idempotently apply schema + seed before Load (design § Idempotency)."""
    from funhouse_pipeline.db.migrations import run_migrations
    from funhouse_pipeline.db.seed import seed as seed_reference_data

    if migrate:
        run_migrations(conn)
    if seed_data:
        seed_reference_data(conn)


def _build_rules(conn: Any | None) -> BusinessRules:
    known = _known_player_names(conn) if conn is not None else ()
    return build_business_rules(known)


def _run_collect(
    source_folder: str | Path,
    manifest: RunManifest,
    summary: dict[str, int],
) -> CollectResult:
    manifest.start_stage("collect")
    collect_result = collect(source_folder)
    for rf in collect_result.routed:
        manifest.register_file(
            str(rf.path), handler=rf.handler.value, subfolder=rf.subfolder
        )
        manifest.set_file_status(str(rf.path), "collect", STATUS_DONE)
    for sk in collect_result.skipped:
        manifest.record_skip("collect", str(sk.path), sk.reason)
    for absent in collect_result.absent_subfolders:
        manifest.record_skip("collect", absent, "subfolder absent")
    summary["collected"] = len(collect_result.routed)
    manifest.complete_stage("collect")
    return collect_result


def _run_extract(
    collect_result: CollectResult,
    manifest: RunManifest,
    summary: dict[str, int],
    *,
    rules: BusinessRules,
    config: Config,
    llm_generate_fn: Callable[..., Any],
    policy: RetryPolicy,
    sleep_fn: Callable[[float], None],
    clock: Callable[[], datetime],
    resume: bool,
) -> list[ExtractedRecord]:
    manifest.start_stage("extract")
    records: list[ExtractedRecord] = []

    image_files = [
        rf
        for rf in collect_result.routed
        if rf.handler == HandlerTarget.IMAGE_EXTRACT
        and not (resume and manifest.is_file_done(str(rf.path), "extract"))
    ]
    docx_files = [
        rf
        for rf in collect_result.routed
        if rf.handler == HandlerTarget.DOCX_TEXT_PARSER
        and not (resume and manifest.is_file_done(str(rf.path), "extract"))
    ]

    # --- Image path (Bedrock Batch) with exponential-backoff retries -------- #
    if image_files:
        options = {"s3_bucket": config.s3_bucket, "region": config.region}
        retrying_llm = with_retry(
            llm_generate_fn,
            policy=policy,
            sleep=sleep_fn,
            on_retry=lambda attempt, exc, delay: manifest.record_failure(
                "extract", "bedrock-batch", f"attempt {attempt} failed: {exc}"
            ),
        )
        try:
            image_records = extract_images(
                image_files,
                business_rules=rules,
                llm_generate_fn=retrying_llm,
                provider=config.llm_provider,
                options=options,
                now=clock,
            )
            records.extend(image_records)
            for rf in image_files:
                manifest.set_file_status(str(rf.path), "extract", STATUS_DONE)
        except Exception as exc:  # noqa: BLE001 - recoverable: record + continue
            # Bedrock batch failed after retries: mark the batch's files failed
            # and continue (they are re-attempted on the next run -- idempotent).
            for rf in image_files:
                manifest.set_file_status(str(rf.path), "extract", STATUS_FAILED)
                manifest.record_failure("extract", str(rf.path), f"extract_failed: {exc}")

    # --- Lesson .docx path (per-file so a bad file is isolated) ------------- #
    for rf in docx_files:
        try:
            docx_records = extract_lessons([rf], now=clock)
            records.extend(docx_records)
            manifest.set_file_status(str(rf.path), "extract", STATUS_DONE)
        except Exception as exc:  # noqa: BLE001 - recoverable per-item error
            manifest.set_file_status(str(rf.path), "extract", STATUS_FAILED)
            manifest.record_failure("extract", str(rf.path), f"docx parse error: {exc}")

    for rec in records:
        manifest.mark_record(
            rec.record_id,
            table=rec.target_table,
            status="extracted",
            source_file=rec.source_file,
        )
    summary["extracted"] = len(records)
    manifest.complete_stage("extract")
    return records


def _run_load(
    clean: list[Any],
    conn: Any,
    manifest: RunManifest,
    summary: dict[str, int],
    *,
    config: Config,
) -> Any:
    from funhouse_pipeline.db.seed import SMITHFIELD_LOCATION
    from funhouse_pipeline.load import load_clean_records

    manifest.start_stage("load")

    location_id = _lookup_id(
        conn, "SELECT id FROM locations WHERE name = %s", (SMITHFIELD_LOCATION,)
    )
    if location_id is None:
        raise UnrecoverablePipelineError(
            "No 'Smithfield' location found. Run the seed step (enable --seed) "
            "before loading, so records have a valid location_id to reference."
        )
    # Aya (founder) is the acting operator identity for backfill writes (Req 14.5).
    logged_by = _lookup_id(conn, "SELECT id FROM users WHERE name = %s", ("Aya",))

    rules = _build_rules(conn)
    load_result = load_clean_records(
        clean,
        conn,
        location_id=location_id,
        rules=rules,
        logged_by=logged_by,
    )

    for row in load_result.loaded:
        manifest.mark_record(row.record_id, table=row.table, status="loaded")
    for sk in load_result.skipped:
        manifest.mark_record(sk.record_id, table=sk.table, status="skipped", reason=sk.reason)
        manifest.record_skip("load", sk.record_id, sk.reason)
    for fl in load_result.flagged:
        manifest.mark_record(fl.record_id, table=fl.table, status="flagged", reason=fl.reason)
        manifest.record_skip("load", fl.record_id, f"{fl.reason}: {fl.detail}")

    summary["loaded"] = len(load_result.loaded)
    summary["skipped"] = len(load_result.skipped)
    manifest.complete_stage("load")
    return load_result


def _run_archive(
    collect_result: CollectResult,
    manifest: RunManifest,
    summary: dict[str, int],
    *,
    config: Config,
    s3_client: Any | None,
    policy: RetryPolicy,
    sleep_fn: Callable[[float], None],
    resume: bool,
) -> ArchiveResult:
    manifest.start_stage("archive")

    if not config.s3_bucket:
        raise UnrecoverablePipelineError(
            "Archive stage requires an S3 bucket. Set 's3_bucket' in the config "
            "(or the S3_BUCKET env var) so originals can be archived under raw/."
        )

    archiver = Archiver(
        bucket=config.s3_bucket, s3_client=s3_client, region=config.region
    )

    files = [
        rf
        for rf in collect_result.routed
        if not (resume and manifest.is_file_done(str(rf.path), "archive"))
    ]

    objects: list[ArchivedObject] = []
    for rf in files:
        try:
            # Per-file exponential-backoff retry for transient S3 errors. The
            # head+put in archive_file is idempotent, so re-attempts are safe.
            obj = retry_call(
                lambda rf=rf: archiver.archive_file(rf),
                policy=policy,
                sleep=sleep_fn,
                on_retry=lambda attempt, exc, delay, rf=rf: manifest.record_failure(
                    "archive", str(rf.path), f"attempt {attempt} failed: {exc}"
                ),
            )
            objects.append(obj)
            manifest.set_file_status(str(rf.path), "archive", STATUS_DONE)
        except Exception as exc:  # noqa: BLE001
            # Persistent S3 failure: originals must not be lost before load is
            # trusted (design). Halt the run with an actionable message.
            manifest.set_file_status(str(rf.path), "archive", STATUS_FAILED)
            manifest.record_failure("archive", str(rf.path), f"archive_failed: {exc}")
            raise UnrecoverablePipelineError(
                f"Failed to archive {rf.path} after {policy.max_attempts} attempts: "
                f"{exc}. Originals must be safely archived before the run is trusted; "
                "resolve the S3/network issue and re-run (archival is idempotent)."
            ) from exc

    archive_result = ArchiveResult(objects=tuple(objects), bucket=config.s3_bucket)
    summary["archived"] = len(archive_result.objects)
    manifest.complete_stage("archive")
    return archive_result
