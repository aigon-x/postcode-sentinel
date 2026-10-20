"""Labelled corpus and metrics for the deterministic rule engine.

The engine always returns a score, so "it works" cannot be read off one diff. This
module keeps seven hand-labelled diffs — one per rule class plus a clean control —
and reports precision and recall per rule. The labels encode the rule contract in
``rules.py``: a rule that stops firing, or one that fires on the control, fails the corpus
loudly. The corpus is synthetic; it proves the rules do what they say, not that they match
a real repository's risk appetite.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diff_parser import parse_unified_diff
from .rules import evaluate

MIN_RECALL = 0.9
MIN_PRECISION = 0.9


@dataclass(frozen=True)
class Case:
    name: str
    diff: str
    expected: frozenset[str]


def _diff(path: str, removed: list[str], added: list[str]) -> str:
    header = (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
              f"@@ -1,{len(removed)} +1,{len(added)} @@\n")
    return header + "".join(f"-{line}\n" for line in removed) + "".join(f"+{line}\n" for line in added)


CASES: tuple[Case, ...] = (
    Case("control-doc-only", _diff("README.md", ["# PostCode"], ["# PostCode Sentinel"]), frozenset()),
    Case("secret-in-diff",
         _diff("src/settings.py", ["DEBUG = False"],
               ["DEBUG = False", 'TOKEN = "glpat-abcdefghijklmnopqrst"']),
         frozenset({"secret-in-diff"})),
    Case("destructive-sql-on-migration",
         _diff("migrations/0007_drop.sql", ["DROP TABLE audit_log;"], ["DROP TABLE users;"]),
         frozenset({"destructive-sql", "migration-change"})),
    Case("tests-removed",
         _diff("tests/test_app.py", ["def test_alpha():", "def test_gamma():"], ["def test_beta():"]),
         frozenset({"removed-tests"})),
    Case("auth-weakening",
         _diff("src/http.py", ["requests.get(url)"], ["requests.get(url)", "requests.get(url, verify=False)"]),
         frozenset({"auth-change"})),
    Case("debug-leftover",
         _diff("src/app.py", ["def main():"], ["def main():", '    print("debug here")']),
         frozenset({"debug-leftovers"})),
    Case("ci-config-changed",
         _diff(".gitlab-ci.yml", ["stages: [test]"], ["stages: [test]", "  timeout: 1h"]),
         frozenset({"ci-config-change"})),
)


def predict(diff: str) -> frozenset[str]:
    return frozenset(finding.rule_id for finding in evaluate(parse_unified_diff(diff)))


def evaluate_corpus() -> dict:
    predictions = [(case, predict(case.diff)) for case in CASES]
    labels = sorted({rule for case in CASES for rule in case.expected})
    rows = []
    for rule in labels:
        tp = sum(1 for case, got in predictions if rule in case.expected and rule in got)
        fn = sum(1 for case, got in predictions if rule in case.expected and rule not in got)
        fp = sum(1 for case, got in predictions if rule not in case.expected and rule in got)
        rows.append({"rule": rule, "tp": tp, "fp": fp, "fn": fn,
                     "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
                     "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0})
    macro_precision = round(sum(r["precision"] for r in rows) / len(rows), 3) if rows else 0.0
    macro_recall = round(sum(r["recall"] for r in rows) / len(rows), 3) if rows else 0.0
    mismatches = [f"{case.name}: expected {sorted(case.expected)} got {sorted(got)}"
                  for case, got in predictions if case.expected != got]
    return {"cases": len(CASES), "rules": rows, "macro_precision": macro_precision,
            "macro_recall": macro_recall, "mismatches": mismatches,
            "ok": macro_precision >= MIN_PRECISION and macro_recall >= MIN_RECALL and not mismatches}


def main() -> int:
    report = evaluate_corpus()
    print(f"cases={report['cases']} rules={len(report['rules'])} "
          f"macro_precision={report['macro_precision']} macro_recall={report['macro_recall']}")
    for row in report["rules"]:
        print(f"  {row['rule']:22s} tp={row['tp']} fp={row['fp']} fn={row['fn']} "
              f"precision={row['precision']} recall={row['recall']}")
    for message in report["mismatches"]:
        print(f"  MISMATCH {message}")
    print("OK" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
