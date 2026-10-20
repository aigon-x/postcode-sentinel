"""GitLab REST adapter with an explicit dry-run default.

Dry-run never opens a socket: it records the would-be HTTP call and returns it.
Live mode requires ``GITLAB_TOKEN`` and raises :class:`GitLabError` with a
readable message when it is missing.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .transport import check_url, redact


class GitLabError(RuntimeError):
    """Configuration or API error from the GitLab adapter."""


@dataclass
class Action:
    kind: str
    method: str
    url: str
    payload: dict = field(default_factory=dict)
    status: int | None = None
    response: dict | None = None
    dry_run: bool = True

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "method": self.method,
            "url": self.url,
            "payload": self.payload,
            "status": self.status,
            "response": self.response,
            "dry_run": self.dry_run,
        }


class GitLabClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        project_id: str | None = None,
        dry_run: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = check_url(
            (base_url or os.environ.get("GITLAB_URL") or "https://gitlab.com").strip().rstrip("/"),
            GitLabError,
            "GitLab base URL",
        )
        self.token = token if token is not None else os.environ.get("GITLAB_TOKEN", "")
        self.project_id = project_id or os.environ.get("GITLAB_PROJECT_ID", "")
        self.dry_run = dry_run
        self.timeout = timeout
        self.actions: list[Action] = []

    def _require_live_config(self) -> str:
        if not self.token:
            raise GitLabError(
                "GITLAB_TOKEN is not set. Live mode needs a project access token with "
                "'api' scope. Re-run with --dry-run (the default) to preview actions."
            )
        if not self.project_id:
            raise GitLabError(
                "GITLAB_PROJECT_ID is not set. Live mode needs the numeric project id or "
                "URL-encoded path. Re-run with --dry-run to preview actions."
            )
        return urllib.parse.quote(str(self.project_id), safe="")

    def comment_merge_request(self, mr_iid: int | str, body: str) -> Action:
        if not str(mr_iid).isdigit():
            raise GitLabError(f"merge request iid must be numeric, got: {mr_iid!r}")
        project = urllib.parse.quote(str(self.project_id), safe="") if self.project_id else "<project-id>"
        url = f"{self.base_url}/api/v4/projects/{project}/merge_requests/{mr_iid}/notes"
        action = Action("comment_mr", "POST", url, {"body": body})
        return self._dispatch(action)

    def create_issue(self, title: str, description: str, labels: list[str] | None = None) -> Action:
        if not title.strip():
            raise GitLabError("issue title must not be empty")
        project = urllib.parse.quote(str(self.project_id), safe="") if self.project_id else "<project-id>"
        url = f"{self.base_url}/api/v4/projects/{project}/issues"
        payload: dict = {"title": title, "description": description}
        if labels:
            payload["labels"] = ",".join(labels)
        action = Action("create_issue", "POST", url, payload)
        return self._dispatch(action)

    def _dispatch(self, action: Action) -> Action:
        if self.dry_run:
            self.actions.append(action)
            return action
        self._require_live_config()
        action.dry_run = False
        result = self._request(action.method, action.url, action.payload)
        action.status = result["status"]
        action.response = result["body"]
        self.actions.append(action)
        return action

    def _request(self, method: str, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "PRIVATE-TOKEN": self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "postcode-sentinel/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return {"status": response.status, "body": _maybe_json(raw)}
        except urllib.error.HTTPError as exc:
            raw = redact(exc.read().decode("utf-8", "replace"), self.token)
            raise GitLabError(f"GitLab API {method} {url} failed: HTTP {exc.code} {raw[:300]}") from exc
        except urllib.error.URLError as exc:
            raise GitLabError(f"GitLab API {method} {url} unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise GitLabError(f"GitLab API {method} {url} timed out after {self.timeout}s") from exc


def _maybe_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw[:500]}
    return parsed if isinstance(parsed, dict) else {"data": parsed}
