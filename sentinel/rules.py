"""Deterministic rule engine.

Every rule inspects a :class:`ChangeSet` and returns zero or more findings.
Rules never call the network and never use randomness, so the same diff always
produces the same score and the same ordered list of reasons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .models import ChangeSet, DiffFile, Finding

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|testdata)(/|$)"
    r"|(^|/)test_[^/]*$|_test\.[a-z]+$|\.test\.[a-z]+$|\.spec\.[a-z]+$|Tests?\.(java|cs)$",
    re.IGNORECASE,
)
MIGRATION_PATH_RE = re.compile(
    r"(^|/)(migrations?|alembic|db/migrate|database/migrations|schema)(/|$)|\.sql$",
    re.IGNORECASE,
)
AUTH_PATH_RE = re.compile(
    r"(^|/)(auth|authn|authz|security|oauth|oidc|permissions?|acl|login|session)(/|$)"
    r"|(^|/)(middleware|guards?)\.[a-z]+$",
    re.IGNORECASE,
)
CI_PATH_RE = re.compile(
    r"(^|/)\.gitlab-ci\.ya?ml$|(^|/)\.github/workflows/|(^|/)Jenkinsfile$|(^|/)\.circleci/",
    re.IGNORECASE,
)
DEP_PATH_RE = re.compile(
    r"(^|/)(requirements[^/]*\.txt|package\.json|pyproject\.toml|go\.mod|Cargo\.toml|Gemfile|pom\.xml)$",
    re.IGNORECASE,
)
DOC_PATH_RE = re.compile(r"\.(md|rst|txt|adoc)$", re.IGNORECASE)

SOURCE_LANGUAGES = {"python", "javascript", "typescript", "go", "rust", "java", "ruby", "php"}

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("GitLab token", re.compile(r"glpat-[A-Za-z0-9_\-]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    (
        "hardcoded credential",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|passwd|password|client[_-]?secret)\b"
            r"\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
)

DESTRUCTIVE_SQL_RE = re.compile(
    r"(?i)\b(drop\s+(table|column|database|schema)|truncate\s+table|delete\s+from\s+\w+\s*;"
    r"|alter\s+table\s+\w+\s+drop)\b"
)
AUTH_WEAKENING_RE = re.compile(
    r"(?i)(verify\s*=\s*False|skip[_-]?auth|allow_all_origins|csrf_exempt|permission_classes\s*=\s*\[\s*\]"
    r"|@PermitAll|disable[_-]?auth)"
)
DEBUG_RE = re.compile(r"(?i)(\bprint\(|console\.log\(|\bdebugger\b|\bTODO\b|\bFIXME\b|\bXXX\b)")
TEST_CASE_RE = re.compile(r"(def test_|it\(|describe\(|@Test|func Test|test\(['\"])")


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    weight: int
    evaluate: Callable[[ChangeSet], list[Finding]]


def make_finding(rule: Rule, file: str, reason: str, evidence: list[str]) -> Finding:
    return Finding(
        rule_id=rule.id,
        title=rule.title,
        severity=rule.severity,
        weight=rule.weight,
        file=file,
        reason=reason,
        evidence=tuple(evidence[:5]),
    )


def is_test_file(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def is_doc_file(path: str) -> bool:
    return bool(DOC_PATH_RE.search(path)) and not path.endswith(".sql")


def is_source_file(diff_file: DiffFile) -> bool:
    return diff_file.language in SOURCE_LANGUAGES and not is_test_file(diff_file.path)


def _matching_added(diff_file: DiffFile, pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for line in diff_file.added_lines:
        if pattern.search(line):
            hits.append(line.strip()[:200])
    return hits


def _rule_secrets(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["secret-in-diff"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        evidence: list[str] = []
        labels: list[str] = []
        for label, pattern in SECRET_PATTERNS:
            hits = _matching_added(diff_file, pattern)
            if hits:
                labels.append(label)
                evidence.extend(hits)
        if evidence:
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"diff adds {', '.join(sorted(set(labels)))} in {diff_file.path}",
                    evidence,
                )
            )
    return findings


def _rule_removed_tests(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["removed-tests"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if is_test_file(diff_file.path):
            if diff_file.change_type == "deleted":
                findings.append(
                    make_finding(rule, diff_file.path, f"test file deleted: {diff_file.path}", [])
                )
                continue
            removed_cases = [ln.strip() for ln in diff_file.removed_lines if TEST_CASE_RE.search(ln)]
            added_cases = [ln.strip() for ln in diff_file.added_lines if TEST_CASE_RE.search(ln)]
            if removed_cases and len(removed_cases) > len(added_cases):
                findings.append(
                    make_finding(
                        rule,
                        diff_file.path,
                        f"{len(removed_cases)} test case(s) removed and only {len(added_cases)} added in {diff_file.path}",
                        removed_cases,
                    )
                )
    return findings


def _rule_migrations(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["migration-change"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if not MIGRATION_PATH_RE.search(diff_file.path):
            continue
        if is_test_file(diff_file.path):
            continue
        findings.append(
            make_finding(
                rule,
                diff_file.path,
                f"schema migration touched ({diff_file.change_type}): {diff_file.path}",
                [ln.strip()[:200] for ln in diff_file.added_lines[:3]],
            )
        )
    return findings


def _rule_destructive_sql(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["destructive-sql"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        hits = _matching_added(diff_file, DESTRUCTIVE_SQL_RE)
        if hits:
            findings.append(
                make_finding(rule, diff_file.path, f"destructive SQL added in {diff_file.path}", hits)
            )
    return findings


def _rule_auth_change(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["auth-change"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        weakening = _matching_added(diff_file, AUTH_WEAKENING_RE)
        touches_auth = bool(AUTH_PATH_RE.search(diff_file.path))
        if weakening:
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"authentication/authorization weakening pattern added in {diff_file.path}",
                    weakening,
                )
            )
        elif touches_auth and diff_file.change_type != "deleted":
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"auth-related module changed: {diff_file.path}",
                    [ln.strip()[:200] for ln in diff_file.added_lines[:3]],
                )
            )
    return findings


def _rule_logic_without_tests(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["logic-without-tests"]
    test_files = [f for f in changeset.files if is_test_file(f.path) and f.change_type != "deleted"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if not is_source_file(diff_file):
            continue
        if MIGRATION_PATH_RE.search(diff_file.path):
            continue
        churn = diff_file.added_count + diff_file.removed_count
        if churn < 5:
            continue
        if test_files:
            continue
        findings.append(
            make_finding(
                rule,
                diff_file.path,
                f"{churn} changed line(s) in {diff_file.path} with no test file in the same diff",
                diff_file.symbols()[:5],
            )
        )
    return findings


def _rule_deleted_source(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["deleted-source"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if diff_file.change_type != "deleted" or is_test_file(diff_file.path):
            continue
        if is_doc_file(diff_file.path):
            continue
        findings.append(
            make_finding(rule, diff_file.path, f"file removed: {diff_file.path}", [])
        )
    return findings


def _rule_ci_config(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["ci-config-change"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if CI_PATH_RE.search(diff_file.path):
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"CI/CD configuration changed ({diff_file.change_type}): {diff_file.path}",
                    [ln.strip()[:200] for ln in diff_file.added_lines[:3]],
                )
            )
    return findings


def _rule_dependency(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["dependency-change"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if DEP_PATH_RE.search(diff_file.path) and diff_file.change_type != "deleted":
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"dependency manifest changed: {diff_file.path}",
                    [ln.strip()[:200] for ln in diff_file.added_lines[:3]],
                )
            )
    return findings


def _rule_binary(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["binary-change"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if diff_file.binary:
            findings.append(
                make_finding(rule, diff_file.path, f"binary blob changed: {diff_file.path}", [])
            )
    return findings


def _rule_large_change(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["large-change"]
    churn = changeset.total_added + changeset.total_removed
    if churn <= 400:
        return []
    return [
        make_finding(
            rule,
            "<changeset>",
            f"large diff: {churn} changed lines across {len(changeset.files)} file(s)",
            [],
        )
    ]


def _rule_debug_leftovers(changeset: ChangeSet) -> list[Finding]:
    rule = RULES_BY_ID["debug-leftovers"]
    findings: list[Finding] = []
    for diff_file in changeset.files:
        if not is_source_file(diff_file):
            continue
        hits = _matching_added(diff_file, DEBUG_RE)
        if hits:
            findings.append(
                make_finding(
                    rule,
                    diff_file.path,
                    f"debug/TODO markers added in {diff_file.path}",
                    hits,
                )
            )
    return findings


RULES: tuple[Rule, ...] = (
    Rule("secret-in-diff", "Secret material in diff", "critical", 40, _rule_secrets),
    Rule("destructive-sql", "Destructive SQL", "critical", 30, _rule_destructive_sql),
    Rule("removed-tests", "Tests removed", "high", 25, _rule_removed_tests),
    Rule("migration-change", "Schema migration touched", "high", 20, _rule_migrations),
    Rule("auth-change", "Auth or permission surface changed", "high", 20, _rule_auth_change),
    Rule("logic-without-tests", "Logic changed without tests", "medium", 20, _rule_logic_without_tests),
    Rule("deleted-source", "Source file deleted", "medium", 15, _rule_deleted_source),
    Rule("ci-config-change", "CI/CD pipeline changed", "medium", 10, _rule_ci_config),
    Rule("dependency-change", "Dependency manifest changed", "low", 10, _rule_dependency),
    Rule("binary-change", "Binary file changed", "low", 10, _rule_binary),
    Rule("large-change", "Large changeset", "low", 10, _rule_large_change),
    Rule("debug-leftovers", "Debug leftovers", "low", 5, _rule_debug_leftovers),
)

RULES_BY_ID: dict[str, Rule] = {rule.id: rule for rule in RULES}


def evaluate(changeset: ChangeSet) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule.evaluate(changeset))
    return findings

