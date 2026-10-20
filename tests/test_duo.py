import io
import json
import urllib.error

import pytest

from sentinel.diff_parser import parse_unified_diff
from sentinel.duo import (
    LIVE_MODE,
    OFFLINE_MODE,
    DuoAgentClient,
    DuoError,
    offline_decision,
)
from sentinel.scoring import analyze_changeset


def _result(text):
    return analyze_changeset(parse_unified_diff(text))


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _transport(body, captured=None, status=200):
    def transport(request, timeout=None):
        if captured is not None:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
        response = FakeResponse(json.dumps(body).encode("utf-8"))
        response.status = status
        return response

    return transport


def test_offline_reasoner_is_labeled(risky_diff_text):
    decision = DuoAgentClient(offline=True).decide(_result(risky_diff_text))
    assert decision["mode"] == OFFLINE_MODE
    assert decision["reasoner"] == "local-deterministic"
    assert decision["recommendation"] == "revert"
    assert "no Duo API call was made" in decision["note"]


def test_offline_reasoner_approves_clean_diff(clean_diff_text):
    decision = offline_decision(_result(clean_diff_text))
    assert decision["recommendation"] == "approve"
    assert decision["rules_fired"] == []


def test_offline_mode_never_calls_transport(monkeypatch, risky_diff_text):
    def fail_transport(*args, **kwargs):
        raise AssertionError("transport must not be used in offline mode")

    client = DuoAgentClient(offline=True, transport=fail_transport)
    assert client.decide(_result(risky_diff_text))["mode"] == OFFLINE_MODE


def test_live_mode_without_token_fails_clearly(monkeypatch, risky_diff_text):
    monkeypatch.delenv("GITLAB_DUO_TOKEN", raising=False)
    client = DuoAgentClient(project_id="42", transport=lambda *a, **k: None)
    assert client.mode == LIVE_MODE
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "GITLAB_DUO_TOKEN is not set" in str(excinfo.value)
    assert "--offline" in str(excinfo.value)


def test_live_mode_without_project_id_fails_clearly(monkeypatch, risky_diff_text):
    monkeypatch.delenv("GITLAB_PROJECT_ID", raising=False)
    monkeypatch.delenv("CI_PROJECT_ID", raising=False)
    client = DuoAgentClient(token="dummy", transport=lambda *a, **k: None)
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "GITLAB_PROJECT_ID is not set" in str(excinfo.value)


def test_live_mode_posts_duo_workflow_and_parses_response(risky_diff_text):
    captured = {}
    client = DuoAgentClient(
        endpoint="https://gitlab.example.com/api/v4/ai/duo_workflows/workflows",
        token="secret-token",
        project_id="42",
        transport=_transport(
            {"id": 17, "status": "finished", "recommendation": "revert", "rationale": "secret found"},
            captured,
        ),
    )
    decision = client.decide(_result(risky_diff_text))

    assert decision["mode"] == LIVE_MODE
    assert decision["recommendation"] == "revert"
    assert decision["workflow_id"] == 17
    assert decision["endpoint"].endswith("/api/v4/ai/duo_workflows/workflows")
    assert captured["url"] == "https://gitlab.example.com/api/v4/ai/duo_workflows/workflows"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["body"]["project_id"] == 42
    assert captured["body"]["flow_config_id"] == "postcode-sentinel"
    assert captured["body"]["flow_config_schema_version"] == "v1"
    assert captured["body"]["environment"] == "ambient"
    assert captured["body"]["context"]["score"] == 100
    assert "secret-in-diff" in captured["body"]["goal"]


def test_live_mode_parses_nested_result_json(risky_diff_text):
    client = DuoAgentClient(
        endpoint="https://duo.example/flow",
        token="t",
        project_id="42",
        transport=_transport(
            {"id": 3, "status": "finished", "result": json.dumps({"recommendation": "block_and_followup", "rationale": "tests removed"})}
        ),
    )
    decision = client.decide(_result(risky_diff_text))
    assert decision["recommendation"] == "block_and_followup"
    assert decision["rationale"] == "tests removed"


def test_live_mode_rejects_missing_recommendation(risky_diff_text):
    client = DuoAgentClient(
        endpoint="https://duo.example/flow",
        token="t",
        project_id="42",
        transport=_transport({"id": 9, "status": "running"}),
    )
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "did not contain a valid 'recommendation'" in str(excinfo.value)
    assert "workflow id: 9" in str(excinfo.value)


def test_live_mode_http_error_is_explicit(risky_diff_text):
    def failing_transport(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"message":"401 Unauthorized"}')
        )

    client = DuoAgentClient(
        endpoint="https://duo.example/flow", token="bad", project_id="42", transport=failing_transport
    )
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "HTTP 401" in str(excinfo.value)
    assert "GITLAB_DUO_TOKEN" in str(excinfo.value)


def test_live_mode_unreachable_is_explicit(risky_diff_text):
    def failing_transport(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    client = DuoAgentClient(
        endpoint="https://duo.example/flow", token="t", project_id="42", transport=failing_transport
    )
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "unreachable" in str(excinfo.value)


def test_live_mode_non_json_is_explicit(risky_diff_text):
    def transport(request, timeout=None):
        return FakeResponse(b"<html>gateway error</html>")

    client = DuoAgentClient(
        endpoint="https://duo.example/flow", token="t", project_id="42", transport=transport
    )
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "non-JSON" in str(excinfo.value)


def test_endpoint_defaults_to_gitlab_workflow_api(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
    monkeypatch.delenv("GITLAB_DUO_ENDPOINT", raising=False)
    client = DuoAgentClient(token="t", project_id="42", offline=False)
    assert client.endpoint == "https://gitlab.example.com/api/v4/ai/duo_workflows/workflows"
