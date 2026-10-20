"""GitLab Duo Agent Platform adapter.

Live mode calls the Duo Agent Platform workflow API on the GitLab instance
(``/api/v4/ai/duo_workflows/workflows`` by default, override with
``GITLAB_DUO_ENDPOINT``) using a token from ``GITLAB_DUO_TOKEN``. The flow
configuration that the request targets lives in
``.gitlab/duo/flows/postcode-sentinel.yml`` (Flow Registry v1).

There is no silent fallback: live mode without a token raises ``DuoError``.
The deterministic local reasoner is used only when explicitly requested
(``--offline``), and its output is labelled ``offline``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .models import ReviewResult


class DuoError(RuntimeError):
    """Raised when a live Duo call cannot be completed."""


OFFLINE_MODE = "offline"
LIVE_MODE = "live-duo-workflow"

DEFAULT_DUO_PATH = "/api/v4/ai/duo_workflows/workflows"
FLOW_CONFIG_ID = "postcode-sentinel"
FLOW_SCHEMA_VERSION = "v1"
FLOW_VERSION = "1.0.0"

RECOMMENDATIONS = ("approve", "comment_and_review", "block_and_followup", "revert")


def _default_endpoint() -> str:
    base = (os.environ.get("GITLAB_URL") or "https://gitlab.com").rstrip("/")
    return f"{base}{DEFAULT_DUO_PATH}"


class DuoAgentClient:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        offline: bool = False,
        project_id: str | None = None,
        timeout: float = 60.0,
        transport=None,
    ) -> None:
        self.offline = offline
        self.endpoint = (
            endpoint
            if endpoint is not None
            else os.environ.get("GITLAB_DUO_ENDPOINT") or _default_endpoint()
        ).strip()
        self.token = token if token is not None else os.environ.get("GITLAB_DUO_TOKEN", "")
        self.project_id = (
            project_id
            or os.environ.get("GITLAB_PROJECT_ID")
            or os.environ.get("CI_PROJECT_ID")
            or ""
        )
        self.timeout = timeout
        self._transport = transport or urllib.request.urlopen

    @property
    def mode(self) -> str:
        return OFFLINE_MODE if self.offline else LIVE_MODE

    def decide(self, result: ReviewResult) -> dict:
        if self.offline:
            return offline_decision(result)
        return self._live_decision(result)

    def _require_config(self) -> None:
        if not self.token:
            raise DuoError(
                "GITLAB_DUO_TOKEN is not set. A live Duo Agent Platform call needs a "
                "GitLab token with access to the project's Duo workflow API. Set "
                "GITLAB_DUO_TOKEN, or run with --offline to use the local deterministic reasoner."
            )
        if not self.project_id:
            raise DuoError(
                "GITLAB_PROJECT_ID is not set. The Duo workflow API is project-scoped; "
                "set GITLAB_PROJECT_ID (GitLab CI provides CI_PROJECT_ID automatically), "
                "or run with --offline."
            )

    def _live_decision(self, result: ReviewResult) -> dict:
        self._require_config()
        payload = {
            "project_id": int(self.project_id) if str(self.project_id).isdigit() else self.project_id,
            "flow_config_id": FLOW_CONFIG_ID,
            "flow_config_schema_version": FLOW_SCHEMA_VERSION,
            "flow_version": FLOW_VERSION,
            "environment": "ambient",
            "goal": _goal_text(result),
            "context": _review_context(result),
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "postcode-sentinel/0.2",
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with self._transport(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                status = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            hint = ""
            if exc.code in (401, 403):
                hint = " Check GITLAB_DUO_TOKEN and that Duo Agent Platform is enabled for the project."
            raise DuoError(f"Duo endpoint returned HTTP {exc.code}: {body[:300]}{hint}") from exc
        except urllib.error.URLError as exc:
            raise DuoError(f"Duo endpoint unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise DuoError(f"Duo endpoint timed out after {self.timeout}s") from exc

        parsed = _parse_json(raw)
        decision = _extract_decision(parsed)
        return {
            "mode": LIVE_MODE,
            "endpoint": self.endpoint,
            "workflow_id": parsed.get("id"),
            "recommendation": decision["recommendation"],
            "rationale": decision["rationale"],
            "status": status,
            "raw": parsed,
        }


def _goal_text(result: ReviewResult) -> str:
    return (
        "Review this post-merge risk report and return the decision as JSON with "
        'the keys "recommendation" (one of: ' + ", ".join(RECOMMENDATIONS) + ') and '
        '"rationale" (one short paragraph).\n\n'
        + json.dumps(_review_context(result), indent=2, ensure_ascii=False)
    )


def _review_context(result: ReviewResult) -> dict:
    return {
        "task": "post_merge_risk_review",
        "score": result.score,
        "level": result.level,
        "findings": [finding.to_dict() for finding in result.findings],
        "files": result.changeset.paths(),
        "lines_added": result.changeset.total_added,
        "lines_removed": result.changeset.total_removed,
    }


def _parse_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DuoError(f"Duo endpoint returned non-JSON body: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise DuoError("Duo endpoint returned JSON that is not an object")
    return parsed


def _extract_decision(parsed: dict) -> dict:
    for key in ("result", "output", "final_answer"):
        value = parsed.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict) and "recommendation" in value:
            parsed = value
            break
    recommendation = parsed.get("recommendation")
    if not isinstance(recommendation, str) or recommendation not in RECOMMENDATIONS:
        workflow_id = parsed.get("id")
        suffix = f" (workflow id: {workflow_id})" if workflow_id else ""
        raise DuoError(
            "Duo response did not contain a valid 'recommendation'"
            f"{suffix}; got keys: {sorted(parsed)}. The flow in "
            ".gitlab/duo/flows/postcode-sentinel.yml must return the decision JSON."
        )
    rationale = parsed.get("rationale", "")
    return {"recommendation": recommendation, "rationale": str(rationale)}


def recommendation_for(score: int) -> str:
    if score >= 75:
        return "revert"
    if score >= 55:
        return "block_and_followup"
    if score >= 30:
        return "comment_and_review"
    return "approve"


def offline_decision(result: ReviewResult) -> dict:
    fired = sorted({finding.rule_id for finding in result.findings})
    if fired:
        rationale = (
            f"Score {result.score}/100 ({result.level}). Rules fired: {', '.join(fired)}. "
            f"Top reason: {result.findings[0].reason}"
        )
    else:
        rationale = f"Score {result.score}/100 ({result.level}); no risk rules fired."
    return {
        "mode": OFFLINE_MODE,
        "reasoner": "local-deterministic",
        "recommendation": recommendation_for(result.score),
        "rationale": rationale,
        "rules_fired": fired,
        "note": "Offline mode was requested explicitly; no Duo API call was made.",
    }
