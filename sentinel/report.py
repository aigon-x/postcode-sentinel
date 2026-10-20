"""Rendering of review results to Markdown and JSON."""

from __future__ import annotations

import json

from .models import ReviewResult


def render_json(result: ReviewResult, extra: dict | None = None) -> str:
    payload = result.to_dict()
    if extra:
        payload.update(extra)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_markdown(result: ReviewResult, mode: str = "offline", duo: dict | None = None) -> str:
    lines: list[str] = []
    lines.append("## PostCode Sentinel — post-merge risk review")
    lines.append("")
    lines.append(f"**Risk score:** `{result.score}/100` ({result.level})  ")
    lines.append(f"**Mode:** `{mode}`  ")
    lines.append(
        f"**Changeset:** {len(result.changeset.files)} file(s), "
        f"+{result.changeset.total_added}/-{result.changeset.total_removed} lines"
    )
    lines.append("")
    if result.findings:
        lines.append("| Severity | Rule | File | Weight | Reason |")
        lines.append("|---|---|---|---:|---|")
        for finding in result.findings:
            lines.append(
                f"| {finding.severity} | `{finding.rule_id}` | `{finding.file}` "
                f"| {finding.weight} | {finding.reason} |"
            )
    else:
        lines.append("No risk rules fired on this diff.")
    lines.append("")

    if duo:
        lines.append("### Duo agent decision")
        lines.append("")
        lines.append(f"- mode: `{duo.get('mode', 'unknown')}`")
        lines.append(f"- recommendation: **{duo.get('recommendation', 'n/a')}**")
        lines.append(f"- rationale: {duo.get('rationale', '')}")
        lines.append("")

    lines.append("### Suggested actions")
    lines.append("")
    for action in suggested_actions(result):
        lines.append(f"- {action}")
    lines.append("")
    return "\n".join(lines)


def suggested_actions(result: ReviewResult) -> list[str]:
    actions: list[str] = []
    fired = {finding.rule_id for finding in result.findings}
    if "secret-in-diff" in fired:
        actions.append("Rotate the exposed credential immediately and purge it from history.")
    if "destructive-sql" in fired:
        actions.append("Block further merges until the destructive migration is reviewed by a DBA.")
    if "removed-tests" in fired:
        actions.append("Restore or replace the removed test coverage before the next release.")
    if "migration-change" in fired:
        actions.append("Verify forward and rollback migrations against a production-like snapshot.")
    if "auth-change" in fired:
        actions.append("Request a security review of the changed authentication surface.")
    if result.score >= 75:
        actions.append("Open a blocking issue and propose reverting the merge commit.")
    elif result.score >= 55:
        actions.append("Open a follow-up issue and request a reviewer from the owning team.")
    elif result.score >= 30:
        actions.append("Leave an MR comment with this report and ask the author to confirm.")
    else:
        actions.append("No action required; keep the report attached to the merge for the audit trail.")
    return actions
