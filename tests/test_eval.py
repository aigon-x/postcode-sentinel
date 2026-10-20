"""The rule engine is measured, not asserted: corpus labels and an ablation."""

from __future__ import annotations

import pytest

from sentinel import eval as ev
from sentinel import rules


@pytest.mark.parametrize("case", ev.CASES, ids=[case.name for case in ev.CASES])
def test_every_labelled_case_matches_its_label(case):
    assert ev.predict(case.diff) == case.expected


def test_metrics_react_when_a_rule_is_switched_off(monkeypatch):
    """Ablation: remove one rule and the measurement must notice."""
    healthy = ev.evaluate_corpus()
    assert healthy["ok"] is True and healthy["macro_recall"] == 1.0

    trimmed = tuple(rule for rule in rules.RULES if rule.id != "secret-in-diff")
    monkeypatch.setattr(rules, "RULES", trimmed)
    report = ev.evaluate_corpus()
    assert report["macro_recall"] < 1.0
    assert report["ok"] is False
    assert any("secret-in-diff" in message for message in report["mismatches"])
