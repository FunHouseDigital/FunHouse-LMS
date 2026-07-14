"""Orchestrator: the single documented, re-runnable command (Task 14).

Wires the five stages -- Collect -> Extract -> Validate -> Load -> Archive --
into one re-runnable flow behind a resumable run manifest, with recoverable vs
unrecoverable error handling and exponential-backoff retries for the remote
(Bedrock/S3) calls (Req 13.1, 13.3, 13.4).

Public surface:
- :func:`run_pipeline` -- programmatic end-to-end (or single-stage) execution.
- :func:`main` -- the ``funhouse-pipeline`` console entry point.
- :class:`RunManifest` -- the persisted per-file/per-record run state.
- :class:`RetryPolicy` / :func:`retry_call` / :func:`with_retry` -- the backoff helper.
"""

from funhouse_pipeline.orchestrator.cli import build_parser, main
from funhouse_pipeline.orchestrator.manifest import (
    DEFAULT_STATE_DIR,
    STAGES,
    RunManifest,
)
from funhouse_pipeline.orchestrator.pipeline import (
    ALL_STAGES,
    PipelineResult,
    UnrecoverablePipelineError,
    run_pipeline,
)
from funhouse_pipeline.orchestrator.retry import (
    DEFAULT_RETRYABLE,
    RetryPolicy,
    retry_call,
    with_retry,
)

__all__ = [
    # command
    "main",
    "build_parser",
    "run_pipeline",
    "PipelineResult",
    "UnrecoverablePipelineError",
    "ALL_STAGES",
    # manifest
    "RunManifest",
    "STAGES",
    "DEFAULT_STATE_DIR",
    # retries
    "RetryPolicy",
    "retry_call",
    "with_retry",
    "DEFAULT_RETRYABLE",
]
