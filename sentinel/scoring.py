"""Deterministic risk scoring.

Score = sum of the weights of the rule *classes* that fired, capped at 100.
A rule contributes its weight once no matter how many files it matched, so the
score reflects distinct risk classes rather than diff size. Every point is
traceable to a finding and its reason string.
"""

from __future__ import annotations

from .models import ChangeSet, Finding, ReviewResult
from .rules import RULES, evaluate

LEVELS = (
    (75, "critical"),
    (55, "high"),
    (30, "moderate"),
    (0, "low"),
)


def score_findings(findings: list[Finding]) -> tuple[int, dict[str, int]]:
    contributions: dict[str, int] = {}
    for finding in findings:
        contributions.setdefault(finding.rule_id, finding.weight)
    total = min(100, sum(contributions.values()))
    return total, contributions


def level_for(score: int) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "low"


def analyze_changeset(changeset: ChangeSet, findings: list[Finding] | None = None) -> ReviewResult:
    if findings is None:
        findings = evaluate(changeset)
    score, contributions = score_findings(findings)
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
    summary = {
        "rules_total": len(RULES),
        "rules_fired": sorted(contributions),
        "contributions": contributions,
        "severity_counts": severity_counts,
    }
    return ReviewResult(
        score=score,
        level=level_for(score),
        findings=tuple(findings),
        changeset=changeset,
        summary=summary,
    )
