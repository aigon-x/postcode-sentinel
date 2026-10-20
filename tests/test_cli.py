import json
from pathlib import Path

from sentinel.cli import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_analyze_json_reports_score(risky_diff_text, capsys):
    exit_code = main(["analyze", str(FIXTURES / "risky.diff"), "--format", "json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["score"] >= 60
    assert payload["mode"] == "offline-local-reasoner"
    assert payload["reasons"]


def test_analyze_markdown_contains_reasons(capsys):
    exit_code = main(["analyze", str(FIXTURES / "clean.diff"), "--format", "md"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PostCode Sentinel" in out
    assert "Risk score" in out
    assert "offline-local-reasoner" in out


def test_review_dry_run_previews_actions(monkeypatch, capsys):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setenv("GITLAB_PROJECT_ID", "42")
    exit_code = main(["review", str(FIXTURES / "risky.diff"), "--mr-iid", "7", "--format", "json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert {action["kind"] for action in payload["actions"]} == {"comment_mr", "create_issue"}
    assert all(action["dry_run"] for action in payload["actions"])


def test_review_live_without_token_fails_clearly(monkeypatch, capsys):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.setenv("GITLAB_PROJECT_ID", "42")
    exit_code = main(["review", str(FIXTURES / "clean.diff"), "--live", "--mr-iid", "7"])
    assert exit_code == 2
    assert "GITLAB_TOKEN is not set" in capsys.readouterr().err


def test_analyze_missing_file_reports_error(capsys):
    exit_code = main(["analyze", "/nonexistent/merged.diff"])
    assert exit_code == 2
    assert "diff file not found" in capsys.readouterr().err
