import pytest

from sentinel.gitlab_client import GitLabClient, GitLabError


def test_dry_run_comment_does_not_hit_network(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com")
    client = GitLabClient(project_id="42", dry_run=True)
    action = client.comment_merge_request(7, "report body")
    assert action.dry_run is True
    assert action.method == "POST"
    assert action.url == "https://gitlab.example.com/api/v4/projects/42/merge_requests/7/notes"
    assert action.payload["body"] == "report body"
    assert client.actions == [action]


def test_live_without_token_raises_readable_error(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    client = GitLabClient(project_id="42", token="", dry_run=False)
    with pytest.raises(GitLabError) as excinfo:
        client.create_issue("title", "description")
    assert "GITLAB_TOKEN is not set" in str(excinfo.value)
    assert "--dry-run" in str(excinfo.value)


def test_live_without_project_id_raises(monkeypatch):
    client = GitLabClient(project_id="", token="dummy-token", dry_run=False)
    with pytest.raises(GitLabError) as excinfo:
        client.comment_merge_request(1, "body")
    assert "GITLAB_PROJECT_ID is not set" in str(excinfo.value)


def test_invalid_inputs_are_rejected():
    client = GitLabClient(project_id="42", dry_run=True)
    with pytest.raises(GitLabError):
        client.comment_merge_request("not-a-number", "body")
    with pytest.raises(GitLabError):
        client.create_issue("  ", "description")


def test_live_dispatch_uses_mocked_transport(monkeypatch):
    client = GitLabClient(project_id="42", token="dummy-token", dry_run=False)
    calls = []

    def fake_request(method, url, payload):
        calls.append((method, url, payload))
        return {"status": 201, "body": {"id": 99}}

    monkeypatch.setattr(client, "_request", fake_request)
    action = client.create_issue("risk 80", "body", labels=["postcode-sentinel"])
    assert calls and calls[0][0] == "POST"
    assert action.status == 201
    assert action.response == {"id": 99}
