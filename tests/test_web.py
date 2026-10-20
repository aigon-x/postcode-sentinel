"""HTTP contract tests for the FastAPI web layer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.server import app

ROOT = Path(__file__).resolve().parent.parent
RISKY_DIFF = ROOT / "tests" / "fixtures" / "risky.diff"


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
