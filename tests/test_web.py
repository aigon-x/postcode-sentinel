"""HTTP contract tests for the FastAPI web layer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pydantic import ValidationError

from web.server import MAX_DIFF_CHARS, MAX_REQUEST_BYTES, AnalyzeRequest, _body_rejection, app

ROOT = Path(__file__).resolve().parent.parent
RISKY_DIFF = ROOT / "tests" / "fixtures" / "risky.diff"
CLEAN_DIFF = ROOT / "tests" / "fixtures" / "clean.diff"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_index_serves_ui(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "PostCode Sentinel" in body
    assert 'id="diff"' in body
    assert 'id="analyze"' in body
    assert "prefers-reduced-motion" in body


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_analyze_risky_diff_scores_high(client: TestClient) -> None:
    diff = RISKY_DIFF.read_text(encoding="utf-8")
    response = client.post("/api/analyze", json={"diff": diff})
    assert response.status_code == 200
    payload = response.json()
    assert payload["score"] >= 60
    assert payload["level"] in {"high", "critical", "moderate"}
    assert payload["findings"], "risky diff must produce findings"
    assert payload["markdown"].startswith("## PostCode Sentinel")
    assert payload["files_total"] >= 1
    finding = payload["findings"][0]
    assert {"rule", "title", "severity", "weight", "file", "reason"} <= set(finding)


def test_analyze_clean_diff_scores_low(client: TestClient) -> None:
    diff = (ROOT / "tests" / "fixtures" / "clean.diff").read_text(encoding="utf-8")
    response = client.post("/api/analyze", json={"diff": diff})
    assert response.status_code == 200
    assert response.json()["score"] < 60


@pytest.mark.parametrize("value", ["", "   ", "\n\t\n"])
def test_analyze_empty_diff_is_rejected(client: TestClient, value: str) -> None:
    response = client.post("/api/analyze", json={"diff": value})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert isinstance(detail, str) and "empty" in detail.lower()


def test_analyze_garbage_input_is_rejected(client: TestClient) -> None:
    response = client.post("/api/analyze", json={"diff": "this is not a diff"})
    assert response.status_code == 400
    assert "diff" in response.json()["detail"].lower()


def test_oversized_diff_is_refused_before_the_analyzer(client: TestClient, monkeypatch) -> None:
    """A public demo cannot buffer an unbounded body: 8.6 MB cost one worker 7.3 s."""

    def boom(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("the analyzer must not see an oversized body")

    monkeypatch.setattr("web.server.analyze_changeset", boom)
    body = '{"diff":"' + "x" * (MAX_REQUEST_BYTES + 10) + '"}'
    response = client.post("/api/analyze", content=body,
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert "MB" in response.json()["detail"]


def test_content_length_policy() -> None:
    """The decision uses the header alone, so the body is never read to find out."""
    assert _body_rejection(None).status_code == 411
    assert _body_rejection("not-a-number").status_code == 413
    assert _body_rejection(str(MAX_REQUEST_BYTES + 1)).status_code == 413
    assert _body_rejection(str(MAX_REQUEST_BYTES)) is None


def test_model_also_caps_the_diff() -> None:
    with pytest.raises(ValidationError):
        AnalyzeRequest(diff="x" * (MAX_DIFF_CHARS + 1))


def test_small_diff_still_works(client: TestClient) -> None:
    response = client.post("/api/analyze", json={"diff": RISKY_DIFF.read_text(encoding="utf-8")})
    assert response.status_code == 200
    assert response.json()["score"] >= 55


def test_analysis_reports_the_recommended_action(client: TestClient) -> None:
    """The product promises comment / block / revert; the demo has to show the decision."""
    body = client.post("/api/analyze", json={"diff": RISKY_DIFF.read_text(encoding="utf-8")}).json()
    assert body["score"] >= 75
    assert body["recommendation"] == "revert"
    assert body["decision_mode"] == "offline"
    assert body["rationale"].startswith("Score ")
    assert "recommendation: **revert**" in body["markdown"]
    assert 'id="action-badge"' in client.get("/").text


def test_recommendation_uses_the_shared_mapping(client: TestClient) -> None:
    """The web layer must not grow a second policy next to sentinel.duo.recommendation_for."""
    from sentinel.duo import recommendation_for

    for fixture, expected in ((RISKY_DIFF, "revert"), (CLEAN_DIFF, "approve")):
        body = client.post("/api/analyze", json={"diff": fixture.read_text(encoding="utf-8")}).json()
        assert body["recommendation"] == recommendation_for(body["score"]) == expected
