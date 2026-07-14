"""The documented, re-runnable command (Task 14.1/14.3, Req 13.1/13.2).

Exposes the single console entry point registered in ``pyproject.toml`` as
``funhouse-pipeline``::

    funhouse-pipeline run \\
        --source-folder <path> \\
        --config <path/to/config.yaml> \\
        [--stage collect|extract|validate|load|archive] \\
        [--resume <run_id>] \\
        [--no-migrate] [--no-seed] \\
        [--state-dir <path>]

The command runs Collect -> Extract -> Validate -> Load -> Archive end to end
over the given ``Source_Folder`` (Req 13.1), threading a resumable run manifest
through every stage, and prints a run summary (counts of collected, extracted,
flagged, loaded, skipped, archived records). See ``docs/pipeline-command.md`` for
the full written documentation (Req 13.2).

This module only parses arguments and formats output; the orchestration lives in
:mod:`funhouse_pipeline.orchestrator.pipeline`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Sequence

from funhouse_pipeline.config import ConfigError, load_config
from funhouse_pipeline.orchestrator.pipeline import (
    ALL_STAGES,
    UnrecoverablePipelineError,
    run_pipeline,
)

PROG = "funhouse-pipeline"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the documented command."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "FunHouse Phase 0 document-intelligence pipeline: one re-runnable "
            "command that runs Collect -> Extract -> Validate -> Load -> Archive "
            "over a Source_Folder."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser(
        "run",
        help="Run the pipeline end to end over a Source_Folder.",
        description="Run the pipeline end to end (or a single stage) over a Source_Folder.",
    )
    run.add_argument(
        "--source-folder",
        required=True,
        help="Path to the Source_Folder (contains cards/ sheets/ lessons/ photos/ whatsapp/).",
    )
    run.add_argument(
        "--config",
        default=None,
        help="Path to a YAML config file. Env vars override file values.",
    )
    run.add_argument(
        "--stage",
        choices=ALL_STAGES,
        default=None,
        help=(
            "Run a single stage (plus its cheap prerequisites). Omit to run all "
            "five stages end to end."
        ),
    )
    run.add_argument(
        "--resume",
        metavar="RUN_ID",
        default=None,
        help="Resume a prior run using its manifest; skips work already completed.",
    )
    run.add_argument(
        "--run-id",
        default=None,
        help="Use an explicit run id (default: a generated id).",
    )
    run.add_argument(
        "--state-dir",
        default=None,
        help="Directory for the run manifest (default: .pipeline-state/).",
    )
    run.add_argument(
        "--reference-date",
        default=None,
        help="Reference 'today' (YYYY-MM-DD) for date validation (default: today).",
    )

    # migrate / seed toggles (idempotent, on by default).
    run.add_argument(
        "--migrate",
        dest="migrate",
        action="store_true",
        default=True,
        help="Apply schema migrations idempotently before Load (default: on).",
    )
    run.add_argument(
        "--no-migrate",
        dest="migrate",
        action="store_false",
        help="Assume the schema is already deployed; skip migrations.",
    )
    run.add_argument(
        "--seed",
        dest="seed",
        action="store_true",
        default=True,
        help="Apply reference-data seed idempotently before Load (default: on).",
    )
    run.add_argument(
        "--no-seed",
        dest="seed",
        action="store_false",
        help="Assume reference data is already seeded; skip seeding.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "run":  # pragma: no cover - argparse enforces subcommand
        parser.error("unknown command")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    reference_date: date | None = None
    if args.reference_date:
        try:
            reference_date = date.fromisoformat(args.reference_date)
        except ValueError:
            print(
                f"Invalid --reference-date {args.reference_date!r}; expected YYYY-MM-DD.",
                file=sys.stderr,
            )
            return 2

    resume = args.resume is not None
    run_id = args.resume if resume else args.run_id

    try:
        result = run_pipeline(
            config,
            args.source_folder,
            run_id=run_id,
            resume=resume,
            stage=args.stage,
            state_dir=args.state_dir,
            migrate=args.migrate,
            seed_data=args.seed,
            reference_date=reference_date,
        )
    except UnrecoverablePipelineError as exc:
        print(f"Pipeline halted (unrecoverable): {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1

    print(result.summary_text())
    if result.review_artifact:
        print(f"\nFlagged records for review: {result.review_artifact}")
    manifest_path = result.manifest._path
    if manifest_path:
        print(f"Run manifest: {manifest_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
