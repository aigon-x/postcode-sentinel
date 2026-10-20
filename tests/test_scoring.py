from sentinel.diff_parser import parse_unified_diff
from sentinel.scoring import analyze_changeset, level_for


def test_risky_diff_scores_at_least_60(risky_diff_text):
    result = analyze_changeset(parse_unified_diff(risky_diff_text))
    assert result.score >= 60
    assert result.level in {"high", "critical"}
    assert result.reasons, "score must be backed by reasons"
    assert len(result.findings) >= 3


def test_clean_diff_scores_below_20(clean_diff_text):
    result = analyze_changeset(parse_unified_diff(clean_diff_text))
    assert result.score < 20
    assert result.level == "low"


def test_score_is_capped_and_traceable(risky_diff_text):
    result = analyze_changeset(parse_unified_diff(risky_diff_text))
    assert result.score <= 100
    contributions = result.summary["contributions"]
    assert sum(contributions.values()) >= result.score
    for rule_id in contributions:
        assert any(f.rule_id == rule_id for f in result.findings)


def test_level_thresholds():
    assert level_for(0) == "low"
    assert level_for(30) == "moderate"
    assert level_for(55) == "high"
    assert level_for(75) == "critical"
