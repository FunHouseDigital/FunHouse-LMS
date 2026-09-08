"""Read-only GitHub REST collection for Phase 1 release evidence.

Only an explicit allow-list of non-sensitive fields is retained. Authentication
headers, response headers, URL credentials/query strings, and raw API payloads
are never written to the snapshot.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_URL = "https://api.github.com"
SNAPSHOT_SCHEMA_VERSION = 1
MAX_PAGES = 100
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

WORKFLOWS = {
    "ci": "ci.yml",
    "api_role": "verify-live-api-rbac.yml",
    "browser": "verify-live-pwa-browser.yml",
    "preflight": "verify-live-runtime-db.yml",
}
VERCEL_CONTEXTS = {
    "Vercel – fun-house-lms",
    "Vercel – funhouse-revenue-pwa",
}
VERCEL_ENVIRONMENTS = {
    "Production – fun-house-lms",
    "Production – funhouse-revenue-pwa",
}


class GitHubAPIError(RuntimeError):
    """The read-only GitHub evidence request or response contract failed."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so an Authorization header can never cross origins."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


class GitHubClient:
    """Minimal GET-only GitHub REST client with bounded Link pagination."""

    def __init__(
        self,
        token: str | None = None,
        *,
        api_url: str = API_URL,
        opener: Callable[[Request], Any] | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._validate_url(self.api_url)
        self._token = token
        self._opener = opener or build_opener(_NoRedirectHandler()).open

    def get_object(self, path: str) -> dict[str, Any]:
        """Fetch one JSON object."""
        data, _ = self._get(path)
        if not isinstance(data, dict):
            raise GitHubAPIError(f"GitHub API returned a non-object for {self._safe_path(path)}")
        return data

    def get_items(self, path: str, *, key: str | None = None) -> list[dict[str, Any]]:
        """Fetch every page from an array response or an object array field."""
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        seen: set[str] = set()
        page_count = 0
        while next_url:
            absolute_url = self._absolute_url(next_url)
            if absolute_url in seen:
                raise GitHubAPIError("GitHub API pagination cycle detected")
            seen.add(absolute_url)
            page_count += 1
            if page_count > MAX_PAGES:
                raise GitHubAPIError(f"GitHub API pagination exceeded {MAX_PAGES} pages")

            data, headers = self._get(absolute_url)
            page = data.get(key) if key is not None and isinstance(data, dict) else data
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise GitHubAPIError(
                    f"GitHub API returned an invalid collection for {self._safe_path(next_url)}"
                )
            items.extend(page)
            next_url = self._next_link(headers.get("Link") or headers.get("link"))
        return items

    def _absolute_url(self, path_or_url: str) -> str:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else urljoin(f"{self.api_url}/", path_or_url.lstrip("/"))
        )
        self._validate_url(url)
        return url

    def _get(self, path_or_url: str) -> tuple[Any, Mapping[str, str]]:
        url = self._absolute_url(path_or_url)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "funhouse-release-evidence/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(url, headers=headers, method="GET")
        response: Any = None
        try:
            response = self._opener(request)
            body = response.read()
            response_headers = response.headers
        except HTTPError as exc:
            raise GitHubAPIError(
                f"GitHub API returned HTTP {exc.code} for {self._safe_path(url)}"
            ) from exc
        except (URLError, OSError) as exc:
            raise GitHubAPIError(
                f"GitHub API request failed for {self._safe_path(url)}"
            ) from exc
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        try:
            return json.loads(body), response_headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAPIError(
                f"GitHub API returned invalid JSON for {self._safe_path(url)}"
            ) from exc

    @staticmethod
    def _validate_url(url: str) -> None:
        expected = urlsplit(API_URL)
        actual = urlsplit(url)
        if (
            actual.scheme != "https"
            or actual.hostname != expected.hostname
            or actual.port is not None
            or actual.username is not None
            or actual.password is not None
        ):
            raise GitHubAPIError("GitHub API URL must use https://api.github.com")

    @staticmethod
    def _next_link(value: str | None) -> str | None:
        if not value:
            return None
        for part in value.split(","):
            match = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"\s*$', part)
            if match and match.group(2) == "next":
                return match.group(1)
        return None

    @staticmethod
    def _safe_path(path_or_url: str) -> str:
        parsed = urlsplit(path_or_url)
        return parsed.path if parsed.scheme else path_or_url.split("?", 1)[0]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_public_url(value: Any, host: str) -> str | None:
    """Return only an HTTPS host/path, dropping query and fragment data."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return f"https://{host}{parsed.path}"


def _actor_login(value: Any) -> str | None:
    return value.get("login") if isinstance(value, Mapping) else None


def _run(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "workflow_id": raw.get("workflow_id"),
        "event": raw.get("event"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "head_sha": raw.get("head_sha"),
        "head_branch": raw.get("head_branch"),
        "display_title": raw.get("display_title"),
        "created_at": raw.get("created_at"),
        "run_started_at": raw.get("run_started_at"),
        "updated_at": raw.get("updated_at"),
        "html_url": _safe_public_url(raw.get("html_url"), "github.com"),
    }


def _job(raw: Mapping[str, Any]) -> dict[str, Any]:
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "started_at": raw.get("started_at"),
        "completed_at": raw.get("completed_at"),
        "html_url": _safe_public_url(raw.get("html_url"), "github.com"),
        "steps": [
            {
                "name": step.get("name"),
                "status": step.get("status"),
                "conclusion": step.get("conclusion"),
                "number": step.get("number"),
            }
            for step in steps
            if isinstance(step, dict)
        ],
    }


def _commit_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "state": raw.get("state"),
        "context": raw.get("context"),
        "creator": _actor_login(raw.get("creator")),
        "target_url": _safe_public_url(raw.get("target_url"), "vercel.com"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


def _deployment_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "state": raw.get("state"),
        "creator": _actor_login(raw.get("creator")),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


def _workflow(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "path": raw.get("path"),
        "state": raw.get("state"),
        "html_url": _safe_public_url(raw.get("html_url"), "github.com"),
    }


def _ref_sha(ref: Mapping[str, Any]) -> str | None:
    ref_object = ref.get("object")
    return ref_object.get("sha") if isinstance(ref_object, Mapping) else None


def _raw_item_key(item: Mapping[str, Any]) -> tuple[str, int]:
    created_at = item.get("created_at")
    item_id = item.get("id")
    return (
        created_at if isinstance(created_at, str) else "",
        item_id if isinstance(item_id, int) else -1,
    )


def _collect_production_deployments(
    github: GitHubClient, root: str
) -> list[dict[str, Any]]:
    """Collect only the newest deployment and status for each Production environment."""
    deployments: list[dict[str, Any]] = []
    for environment in sorted(VERCEL_ENVIRONMENTS):
        raw_deployments = github.get_items(
            f"{root}/deployments?"
            f"{urlencode({'environment': environment, 'per_page': 100})}"
        )
        matching = [
            item for item in raw_deployments if item.get("environment") == environment
        ]
        if not matching:
            continue
        raw = max(matching, key=_raw_item_key)
        deployment_id = raw.get("id")
        statuses: list[dict[str, Any]] = []
        if isinstance(deployment_id, int):
            raw_statuses = github.get_items(
                f"{root}/deployments/{deployment_id}/statuses?"
                f"{urlencode({'per_page': 100})}"
            )
            if raw_statuses:
                statuses = [_deployment_status(max(raw_statuses, key=_raw_item_key))]
        deployments.append(
            {
                "id": deployment_id,
                "sha": raw.get("sha"),
                "environment": raw.get("environment"),
                "creator": _actor_login(raw.get("creator")),
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at"),
                "statuses": statuses,
            }
        )
    return deployments


def collect_snapshot(
    repository: str,
    *,
    candidate_sha: str | None = None,
    token: str | None = None,
    as_of: str | None = None,
    collected_at: str | None = None,
    client: GitHubClient | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect a schema-v1, secret-free snapshot using GitHub REST GET requests only."""
    if not REPOSITORY_RE.fullmatch(repository):
        raise GitHubAPIError("repository must use the owner/name form")

    github = client or GitHubClient(token)
    observation_as_of = as_of or _iso_now()
    encoded_repo = "/".join(quote(part, safe="") for part in repository.split("/"))
    root = f"/repos/{encoded_repo}"

    repository_raw = github.get_object(root)
    initial_main_sha = _ref_sha(github.get_object(f"{root}/git/ref/heads/main"))
    selected_sha = candidate_sha or initial_main_sha
    if not isinstance(selected_sha, str) or not SHA_RE.fullmatch(selected_sha):
        raise GitHubAPIError("candidate SHA must be a lowercase full 40-character hexadecimal SHA")

    workflow_definitions: dict[str, dict[str, Any]] = {}
    workflow_runs: dict[str, list[dict[str, Any]]] = {}
    raw_runs: dict[str, list[dict[str, Any]]] = {}

    for logical_name, filename in WORKFLOWS.items():
        workflow_path = f"{root}/actions/workflows/{quote(filename, safe='')}"
        workflow_definitions[logical_name] = _workflow(github.get_object(workflow_path))
        runs = github.get_items(
            f"{workflow_path}/runs?{urlencode({'branch': 'main', 'per_page': 100})}",
            key="workflow_runs",
        )
        raw_runs[logical_name] = runs
        workflow_runs[logical_name] = [_run(run) for run in runs]

    ci_jobs: dict[str, list[dict[str, Any]]] = {}
    for run in raw_runs["ci"]:
        if (
            run.get("head_sha") == selected_sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and isinstance(run.get("id"), int)
        ):
            run_id = run["id"]
            jobs = github.get_items(
                f"{root}/actions/runs/{run_id}/jobs?{urlencode({'per_page': 100})}",
                key="jobs",
            )
            ci_jobs[str(run_id)] = [_job(job) for job in jobs]

    commit_statuses = [
        _commit_status(status)
        for status in github.get_items(
            f"{root}/commits/{selected_sha}/statuses?{urlencode({'per_page': 100})}"
        )
        if status.get("context") in VERCEL_CONTEXTS
    ]
    deployments = _collect_production_deployments(github, root)
    confirmed_deployments = _collect_production_deployments(github, root)
    if confirmed_deployments != deployments:
        raise GitHubAPIError(
            "Production deployment state changed during evidence collection; retry"
        )

    final_main_sha = _ref_sha(github.get_object(f"{root}/git/ref/heads/main"))
    collection_finished_at = collected_at or (
        as_of if as_of is not None else _iso_now()
    )
    safe_provenance = {
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
        if provenance and isinstance(provenance.get(key), (str, int))
    }
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "repository": repository,
        "candidate_sha": selected_sha,
        "collected_at": collection_finished_at,
        "as_of": observation_as_of,
        "collection_provenance": safe_provenance,
        "repository_state": {
            "default_branch": repository_raw.get("default_branch"),
            "initial_main_sha": initial_main_sha,
            "current_main_sha": final_main_sha,
        },
        "workflow_definitions": workflow_definitions,
        "workflow_runs": workflow_runs,
        "ci_jobs": ci_jobs,
        "commit_statuses": commit_statuses,
        "deployments": deployments,
    }
