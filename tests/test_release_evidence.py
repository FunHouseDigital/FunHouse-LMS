"""Tests for secret-free exact-SHA Phase 1 release evidence."""

from __future__ import annotations

import copy
import json
from email.message import Message
from pathlib import Path
from urllib.request import Request

import pytest

from funhouse_pipeline.release_evidence import (
    EvidenceFormatError,
    render_markdown,
    validate_snapshot,
)
from funhouse_pipeline.release_evidence.cli import main
from funhouse_pipeline.release_evidence.github import (
    GitHubAPIError,
    GitHubClient,
    collect_snapshot,
)

SHA = "a" * 40
OTHER_SHA = "b" * 40
AS_OF = "2026-08-14T12:00:00Z"


def passing_snapshot() -> dict[str, object]:
    """Return a complete deterministic snapshot for one successful release chain."""
    return {
        "schema_version": 1,
        "repository": "FunHouseDigital/FunHouse-LMS",
        "candidate_sha": SHA,
        "collected_at": AS_OF,
        "as_of": AS_OF,
        "collection_provenance": {
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "run_id": "500",
            "run_attempt": "1",
            "validator_sha": SHA,
            "workflow": "Validate Phase 1 Release Evidence",
            "workflow_ref": (
                "FunHouseDigital/FunHouse-LMS/"
                ".github/workflows/validate-phase1-release-evidence.yml@refs/heads/main"
            ),
        },
        "repository_state": {
            "default_branch": "main",
            "initial_main_sha": SHA,
            "current_main_sha": SHA,
        },
        "workflow_definitions": {
            "ci": {
                "id": 1,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "state": "active",
                "html_url": "https://github.com/example/actions/workflows/ci.yml",
            },
            "api_role": {
                "id": 2,
                "name": "Verify Live API Role Access",
                "path": ".github/workflows/verify-live-api-rbac.yml",
                "state": "active",
            },
            "browser": {
                "id": 3,
                "name": "Verify Live PWA Browser",
                "path": ".github/workflows/verify-live-pwa-browser.yml",
                "state": "active",
            },
            "preflight": {
                "id": 4,
                "name": "Prepare Phase 1 Field Acceptance",
                "path": ".github/workflows/verify-live-runtime-db.yml",
                "state": "active",
            },
        },
        "workflow_runs": {
            "ci": [
                {
                    "id": 100,
                    "workflow_id": 1,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": SHA,
                    "head_branch": "main",
                    "created_at": "2026-08-14T10:00:00Z",
                    "run_started_at": "2026-08-14T10:01:00Z",
                    "updated_at": "2026-08-14T10:30:00Z",
                    "html_url": "https://github.com/example/actions/runs/100",
                }
            ],
            "api_role": [
                {
                    "id": 200,
                    "workflow_id": 2,
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": SHA,
                    "head_branch": "main",
                    "created_at": "2026-08-14T11:00:00Z",
                    "run_started_at": "2026-08-14T11:01:00Z",
                    "updated_at": "2026-08-14T11:15:00Z",
                    "html_url": "https://github.com/example/actions/runs/200",
                }
            ],
            "browser": [
                {
                    "id": 301,
                    "workflow_id": 3,
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": SHA,
                    "head_branch": "main",
                    "display_title": f"Verify Live PWA Browser · applied-or-skipped · {SHA}",
                    "created_at": "2026-08-14T11:30:00Z",
                    "run_started_at": "2026-08-14T11:31:00Z",
                    "updated_at": "2026-08-14T11:50:00Z",
                    "html_url": "https://github.com/example/actions/runs/301",
                },
                {
                    "id": 302,
                    "workflow_id": 3,
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": SHA,
                    "head_branch": "main",
                    "display_title": f"Verify Live PWA Browser · skipped · {SHA}",
                    "created_at": "2026-08-14T11:51:00Z",
                    "run_started_at": "2026-08-14T11:52:00Z",
                    "updated_at": "2026-08-14T11:59:00Z",
                    "html_url": "https://github.com/example/actions/runs/302",
                },
            ],
            "preflight": [
                {
                    "id": 400,
                    "workflow_id": 4,
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": SHA,
                    "head_branch": "main",
                    "created_at": "2026-08-14T09:00:00Z",
                    "run_started_at": "2026-08-14T09:01:00Z",
                    "updated_at": "2026-08-14T09:20:00Z",
                    "html_url": "https://github.com/example/actions/runs/400",
                }
            ],
        },
        "ci_jobs": {
            "100": [
                {
                    "id": 101,
                    "name": "web-tests (npm test + build)",
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [
                        {
                            "name": "Production build + hermetic five-session offline gate",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ],
                }
            ]
        },
        "commit_statuses": [
            {
                "id": 10,
                "context": "Vercel – fun-house-lms",
                "state": "success",
                "creator": "vercel[bot]",
                "target_url": "https://vercel.com/fun-house-digital/fun-house-lms/build-a",
                "created_at": "2026-08-14T10:40:00Z",
            },
            {
                "id": 11,
                "context": "Vercel – funhouse-revenue-pwa",
                "state": "success",
                "creator": "vercel[bot]",
                "target_url": (
                    "https://vercel.com/fun-house-digital/funhouse-revenue-pwa/build-b"
                ),
                "created_at": "2026-08-14T10:41:00Z",
            },
        ],
        "deployments": [
            {
                "id": 20,
                "sha": SHA,
                "environment": "Production – fun-house-lms",
                "creator": "vercel[bot]",
                "created_at": "2026-08-14T10:35:00Z",
                "statuses": [
                    {
                        "id": 21,
                        "state": "success",
                        "creator": "vercel[bot]",
                        "created_at": "2026-08-14T10:45:00Z",
                        "environment_url": "https://api.example.invalid",
                    }
                ],
            },
            {
                "id": 30,
                "sha": SHA,
                "environment": "Production – funhouse-revenue-pwa",
                "creator": "vercel[bot]",
                "created_at": "2026-08-14T10:36:00Z",
                "statuses": [
                    {
                        "id": 31,
                        "state": "success",
                        "creator": "vercel[bot]",
                        "created_at": "2026-08-14T10:46:00Z",
                        "environment_url": "https://pwa.example.invalid",
                    }
                ],
            },
        ],
    }


def check_status(report: dict[str, object], code: str) -> str:
    checks = report["checks"]
    assert isinstance(checks, list)
    return next(check["status"] for check in checks if check["code"] == code)


def test_complete_chain_passes_but_final_go_remains_manual() -> None:
    report = validate_snapshot(passing_snapshot())

    assert report["automated_verdict"] == "PASS"
    assert report["final_phase1_go"] == "MANUAL_REQUIRED"
    assert len(report["manual_gates"]) == 5
    assert all(check["status"] == "PASS" for check in report["checks"])


@pytest.mark.parametrize("candidate_sha", ["A" * 40, "a" * 39])
def test_noncanonical_candidate_sha_is_a_format_error(candidate_sha: str) -> None:
    snapshot = passing_snapshot()
    snapshot["candidate_sha"] = candidate_sha

    with pytest.raises(EvidenceFormatError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_branch", "trunk"),
        ("initial_main_sha", OTHER_SHA),
        ("current_main_sha", OTHER_SHA),
    ],
)
def test_candidate_must_still_be_exact_current_main(field: str, value: str) -> None:
    snapshot = passing_snapshot()
    snapshot["repository_state"][field] = value

    report = validate_snapshot(snapshot)
    assert report["automated_verdict"] == "BLOCKED"
    assert check_status(report, "current_main") == "FAIL"


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
def test_pr_or_guard_only_ci_does_not_count(event: str) -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["ci"][0]["event"] = event

    report = validate_snapshot(snapshot)
    assert check_status(report, "main_ci") == "FAIL"


def test_exact_hermetic_job_and_step_are_required() -> None:
    snapshot = passing_snapshot()
    snapshot["ci_jobs"]["100"][0]["steps"] = []

    report = validate_snapshot(snapshot)
    assert check_status(report, "main_ci") == "FAIL"


def test_newer_failed_duplicate_commit_status_shadows_old_success() -> None:
    snapshot = passing_snapshot()
    snapshot["commit_statuses"].append(
        {
            "id": 99,
            "context": "Vercel – fun-house-lms",
            "state": "failure",
            "target_url": "https://vercel.com/fun-house-digital/fun-house-lms/build-new",
            "created_at": "2026-08-14T10:50:00Z",
        }
    )

    report = validate_snapshot(snapshot)
    assert check_status(report, "vercel_api_commit_status") == "FAIL"


def test_newer_failed_deployment_shadows_old_success() -> None:
    snapshot = passing_snapshot()
    snapshot["deployments"].append(
        {
            "id": 40,
            "sha": SHA,
            "environment": "Production – fun-house-lms",
            "creator": "vercel[bot]",
            "created_at": "2026-08-14T10:55:00Z",
            "statuses": [
                {"id": 41, "state": "failure", "created_at": "2026-08-14T10:56:00Z"}
            ],
        }
    )

    report = validate_snapshot(snapshot)
    assert check_status(report, "vercel_api_production_deployment") == "FAIL"


def test_newer_different_sha_production_deployment_blocks_old_candidate() -> None:
    snapshot = passing_snapshot()
    snapshot["deployments"].append(
        {
            "id": 50,
            "sha": OTHER_SHA,
            "environment": "Production – fun-house-lms",
            "creator": "vercel[bot]",
            "created_at": "2026-08-14T10:55:00Z",
            "statuses": [
                {
                    "id": 51,
                    "state": "success",
                    "creator": "vercel[bot]",
                    "created_at": "2026-08-14T10:56:00Z",
                }
            ],
        }
    )

    report = validate_snapshot(snapshot)
    assert check_status(report, "vercel_api_production_deployment") == "FAIL"


@pytest.mark.parametrize(
    ("location", "field", "value", "code"),
    [
        ("status", "target_url", "https://example.invalid/build", "vercel_api_commit_status"),
        ("status", "creator", "someone", "vercel_api_commit_status"),
        (
            "deployment",
            "creator",
            "someone",
            "vercel_api_production_deployment",
        ),
        (
            "deployment",
            "environment",
            "Preview – fun-house-lms",
            "vercel_api_production_deployment",
        ),
    ],
)
def test_wrong_vercel_contract_is_blocked(
    location: str, field: str, value: str, code: str
) -> None:
    snapshot = passing_snapshot()
    target = snapshot["commit_statuses"][0] if location == "status" else snapshot["deployments"][0]
    target[field] = value

    report = validate_snapshot(snapshot)
    assert check_status(report, code) == "FAIL"


def test_api_role_must_start_after_api_deployment_succeeds() -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["api_role"][0]["run_started_at"] = "2026-08-14T10:44:59Z"

    report = validate_snapshot(snapshot)
    assert check_status(report, "api_role_verification") == "FAIL"


def test_browser_run_without_mode_title_is_not_evidence() -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["browser"][0]["display_title"] = "Verify Live PWA Browser"

    report = validate_snapshot(snapshot)
    assert check_status(report, "protected_browser_pair") == "FAIL"


def test_replay_must_start_after_first_browser_run_completes() -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["browser"][1]["run_started_at"] = "2026-08-14T11:49:59Z"

    report = validate_snapshot(snapshot)
    assert check_status(report, "protected_browser_pair") == "FAIL"


@pytest.mark.parametrize(
    ("updated_at", "expected"),
    [
        ("2026-08-07T12:00:00Z", "PASS"),
        ("2026-08-07T11:59:59Z", "FAIL"),
    ],
)
def test_preflight_seven_day_freshness_boundary(updated_at: str, expected: str) -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["preflight"][0]["updated_at"] = updated_at

    report = validate_snapshot(snapshot)
    assert check_status(report, "database_preflight") == expected


def test_preflight_freshness_uses_collection_finish_not_earlier_cutoff() -> None:
    snapshot = passing_snapshot()
    snapshot["as_of"] = "2026-08-14T12:00:00Z"
    snapshot["collected_at"] = "2026-08-14T12:00:01Z"
    snapshot["workflow_runs"]["preflight"][0]["updated_at"] = (
        "2026-08-07T12:00:00Z"
    )

    report = validate_snapshot(snapshot)
    assert check_status(report, "database_preflight") == "FAIL"


def test_preflight_after_observation_cutoff_is_blocked_even_when_fresh() -> None:
    snapshot = passing_snapshot()
    snapshot["as_of"] = "2026-08-14T12:00:00Z"
    snapshot["collected_at"] = "2026-08-14T12:00:10Z"
    snapshot["workflow_runs"]["preflight"][0]["updated_at"] = (
        "2026-08-14T12:00:05Z"
    )

    report = validate_snapshot(snapshot)
    assert check_status(report, "evidence_as_of") == "FAIL"
    assert check_status(report, "database_preflight") == "PASS"
    assert report["automated_verdict"] == "BLOCKED"


def test_input_order_does_not_change_selection_or_rendering() -> None:
    snapshot = passing_snapshot()
    report = validate_snapshot(snapshot)
    reordered = copy.deepcopy(snapshot)
    reordered["workflow_runs"]["browser"].reverse()
    reordered["commit_statuses"].reverse()
    reordered["deployments"].reverse()

    assert validate_snapshot(reordered) == report
    assert render_markdown(report) == render_markdown(validate_snapshot(reordered))


def test_deployment_record_after_observation_cutoff_is_blocked() -> None:
    snapshot = passing_snapshot()
    snapshot["deployments"][0]["created_at"] = "2026-08-14T12:00:01Z"

    report = validate_snapshot(snapshot)
    assert check_status(report, "evidence_as_of") == "FAIL"
    assert report["automated_verdict"] == "BLOCKED"


def test_future_selected_evidence_is_blocked() -> None:
    snapshot = passing_snapshot()
    snapshot["workflow_runs"]["browser"][1]["updated_at"] = "2026-08-14T12:00:01Z"

    report = validate_snapshot(snapshot)
    assert check_status(report, "evidence_as_of") == "FAIL"
    assert report["automated_verdict"] == "BLOCKED"


def test_unbound_collector_provenance_is_diagnostic_only() -> None:
    snapshot = passing_snapshot()
    snapshot["collection_provenance"]["ref"] = "refs/heads/feature/bypass"

    report = validate_snapshot(snapshot)
    assert check_status(report, "collector_provenance") == "FAIL"
    assert report["authority"] == "LOCAL_DIAGNOSTIC_ONLY"
    assert report["automated_verdict"] == "BLOCKED"


def test_deployment_status_must_be_created_by_vercel_bot() -> None:
    snapshot = passing_snapshot()
    snapshot["deployments"][0]["statuses"][0]["creator"] = "someone"

    report = validate_snapshot(snapshot)
    assert check_status(report, "vercel_api_production_deployment") == "FAIL"


def test_offline_validate_cli_writes_canonical_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    snapshot_path.write_text(json.dumps(passing_snapshot()), encoding="utf-8")

    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline validation attempted network access")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    result = main(
        [
            "validate",
            "--snapshot",
            str(snapshot_path),
            "--report-json",
            str(report_path),
            "--report-markdown",
            str(markdown_path),
        ]
    )

    assert result == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["automated_verdict"] == "PASS"
    assert report_path.read_text(encoding="utf-8").endswith("\n")
    assert "founder/operator GO" in markdown_path.read_text(encoding="utf-8")


def test_validate_cli_returns_two_for_malformed_json(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text("not-json", encoding="utf-8")

    result = main(
        [
            "validate",
            "--snapshot",
            str(snapshot_path),
            "--report-json",
            str(tmp_path / "report.json"),
            "--report-markdown",
            str(tmp_path / "report.md"),
        ]
    )
    assert result == 2


def test_check_cli_forwards_actions_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actions_environment = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "500",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": SHA,
        "GITHUB_WORKFLOW": "Validate Phase 1 Release Evidence",
        "GITHUB_WORKFLOW_REF": (
            "FunHouseDigital/FunHouse-LMS/"
            ".github/workflows/validate-phase1-release-evidence.yml@refs/heads/main"
        ),
    }
    for key, value in actions_environment.items():
        monkeypatch.setenv(key, value)

    def fake_collect(repository: str, **kwargs: object) -> dict[str, object]:
        assert repository == "FunHouseDigital/FunHouse-LMS"
        snapshot = passing_snapshot()
        snapshot["collection_provenance"] = dict(kwargs["provenance"])
        return snapshot

    monkeypatch.setattr(
        "funhouse_pipeline.release_evidence.cli.collect_snapshot", fake_collect
    )
    result = main(
        [
            "check",
            "--repository",
            "FunHouseDigital/FunHouse-LMS",
            "--candidate-sha",
            SHA,
            "--snapshot",
            str(tmp_path / "snapshot.json"),
            "--report-json",
            str(tmp_path / "report.json"),
            "--report-markdown",
            str(tmp_path / "report.md"),
        ]
    )

    assert result == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["authority"] == "EXTERNAL_WORKFLOW_RUN_REQUIRED"


class FakeResponse:
    def __init__(self, value: object, link: str | None = None) -> None:
        self._body = json.dumps(value).encode()
        self.headers = Message()
        if link:
            self.headers["Link"] = link

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def test_github_client_follows_every_safe_link_page() -> None:
    requests: list[Request] = []
    responses = [
        FakeResponse(
            [{"id": 2}],
            '<https://api.github.com/example?page=2>; rel="next", '
            '<https://api.github.com/example?page=2>; rel="last"',
        ),
        FakeResponse([{"id": 1}]),
    ]

    def opener(request: Request) -> FakeResponse:
        requests.append(request)
        return responses.pop(0)

    items = GitHubClient(opener=opener).get_items("/example?page=1")

    assert items == [{"id": 2}, {"id": 1}]
    assert [request.full_url for request in requests] == [
        "https://api.github.com/example?page=1",
        "https://api.github.com/example?page=2",
    ]


def test_github_client_rejects_external_pagination_host() -> None:
    response = FakeResponse(
        [{"id": 1}], '<https://attacker.invalid/example?page=2>; rel="next"'
    )
    client = GitHubClient(opener=lambda request: response)

    with pytest.raises(GitHubAPIError, match="api.github.com"):
        client.get_items("/example")



class FakeSnapshotClient:
    """Return raw GitHub-shaped data for collector integration tests."""

    def __init__(
        self, final_main_sha: str = SHA, *, production_changes: bool = False
    ) -> None:
        self.final_main_sha = final_main_sha
        self.production_changes = production_changes
        self.ref_reads = 0
        self.deployment_head_reads = 0
        self.item_paths: list[str] = []
        self.fixture = passing_snapshot()

    def get_object(self, path: str) -> dict[str, object]:
        root = "/repos/FunHouseDigital/FunHouse-LMS"
        if path == root:
            return {"default_branch": "main"}
        if path == f"{root}/git/ref/heads/main":
            self.ref_reads += 1
            sha = SHA if self.ref_reads == 1 else self.final_main_sha
            return {"object": {"sha": sha}}
        workflow_contracts = {
            "ci.yml": (1, "CI"),
            "verify-live-api-rbac.yml": (2, "Verify Live API Role Access"),
            "verify-live-pwa-browser.yml": (3, "Verify Live PWA Browser"),
            "verify-live-runtime-db.yml": (4, "Prepare Phase 1 Field Acceptance"),
        }
        for filename, (workflow_id, name) in workflow_contracts.items():
            if path.endswith(f"/actions/workflows/{filename}"):
                return {
                    "id": workflow_id,
                    "name": name,
                    "path": f".github/workflows/{filename}",
                    "state": "active",
                    "html_url": (
                        f"https://github.com/FunHouseDigital/FunHouse-LMS/"
                        f"actions/workflows/{filename}?discard=secret"
                    ),
                }
        raise AssertionError(f"unexpected object path: {path}")

    def get_items(
        self, path: str, *, key: str | None = None
    ) -> list[dict[str, object]]:
        self.item_paths.append(path)
        if "/actions/workflows/" in path and "/runs?" in path:
            logical = (
                "api_role"
                if "verify-live-api-rbac" in path
                else "browser"
                if "verify-live-pwa-browser" in path
                else "preflight"
                if "verify-live-runtime-db" in path
                else "ci"
            )
            return copy.deepcopy(self.fixture["workflow_runs"][logical])
        if "/actions/runs/100/jobs?" in path:
            return copy.deepcopy(self.fixture["ci_jobs"]["100"])
        if f"/commits/{SHA}/statuses?" in path:
            statuses = copy.deepcopy(self.fixture["commit_statuses"])
            for status in statuses:
                status["creator"] = {"login": "vercel[bot]"}
                status["target_url"] += "?signature=secret#fragment"
            statuses.append(
                {
                    "id": 999,
                    "context": "Unrelated integration",
                    "state": "success",
                    "creator": {"login": "other[bot]"},
                    "target_url": "https://example.invalid/?token=secret",
                    "created_at": "2026-08-14T10:50:00Z",
                }
            )
            return statuses
        if "/deployments?" in path:
            self.deployment_head_reads += 1
            deployments = copy.deepcopy(self.fixture["deployments"])
            if self.production_changes and self.deployment_head_reads > 2:
                deployments.append(
                    {
                        "id": 60,
                        "sha": OTHER_SHA,
                        "environment": "Production – fun-house-lms",
                        "creator": {"login": "vercel[bot]"},
                        "created_at": "2026-08-14T10:58:00Z",
                    }
                )
            for deployment in deployments:
                deployment.pop("statuses", None)
                deployment["creator"] = {"login": "vercel[bot]"}
            return deployments
        if "/deployments/60/statuses?" in path:
            return [
                {
                    "id": 61,
                    "state": "success",
                    "creator": {"login": "vercel[bot]"},
                    "created_at": "2026-08-14T10:59:00Z",
                }
            ]
        for deployment in self.fixture["deployments"]:
            if f"/deployments/{deployment['id']}/statuses?" in path:
                statuses = copy.deepcopy(deployment["statuses"])
                for status in statuses:
                    status["creator"] = {"login": "vercel[bot]"}
                    status["environment_url"] = "https://example.invalid/?token=secret"
                    status["log_url"] = "https://example.invalid/?token=secret"
                return statuses
        raise AssertionError(f"unexpected collection path: {path} ({key})")


def test_collector_normalises_only_safe_required_evidence() -> None:
    provenance = passing_snapshot()["collection_provenance"]
    client = FakeSnapshotClient()

    snapshot = collect_snapshot(
        "FunHouseDigital/FunHouse-LMS",
        candidate_sha=SHA,
        as_of=AS_OF,
        client=client,  # type: ignore[arg-type]
        provenance=provenance,
    )

    assert client.ref_reads == 2
    deployment_queries = [path for path in client.item_paths if "/deployments?" in path]
    assert len(deployment_queries) == 4
    assert all("environment=" in path and "sha=" not in path for path in deployment_queries)
    assert validate_snapshot(snapshot)["automated_verdict"] == "PASS"
    serialised = json.dumps(snapshot, sort_keys=True)
    assert "secret" not in serialised
    assert "Unrelated integration" not in serialised
    assert all(status["creator"] == "vercel[bot]" for status in snapshot["commit_statuses"])
    assert all(
        status["creator"] == "vercel[bot]"
        for deployment in snapshot["deployments"]
        for status in deployment["statuses"]
    )


def test_collector_final_main_recheck_blocks_a_race() -> None:
    client = FakeSnapshotClient(final_main_sha=OTHER_SHA)
    snapshot = collect_snapshot(
        "FunHouseDigital/FunHouse-LMS",
        candidate_sha=SHA,
        as_of=AS_OF,
        client=client,  # type: ignore[arg-type]
        provenance=passing_snapshot()["collection_provenance"],
    )

    report = validate_snapshot(snapshot)
    assert report["automated_verdict"] == "BLOCKED"
    assert check_status(report, "current_main") == "FAIL"


def test_collector_blocks_production_drift_between_stability_reads() -> None:
    client = FakeSnapshotClient(production_changes=True)

    with pytest.raises(GitHubAPIError, match="Production deployment state changed"):
        collect_snapshot(
            "FunHouseDigital/FunHouse-LMS",
            candidate_sha=SHA,
            as_of=AS_OF,
            client=client,  # type: ignore[arg-type]
            provenance=passing_snapshot()["collection_provenance"],
        )


def test_actions_workflow_binds_main_ref_checkout_and_candidate() -> None:
    workflow = (
        Path(__file__).parents[1]
        / ".github/workflows/validate-phase1-release-evidence.yml"
    ).read_text(encoding="utf-8")

    assert '"${DISPATCH_REF}" != "refs/heads/main"' in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert '--candidate-sha "${CANDIDATE_SHA}"' in workflow
    assert "CANDIDATE_SHA: ${{ github.sha }}" in workflow
    assert "if-no-files-found: error" in workflow
