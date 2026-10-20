from sentinel.diff_parser import parse_unified_diff
from sentinel.rules import evaluate


def _findings(risky_diff_text):
    return evaluate(parse_unified_diff(risky_diff_text))


def test_secret_rule_fires_on_added_credential(risky_diff_text):
    findings = [f for f in _findings(risky_diff_text) if f.rule_id == "secret-in-diff"]
    assert findings, "expected a secret finding"
    assert findings[0].file == "app/settings.py"
    assert findings[0].weight == 40
    assert "AWS access key" in findings[0].reason


def test_removed_tests_and_migration_rules(risky_diff_text):
    rule_ids = {f.rule_id for f in _findings(risky_diff_text)}
    assert "removed-tests" in rule_ids
    assert "migration-change" in rule_ids
    assert "destructive-sql" in rule_ids


def test_evaluation_is_deterministic(risky_diff_text):
    first = [f.to_dict() for f in _findings(risky_diff_text)]
    second = [f.to_dict() for f in _findings(risky_diff_text)]
    assert first == second


def test_clean_diff_has_no_high_severity_findings(clean_diff_text):
    findings = evaluate(parse_unified_diff(clean_diff_text))
    assert all(f.severity in {"low"} for f in findings)
    assert not any(f.rule_id in {"secret-in-diff", "removed-tests", "migration-change"} for f in findings)
