"""Transport rules: no token over plaintext http, no token in an error message."""

from __future__ import annotations

import io
import urllib.error

import pytest

from sentinel.duo import DuoAgentClient, DuoError
from sentinel.gitlab_client import GitLabClient, GitLabError


def _client(**kwargs) -> GitLabClient:
    return GitLabClient(project_id="42", dry_run=True, **kwargs)


def test_https_base_url_is_accepted(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    assert _client(base_url="https://gitlab.example.com").base_url == "https://gitlab.example.com"


def test_plaintext_remote_base_url_is_refused(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    with pytest.raises(GitLabError) as excinfo:
        _client(base_url="http://gitlab.example.com")
    assert "clear text" in str(excinfo.value)


def test_plaintext_loopback_is_allowed_for_a_local_gitlab(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    assert _client(base_url="http://127.0.0.1:8080").base_url == "http://127.0.0.1:8080"


def test_url_with_credentials_or_unknown_scheme_is_refused(monkeypatch):
    monkeypatch.delenv("GITLAB_URL", raising=False)
    with pytest.raises(GitLabError) as excinfo:
        _client(base_url="https://user:secret@gitlab.example.com")
    assert "must not embed credentials" in str(excinfo.value)
    with pytest.raises(GitLabError):
        _client(base_url="file:///etc/passwd")


def test_duo_endpoint_over_plaintext_http_is_refused():
    with pytest.raises(DuoError) as excinfo:
        DuoAgentClient(endpoint="http://gitlab.example.com/api/v4/ai/duo_workflows/workflows")
    assert "clear text" in str(excinfo.value)


def test_token_is_redacted_from_gitlab_api_errors(monkeypatch):
    client = GitLabClient(base_url="https://gitlab.example.com", project_id="42",
                          token="sekrit-token", dry_run=False)

    def echoing_urlopen(request, timeout=None):  # noqa: ARG001 - urlopen signature
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"message":"bad PRIVATE-TOKEN sekrit-token"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", echoing_urlopen)
    with pytest.raises(GitLabError) as excinfo:
        client.create_issue("title", "body")
    assert "sekrit-token" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)
