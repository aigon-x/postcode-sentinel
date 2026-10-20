"""GitLab Duo agent adapter.

Live mode posts the review context to a configured endpoint. Without an
endpoint/token the client runs a deterministic local reasoner and labels the
result ``offline-local-reasoner`` so the caller can never mistake it for a
real Duo response.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .models import ReviewResult


class DuoError(RuntimeError):
    """Raised when a live Duo call cannot be completed."""


OFFLINE_MODE = "offline-local-reasoner"
LIVE_MODE = "live-duo-endpoint"


class DuoAgentClient:
    def __init__(
        self,
        endpoint: str | None = None,
        token: str | None = None,
        offline: bool = False,
        timeout: float = 20.0,
    ) -> None:
        self.endpoint = (endpoint if endpoint is not None else os.environ.get("DUO_AGENT_ENDPOINT", "")).strip()
        self.token = token if token is not None else os.environ.get("DUO_AGENT_TOKEN", "")
        self.offline = offline or not self.endpoint
        self.timeout = timeout

    @property
    def mode(self) -> str:
        return OFFLINE_MODE if self.offline else LIVE_MODE

    def decide(self, result: ReviewResult) -> dict:
        if self.offline:
            return offline_decision(result)
        return self._live_decision(result)

    def _live_decision(self, result: ReviewResult) -> dict:
        payload = {
            "task": "post_merge_risk_review",
            "score": result.score,
            "level": result.level,
            "findings": [finding.to_dict() for finding in result.findings],
            "files": result.changeset.paths(),
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise DuoError(f"Duo endpoint returned HTTP {exc.code}: {body[:300]}") from exc
        except urllib.error.URLError as exc:
            raise DuoError(f"Duo endpoint unreachable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise DuoError(f"Duo endpoint timed out after {self.timeout}s") from exc
        parsed = _parse_json(raw)
        return {
            "mode": LIVE_MODE,
            "recommendation": str(parsed.get("recommendation", "review")),
            "rationale": str(parsed.get("rationale", "")),
            "raw": parsed,
        }


def _parse_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DuoError(f"Duo endpoint returned non-JSON body: {raw[:200]}") from exc
    if not isinstance(parsed, dict):
        raise DuoError("Duo endpoint returned JSON that is not an object")
    return parsed


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
        "recommendation": recommendation_for(result.score),
        "rationale": rationale,
        "rules_fired": fired,
        "note": "No Duo endpoint configured; decision produced by the local deterministic reasoner.",
    }
