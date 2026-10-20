import io
import json

import pytest

from sentinel.diff_parser import parse_unified_diff
from sentinel.duo import OFFLINE_MODE, DuoAgentClient, DuoError, offline_decision
from sentinel.scoring import analyze_changeset


def _result(text):
    return analyze_changeset(parse_unified_diff(text))


def test_offline_reasoner_is_labeled(risky_diff_text):
    decision = DuoAgentClient(offline=True).decide(_result(risky_diff_text))
    assert decision["mode"] == OFFLINE_MODE
    assert decision["recommendation"] == "revert"
    assert "local deterministic reasoner" in decision["note"]


def test_offline_reasoner_approves_clean_diff(clean_diff_text):
    decision = offline_decision(_result(clean_diff_text))
    assert decision["recommendation"] == "approve"
    assert decision["rules_fired"] == []


def test_live_mode_requires_endpoint_configured(monkeypatch):
    monkeypatch.delenv("DUO_AGENT_ENDPOINT", raising=False)
    client = DuoAgentClient()
    assert client.mode == OFFLINE_MODE
    assert client.decide(_result("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"))["mode"] == OFFLINE_MODE


def test_live_mode_posts_and_parses_response(monkeypatch, risky_diff_text):
    class FakeResponse(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"recommendation": "revert", "rationale": "secret found"}).encode())

    monkeypatch.setattr("sentinel.duo.urllib.request.urlopen", fake_urlopen)
    client = DuoAgentClient(endpoint="https://duo.example/agent", token="dummy", offline=False)
    decision = client.decide(_result(risky_diff_text))
    assert decision["mode"] == "live-duo-endpoint"
    assert decision["recommendation"] == "revert"
    assert captured["body"]["score"] == 100
    assert captured["url"] == "https://duo.example/agent"


def test_live_mode_error_is_explicit(monkeypatch, risky_diff_text):
    import urllib.error

    def failing_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("sentinel.duo.urllib.request.urlopen", failing_urlopen)
    client = DuoAgentClient(endpoint="https://duo.example/agent", offline=False)
    with pytest.raises(DuoError) as excinfo:
        client.decide(_result(risky_diff_text))
    assert "unreachable" in str(excinfo.value)
