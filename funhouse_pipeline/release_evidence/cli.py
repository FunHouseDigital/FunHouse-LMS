"""Command-line interface for release-evidence collection and validation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .core import EvidenceFormatError, render_markdown, validate_snapshot
from .github import GitHubAPIError, collect_snapshot


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if value.endswith("\n") else f"{value}\n", encoding="utf-8")


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceFormatError(f"snapshot is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise EvidenceFormatError("snapshot root must be a JSON object")
    return value


def _actions_provenance() -> dict[str, str]:
    mapping = {
        "event_name": "GITHUB_EVENT_NAME",
        "ref": "GITHUB_REF",
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "validator_sha": "GITHUB_SHA",
        "workflow": "GITHUB_WORKFLOW",
        "workflow_ref": "GITHUB_WORKFLOW_REF",
    }
    return {
        snapshot_key: value
        for snapshot_key, environment_key in mapping.items()
        if (value := os.environ.get(environment_key))
    }


def _add_collection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repository in owner/name form (defaults to GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--candidate-sha",
        help="lowercase full commit SHA (defaults to the current main SHA)",
    )
    parser.add_argument("--snapshot", required=True, type=Path, help="snapshot JSON output")


def _add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-json", required=True, type=Path, help="JSON report output")
    parser.add_argument(
        "--report-markdown", required=True, type=Path, help="Markdown report output"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="funhouse-release-evidence",
        description="Collect and validate secret-free exact-SHA Phase 1 release evidence.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="collect a normalised GitHub REST snapshot")
    _add_collection_arguments(collect)

    validate = commands.add_parser("validate", help="validate a snapshot without network access")
    validate.add_argument("--snapshot", required=True, type=Path, help="snapshot JSON input")
    _add_report_arguments(validate)

    check = commands.add_parser("check", help="collect and immediately validate evidence")
    _add_collection_arguments(check)
    _add_report_arguments(check)
    return parser


def _validate_and_write(
    snapshot: dict[str, Any], report_json: Path, report_markdown: Path
) -> int:
    report = validate_snapshot(snapshot)
    _write_json(report_json, report)
    _write_text(report_markdown, render_markdown(report))
    verdict = report.get("automated_verdict")
    print(f"Automated release-evidence verdict: {verdict}")
    print("Final Phase 1 GO remains manual.")
    return 0 if verdict == "PASS" else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command, returning 0 for PASS, 1 for BLOCKED, and 2 for errors."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        if args.command in {"collect", "check"} and not args.repository:
            parser.error("--repository is required when GITHUB_REPOSITORY is not set")

        if args.command == "collect":
            snapshot = collect_snapshot(
                args.repository,
                candidate_sha=args.candidate_sha,
                token=os.environ.get("GITHUB_TOKEN"),
                provenance=_actions_provenance(),
            )
            _write_json(args.snapshot, snapshot)
            print(f"Wrote secret-free snapshot to {args.snapshot}")
            return 0

        if args.command == "validate":
            snapshot = _read_snapshot(args.snapshot)
            return _validate_and_write(snapshot, args.report_json, args.report_markdown)

        snapshot = collect_snapshot(
            args.repository,
            candidate_sha=args.candidate_sha,
            token=os.environ.get("GITHUB_TOKEN"),
            provenance=_actions_provenance(),
        )
        _write_json(args.snapshot, snapshot)
        return _validate_and_write(snapshot, args.report_json, args.report_markdown)
    except SystemExit as exc:
        return int(exc.code)
    except (EvidenceFormatError, GitHubAPIError, OSError, TypeError, ValueError, KeyError) as exc:
        print(f"release-evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
