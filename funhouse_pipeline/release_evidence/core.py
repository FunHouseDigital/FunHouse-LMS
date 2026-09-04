"""Pure exact-SHA Phase 1 release-evidence validation and rendering.

The validator consumes a versioned snapshot collected from public/read-only
GitHub REST endpoints. It performs no network access and has no clock dependency:
``as_of`` is part of the snapshot so fixture results remain deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

SNAPSHOT_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BROWSER_TITLE_RE = re.compile(
    r"^Verify Live PWA Browser · (applied-or-skipped|skipped) · ([0-9a-f]{40})$"
)
MAX_PREFLIGHT_AGE = timedelta(days=7)
EXPECTED_REPOSITORY = "FunHouseDigital/FunHouse-LMS"
EXPECTED_EVIDENCE_WORKFLOW = "Validate Phase 1 Release Evidence"
EXPECTED_EVIDENCE_WORKFLOW_PATH = ".github/workflows/validate-phase1-release-evidence.yml"

WORKFLOW_CONTRACT = {
    "ci": ("CI", ".github/workflows/ci.yml"),
    "api_role": ("Verify Live API Role Access", ".github/workflows/verify-live-api-rbac.yml"),
    "browser": ("Verify Live PWA Browser", ".github/workflows/verify-live-pwa-browser.yml"),
    "preflight": (
        "Prepare Phase 1 Field Acceptance",
        ".github/workflows/verify-live-runtime-db.yml",
    ),
}

VERCEL_PROJECTS = {
    "api": {
        "context": "Vercel – fun-house-lms",
        "target_prefix": "https://vercel.com/fun-house-digital/fun-house-lms/",
        "environment": "Production – fun-house-lms",
    },
    "pwa": {
        "context": "Vercel – funhouse-revenue-pwa",
        "target_prefix": "https://vercel.com/fun-house-digital/funhouse-revenue-pwa/",
        "environment": "Production – funhouse-revenue-pwa",
    },
}

MANUAL_GATES = [
    "Supabase Security Advisor observation is no more than seven days old and shows zero errors and zero warnings.",
    "The founder confirms no out-of-band migration, role, membership, grant, ownership, function, or policy change occurred after the recorded database evidence.",
    "Separate founder and Loyiso password-manager entries are available; no password is entered into this validator.",
    "The approved lounge device passes install/upgrade, account-transition, true-radio-offline, process-relaunch, five-card, reconciliation, and operator-pace checks using synthetic data only.",
    "The founder and operator record the final GO; automated PASS alone is never final Phase 1 acceptance.",
]


class EvidenceFormatError(ValueError):
    """Raised when a snapshot is malformed or uses an unsupported schema."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceFormatError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise EvidenceFormatError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceFormatError(f"{label} must be a non-empty string")
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvidenceFormatError(f"{label} must be an integer")
    return value


def _time(value: Any, label: str) -> datetime:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceFormatError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EvidenceFormatError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _run_start(run: Mapping[str, Any]) -> datetime:
    return _time(run.get("run_started_at") or run.get("created_at"), "workflow run start")


def _run_end(run: Mapping[str, Any]) -> datetime:
    return _time(run.get("updated_at"), "workflow run updated_at")


def _item_key(item: Mapping[str, Any], timestamp_field: str = "created_at") -> tuple[datetime, int]:
    return (_time(item.get(timestamp_field), timestamp_field), _integer(item.get("id"), "id"))


def _latest(items: Iterable[Mapping[str, Any]], timestamp_field: str = "created_at") -> Mapping[str, Any] | None:
    present = list(items)
    return max(present, key=lambda item: _item_key(item, timestamp_field)) if present else None


def _is_exact_run(run: Mapping[str, Any], candidate: str, event: str) -> bool:
    return (
        run.get("head_sha") == candidate
        and run.get("head_branch") == "main"
        and run.get("event") == event
    )


def _is_success(run: Mapping[str, Any]) -> bool:
    return run.get("status") == "completed" and run.get("conclusion") == "success"


def _evidence_link(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {}
    return {
        key: item.get(key)
        for key in ("id", "html_url", "created_at", "run_started_at", "updated_at")
        if item.get(key) is not None
    }


def _check(
    checks: list[dict[str, Any]],
    code: str,
    passed: bool,
    summary: str,
    evidence: Mapping[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "code": code,
            "status": "PASS" if passed else "FAIL",
            "summary": summary,
            "evidence": dict(evidence or {}),
        }
    )


def _workflow_runs(snapshot: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    all_runs = _mapping(snapshot.get("workflow_runs"), "workflow_runs")
    return [
        _mapping(item, f"workflow_runs.{key} item")
        for item in _sequence(all_runs.get(key), f"workflow_runs.{key}")
    ]


def _validate_workflow_contract(snapshot: Mapping[str, Any], checks: list[dict[str, Any]]) -> None:
    definitions = _mapping(snapshot.get("workflow_definitions"), "workflow_definitions")
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for key, (expected_name, expected_path) in WORKFLOW_CONTRACT.items():
        definition = _mapping(definitions.get(key), f"workflow_definitions.{key}")
        evidence[key] = {
            "id": definition.get("id"),
            "name": definition.get("name"),
            "path": definition.get("path"),
            "state": definition.get("state"),
            "html_url": definition.get("html_url"),
        }
        if (
            definition.get("name") != expected_name
            or definition.get("path") != expected_path
            or definition.get("state") != "active"
        ):
            failures.append(key)
    _check(
        checks,
        "workflow_contract",
        not failures,
        "All required evidence workflows are active with their reviewed names and paths."
        if not failures
        else "Workflow contract mismatch: " + ", ".join(failures),
        evidence,
    )


def _select_ci(snapshot: Mapping[str, Any], candidate: str) -> tuple[Mapping[str, Any] | None, bool, str]:
    runs = [run for run in _workflow_runs(snapshot, "ci") if _is_exact_run(run, candidate, "push")]
    latest = _latest(runs)
    if latest is None:
        return None, False, "No exact-SHA main push CI run was found."
    if not _is_success(latest):
        return latest, False, "The newest exact-SHA main push CI run is not successful."

    jobs_by_run = _mapping(snapshot.get("ci_jobs"), "ci_jobs")
    jobs = [
        _mapping(item, "ci job")
        for item in _sequence(jobs_by_run.get(str(latest["id"]), []), "ci_jobs entry")
    ]
    web = next((job for job in jobs if job.get("name") == "web-tests (npm test + build)"), None)
    if web is None or web.get("status") != "completed" or web.get("conclusion") != "success":
        return latest, False, "The exact-SHA CI run did not complete the required web-tests job successfully."
    steps = [
        _mapping(step, "CI step")
        for step in _sequence(web.get("steps"), "web-tests steps")
    ]
    gate = next(
        (
            step
            for step in steps
            if step.get("name") == "Production build + hermetic five-session offline gate"
        ),
        None,
    )
    if gate is None or gate.get("status") != "completed" or gate.get("conclusion") != "success":
        return latest, False, "The credential-free five-session browser gate did not complete successfully."
    return latest, True, "The newest exact-SHA main CI run includes a successful hermetic five-session browser gate."


def _select_vercel_statuses(
    snapshot: Mapping[str, Any], checks: list[dict[str, Any]]
) -> dict[str, Mapping[str, Any] | None]:
    statuses = [
        _mapping(item, "commit status")
        for item in _sequence(snapshot.get("commit_statuses"), "commit_statuses")
    ]
    selected: dict[str, Mapping[str, Any] | None] = {}
    for project, contract in VERCEL_PROJECTS.items():
        status = _latest(item for item in statuses if item.get("context") == contract["context"])
        selected[project] = status
        passed = bool(
            status
            and status.get("state") == "success"
            and status.get("creator") == "vercel[bot]"
            and isinstance(status.get("target_url"), str)
            and status["target_url"].startswith(contract["target_prefix"])
        )
        _check(
            checks,
            f"vercel_{project}_commit_status",
            passed,
            f"Newest {project.upper()} Vercel commit status is successful and targets the approved project."
            if passed
            else f"Newest {project.upper()} Vercel commit status is missing, unsuccessful, or targets an unapproved project.",
            _evidence_link(status)
            | (
                {
                    "state": status.get("state"),
                    "creator": status.get("creator"),
                    "target_url": status.get("target_url"),
                }
                if status
                else {}
            ),
        )
    return selected


def _select_vercel_deployments(
    snapshot: Mapping[str, Any], candidate: str, checks: list[dict[str, Any]]
) -> dict[str, tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]]:
    deployments = [
        _mapping(item, "deployment")
        for item in _sequence(snapshot.get("deployments"), "deployments")
    ]
    selected: dict[str, tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]] = {}
    for project, contract in VERCEL_PROJECTS.items():
        deployment = _latest(
            item
            for item in deployments
            if item.get("environment") == contract["environment"]
        )
        status: Mapping[str, Any] | None = None
        if deployment is not None:
            status = _latest(
                _mapping(item, "deployment status")
                for item in _sequence(deployment.get("statuses"), "deployment statuses")
            )
        selected[project] = (deployment, status)
        passed = bool(
            deployment
            and deployment.get("sha") == candidate
            and deployment.get("creator") == "vercel[bot]"
            and status
            and status.get("state") == "success"
            and status.get("creator") == "vercel[bot]"
        )
        evidence = _evidence_link(deployment)
        if deployment:
            evidence.update(
                {
                    "sha": deployment.get("sha"),
                    "environment": deployment.get("environment"),
                    "creator": deployment.get("creator"),
                }
            )
        if status:
            evidence["status"] = {
                **_evidence_link(status),
                "state": status.get("state"),
                "creator": status.get("creator"),
            }
        _check(
            checks,
            f"vercel_{project}_production_deployment",
            passed,
            f"Newest {project.upper()} Production deployment is exact-SHA, Vercel-bot-created, and successful."
            if passed
            else f"Newest {project.upper()} Production deployment is missing, not candidate-bound, not Vercel-created, or unsuccessful.",
            evidence,
        )
    return selected


def _select_role_run(
    snapshot: Mapping[str, Any],
    candidate: str,
    api_deployed_at: datetime | None,
) -> tuple[Mapping[str, Any] | None, bool, str]:
    runs = [
        run
        for run in _workflow_runs(snapshot, "api_role")
        if _is_exact_run(run, candidate, "workflow_dispatch")
    ]
    run = _latest(runs)
    if run is None:
        return None, False, "No exact-SHA API role-verification run was found."
    if not _is_success(run):
        return run, False, "The newest exact-SHA API role-verification run is not successful."
    if api_deployed_at is None or _run_start(run) < api_deployed_at:
        return run, False, "API role verification did not start after the successful API Production deployment."
    return run, True, "API role verification passed after the successful API Production deployment."


def _browser_mode(run: Mapping[str, Any], candidate: str) -> str | None:
    title = run.get("display_title")
    if not isinstance(title, str):
        return None
    match = BROWSER_TITLE_RE.fullmatch(title)
    if not match or match.group(2) != candidate:
        return None
    return match.group(1)


def _select_browser_chain(
    snapshot: Mapping[str, Any],
    candidate: str,
    role_run: Mapping[str, Any] | None,
    deployments_ready_at: datetime | None,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, bool, str]:
    runs = [
        run
        for run in _workflow_runs(snapshot, "browser")
        if _is_exact_run(run, candidate, "workflow_dispatch")
    ]
    first = _latest(run for run in runs if _browser_mode(run, candidate) == "applied-or-skipped")
    replay = _latest(run for run in runs if _browser_mode(run, candidate) == "skipped")
    if first is None or replay is None:
        return first, replay, False, "Both browser modes were not observable from exact-SHA workflow run titles."
    if not _is_success(first) or not _is_success(replay):
        return first, replay, False, "The newest run for each required browser mode is not successful."
    if role_run is None or not _is_success(role_run) or deployments_ready_at is None:
        return first, replay, False, "Browser evidence cannot be ordered until deployment and API-role evidence passes."
    prerequisite = max(_run_end(role_run), deployments_ready_at)
    if _run_start(first) < prerequisite:
        return first, replay, False, "The first browser run started before deployment/API-role prerequisites completed."
    if _run_start(replay) < _run_end(first):
        return first, replay, False, "The skipped replay started before the first browser run completed."
    return first, replay, True, "The first and skipped-replay browser runs passed in the required order."


def _select_preflight(
    snapshot: Mapping[str, Any], candidate: str, as_of: datetime
) -> tuple[Mapping[str, Any] | None, bool, str, float | None]:
    runs = [
        run
        for run in _workflow_runs(snapshot, "preflight")
        if _is_exact_run(run, candidate, "workflow_dispatch")
    ]
    run = _latest(runs)
    if run is None:
        return None, False, "No exact-SHA Phase 1 database preflight was found.", None
    if not _is_success(run):
        return run, False, "The newest exact-SHA database preflight is not successful.", None
    age = as_of - _run_end(run)
    age_hours = age.total_seconds() / 3600
    if age < timedelta(0):
        return run, False, "The database preflight timestamp is later than the validation as-of time.", age_hours
    if age > MAX_PREFLIGHT_AGE:
        return run, False, "The database preflight is older than seven days.", age_hours
    return run, True, "The exact-SHA database preflight passed and is no older than seven days.", age_hours


def _future_evidence(
    as_of: datetime,
    evidence: Sequence[tuple[str, Mapping[str, Any] | None, str]],
) -> list[str]:
    future: list[str] = []
    for label, item, timestamp_field in evidence:
        if item is not None and _time(item.get(timestamp_field), f"{label} {timestamp_field}") > as_of:
            future.append(label)
    return future


def _validate_provenance(
    root: Mapping[str, Any], repository: str, candidate: str
) -> tuple[bool, dict[str, Any]]:
    provenance = _mapping(root.get("collection_provenance"), "collection_provenance")
    expected_workflow_ref = (
        f"{repository}/{EXPECTED_EVIDENCE_WORKFLOW_PATH}@refs/heads/main"
    )
    evidence = {
        key: provenance.get(key)
        for key in (
            "event_name",
            "ref",
            "run_id",
            "run_attempt",
            "validator_sha",
            "workflow",
            "workflow_ref",
        )
    }
    if str(provenance.get("run_id", "")).isdigit() and str(
        provenance.get("run_attempt", "")
    ).isdigit():
        evidence["run_url"] = (
            f"https://github.com/{repository}/actions/runs/{provenance['run_id']}"
            f"/attempts/{provenance['run_attempt']}"
        )
    passed = (
        provenance.get("event_name") == "workflow_dispatch"
        and provenance.get("ref") == "refs/heads/main"
        and provenance.get("validator_sha") == candidate
        and provenance.get("workflow") == EXPECTED_EVIDENCE_WORKFLOW
        and provenance.get("workflow_ref") == expected_workflow_ref
        and str(provenance.get("run_id", "")).isdigit()
        and str(provenance.get("run_attempt", "")).isdigit()
    )
    return passed, evidence


def validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one collected snapshot and return a deterministic report."""

    root = _mapping(snapshot, "snapshot")
    if root.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise EvidenceFormatError(
            f"unsupported snapshot schema_version {root.get('schema_version')!r}"
        )
    candidate = _string(root.get("candidate_sha"), "candidate_sha")
    if not SHA_RE.fullmatch(candidate):
        raise EvidenceFormatError("candidate_sha must be a lowercase full 40-character Git SHA")
    as_of_text = _string(root.get("as_of"), "as_of")
    as_of = _time(as_of_text, "as_of")
    collected_at_text = _string(root.get("collected_at"), "collected_at")
    collected_at = _time(collected_at_text, "collected_at")
    if collected_at < as_of:
        raise EvidenceFormatError("collected_at must not be earlier than as_of")
    repository = _string(root.get("repository"), "repository")
    state = _mapping(root.get("repository_state"), "repository_state")

    checks: list[dict[str, Any]] = []
    repository_ok = repository == EXPECTED_REPOSITORY
    _check(
        checks,
        "repository_identity",
        repository_ok,
        "Evidence belongs to the approved production repository."
        if repository_ok
        else "Evidence belongs to an unapproved repository.",
        {"repository": repository, "expected_repository": EXPECTED_REPOSITORY},
    )
    provenance_ok, provenance_evidence = _validate_provenance(root, repository, candidate)
    _check(
        checks,
        "collector_provenance",
        provenance_ok,
        "Snapshot provenance fields match the main-bound workflow contract. External run/artifact verification is still required."
        if provenance_ok
        else "Snapshot provenance fields do not match a main-bound workflow run at the candidate SHA.",
        provenance_evidence,
    )
    exact_main = (
        state.get("default_branch") == "main"
        and state.get("initial_main_sha") == candidate
        and state.get("current_main_sha") == candidate
    )
    _check(
        checks,
        "current_main",
        exact_main,
        "Candidate is the repository's exact current main commit."
        if exact_main
        else "Candidate is not the exact current main commit or the default branch is not main.",
        {
            "default_branch": state.get("default_branch"),
            "initial_main_sha": state.get("initial_main_sha"),
            "current_main_sha": state.get("current_main_sha"),
            "candidate_sha": candidate,
        },
    )
    _validate_workflow_contract(root, checks)

    ci_run, ci_ok, ci_summary = _select_ci(root, candidate)
    _check(checks, "main_ci", ci_ok, ci_summary, _evidence_link(ci_run))

    commit_statuses = _select_vercel_statuses(root, checks)
    deployments = _select_vercel_deployments(root, candidate, checks)
    api_status = deployments["api"][1]
    pwa_status = deployments["pwa"][1]
    api_deployed_at = _time(api_status.get("created_at"), "API deployment status created_at") if api_status else None
    deployments_ready_at = None
    if api_status and pwa_status and api_status.get("state") == pwa_status.get("state") == "success":
        deployments_ready_at = max(
            _time(api_status.get("created_at"), "API deployment status created_at"),
            _time(pwa_status.get("created_at"), "PWA deployment status created_at"),
        )

    role_run, role_ok, role_summary = _select_role_run(root, candidate, api_deployed_at)
    _check(checks, "api_role_verification", role_ok, role_summary, _evidence_link(role_run))

    first, replay, browser_ok, browser_summary = _select_browser_chain(
        root, candidate, role_run, deployments_ready_at
    )
    _check(
        checks,
        "protected_browser_pair",
        browser_ok,
        browser_summary,
        {"first": _evidence_link(first), "replay": _evidence_link(replay)},
    )

    preflight, preflight_ok, preflight_summary, age_hours = _select_preflight(
        root, candidate, collected_at
    )
    future = _future_evidence(
        as_of,
        [
            ("ci", ci_run, "updated_at"),
            ("api commit status", commit_statuses["api"], "created_at"),
            ("pwa commit status", commit_statuses["pwa"], "created_at"),
            ("api deployment", deployments["api"][0], "created_at"),
            ("pwa deployment", deployments["pwa"][0], "created_at"),
            ("api deployment status", api_status, "created_at"),
            ("pwa deployment status", pwa_status, "created_at"),
            ("api role", role_run, "updated_at"),
            ("browser first", first, "updated_at"),
            ("browser replay", replay, "updated_at"),
            ("database preflight", preflight, "updated_at"),
        ],
    )
    _check(
        checks,
        "evidence_as_of",
        not future,
        "All selected evidence existed by the post-collection as-of time."
        if not future
        else "Evidence is later than the snapshot as-of time: " + ", ".join(future),
    )

    preflight_evidence = _evidence_link(preflight)
    if age_hours is not None:
        preflight_evidence["age_hours"] = round(age_hours, 3)
    _check(
        checks,
        "database_preflight",
        preflight_ok,
        preflight_summary,
        preflight_evidence,
    )

    verdict = "PASS" if all(check["status"] == "PASS" for check in checks) else "BLOCKED"
    selected = {
        "ci": _evidence_link(ci_run),
        "api_commit_status": _evidence_link(commit_statuses["api"]),
        "pwa_commit_status": _evidence_link(commit_statuses["pwa"]),
        "api_deployment": _evidence_link(deployments["api"][0]),
        "api_deployment_status": _evidence_link(api_status),
        "pwa_deployment": _evidence_link(deployments["pwa"][0]),
        "pwa_deployment_status": _evidence_link(pwa_status),
        "api_role": _evidence_link(role_run),
        "browser_first": _evidence_link(first),
        "browser_replay": _evidence_link(replay),
        "database_preflight": _evidence_link(preflight),
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "automated_verdict": verdict,
        "authority": "EXTERNAL_WORKFLOW_RUN_REQUIRED"
        if provenance_ok
        else "LOCAL_DIAGNOSTIC_ONLY",
        "repository": repository,
        "candidate_sha": candidate,
        "as_of": as_of_text,
        "collected_at": collected_at_text,
        "collection_provenance": provenance_evidence,
        "checks": checks,
        "selected_evidence": selected,
        "manual_gates": list(MANUAL_GATES),
        "final_phase1_go": "MANUAL_REQUIRED",
        "disclaimer": "Automated evidence PASS still requires external workflow-run/artifact verification, is not final Phase 1 GO, and never authorises real learner data or Phase 2.",
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact human-readable report from a validation result."""

    verdict = _string(report.get("automated_verdict"), "automated_verdict")
    authority = _string(report.get("authority"), "authority")
    candidate = _string(report.get("candidate_sha"), "candidate_sha")
    as_of = _string(report.get("as_of"), "as_of")
    collected_at = _string(report.get("collected_at"), "collected_at")
    provenance = _mapping(report.get("collection_provenance"), "collection_provenance")
    run_url = provenance.get("run_url")
    lines = [
        "# Phase 1 automated release evidence",
        "",
        f"**Automated verdict: {verdict}**",
        "",
        f"- Candidate: `{candidate}`",
        f"- Evidence cut-off: `{as_of}`",
        f"- Collection finished: `{collected_at}`",
        f"- Authority: `{authority}`",
        *([f"- Matching workflow run: [open run]({run_url})"] if isinstance(run_url, str) else []),
        "- Final Phase 1 GO: **MANUAL REQUIRED**",
        "",
        "| Check | Result | Evidence |",
        "| --- | --- | --- |",
    ]
    for item in _sequence(report.get("checks"), "checks"):
        check = _mapping(item, "check")
        summary = str(check.get("summary", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{check.get('code')}` | **{check.get('status')}** | {summary} |")

    lines.extend(["", "## Mandatory manual gates"])
    for gate in _sequence(report.get("manual_gates"), "manual_gates"):
        lines.append(f"- [ ] {gate}")
    lines.extend(
        [
            "",
            "> Automated PASS does not authorise real learner data or Phase 2. Complete the protected manual and physical checks and record founder/operator GO.",
            "",
        ]
    )
    return "\n".join(lines)
