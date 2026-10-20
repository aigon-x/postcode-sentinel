"""Command line interface: ``analyze`` and ``review``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diff_parser import DiffParseError, parse_unified_diff
from .duo import DuoAgentClient, DuoError
from .gitlab_client import GitLabClient, GitLabError
from .report import render_json, render_markdown
from .scoring import analyze_changeset

ISSUE_THRESHOLD = 55


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="PostCode Sentinel — deterministic post-merge risk review for GitLab.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="score a unified diff and print a report")
    analyze.add_argument("diff_file", help="path to a unified diff")
    analyze.add_argument("--format", choices=("json", "md"), default="md")
    analyze.add_argument(
        "--offline",
        action="store_true",
        help="use the local deterministic reasoner instead of calling Duo",
    )
    analyze.add_argument("--duo-endpoint", default=None, help="Duo workflow API URL (or GITLAB_DUO_ENDPOINT)")

    review = sub.add_parser("review", help="score a diff and propose GitLab actions")
    review.add_argument("diff_file", help="path to a unified diff")
    review.add_argument("--dry-run", action="store_true", default=True, help="preview actions (default)")
    review.add_argument("--live", action="store_true", help="perform GitLab API calls; requires GITLAB_TOKEN")
    review.add_argument("--mr-iid", default=None, help="merge request iid to comment on")
    review.add_argument("--issue", action="store_true", help="force issue creation regardless of score")
    review.add_argument("--gitlab-url", default=None, help="GitLab base URL (or GITLAB_URL)")
    review.add_argument("--project-id", default=None, help="GitLab project id (or GITLAB_PROJECT_ID)")
    review.add_argument("--format", choices=("json", "md"), default="json")
    review.add_argument(
        "--offline",
        action="store_true",
        help="use the local deterministic reasoner instead of calling Duo",
    )

    return parser


def _load_diff(path: str):
    diff_path = Path(path)
    if not diff_path.is_file():
        raise FileNotFoundError(f"diff file not found: {path}")
    text = diff_path.read_text(encoding="utf-8", errors="replace")
    return parse_unified_diff(text, source=str(diff_path))


def cmd_analyze(args: argparse.Namespace) -> int:
    changeset = _load_diff(args.diff_file)
    result = analyze_changeset(changeset)
    duo = DuoAgentClient(endpoint=args.duo_endpoint, offline=args.offline)
    try:
        decision = duo.decide(result)
    except DuoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    mode = decision["mode"]
    if args.format == "json":
        print(render_json(result, {"mode": mode, "duo": decision}))
    else:
        print(render_markdown(result, mode=mode, duo=decision))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    changeset = _load_diff(args.diff_file)
    result = analyze_changeset(changeset)
    duo = DuoAgentClient(offline=args.offline, project_id=args.project_id)
    try:
        decision = duo.decide(result)
    except DuoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dry_run = not args.live
    client = GitLabClient(
        base_url=args.gitlab_url,
        project_id=args.project_id,
        dry_run=dry_run,
    )
    body = render_markdown(result, mode=decision["mode"], duo=decision)
    actions = []
    try:
        if args.mr_iid:
            actions.append(client.comment_merge_request(args.mr_iid, body).to_dict())
        if args.issue or result.score >= ISSUE_THRESHOLD:
            title = f"[postcode-sentinel] risk {result.score}/100 on {Path(args.diff_file).name}"
            description = body
            actions.append(client.create_issue(title, description, labels=["postcode-sentinel"]).to_dict())
    except GitLabError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "dry_run": dry_run,
        "mode": decision["mode"],
        "score": result.score,
        "level": result.level,
        "recommendation": decision["recommendation"],
        "actions": actions,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(result, mode=decision["mode"], duo=decision))
        print("### Planned GitLab actions" if dry_run else "### Executed GitLab actions")
        for action in actions:
            print(f"- {action['kind']}: {action['method']} {action['url']}")
        if not actions:
            print("- none (pass --mr-iid, or a score above the issue threshold creates one)")
        if dry_run:
            print(f"\nDry-run only: {len(actions)} action(s) not sent. Use --live to execute.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return cmd_analyze(args)
        return cmd_review(args)
    except DiffParseError as exc:
        print(f"error: unparseable diff: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DuoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
