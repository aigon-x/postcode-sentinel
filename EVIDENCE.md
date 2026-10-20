# Evidence

All commands run from the `project/` directory. Outputs below are copied from
the terminal, not retyped.

## Environment

```
$ date -Is
2026-10-20T11:54:36+02:00

$ python3 --version
Python 3.11.15
```

Runtime code uses the standard library only; `pytest 9.0.3` is the test runner.
The CI image is `python:3.12-slim` (see `.gitlab-ci.yml`); the local host here
has 3.11.15, which is why the README states 3.11+.

## Test suite

```
$ python3 -m pytest tests -q
............................                                             [100%]
28 passed in 0.10s
```

Required negative case (clean diff must score below 20):

```
$ python3 -m pytest tests/test_scoring.py::test_clean_diff_scores_below_20 -q
.                                                                        [100%]
1 passed in 0.03s
```

The assertion in that test:

```python
result = analyze_changeset(parse_unified_diff(clean_diff_text))
assert result.score < 20
assert result.level == "low"
```

Observed score for `tests/fixtures/clean.diff` is `0` (see below).

Covered areas: parser (git and plain diffs, added/deleted files, hunk symbols,
rejection of non-diffs), rules (secret, removed tests, migration, destructive
SQL, determinism, clean diff severity), scoring (risky ≥ 60, clean < 20, cap and
traceability, level thresholds), GitLab client (dry-run, missing token, missing
project id, invalid input, mocked live dispatch), Duo (offline label, clean
approval, live POST parsing, live failure), CLI (json/md output, dry-run action
preview, live without token, missing file).

## Risky fixture — score and reasons

```
$ python3 -m sentinel analyze tests/fixtures/risky.diff --format md
## PostCode Sentinel — post-merge risk review

**Risk score:** `100/100` (critical)
**Mode:** `offline-local-reasoner`
**Changeset:** 6 file(s), +24/-14 lines

| Severity | Rule | File | Weight | Reason |
|---|---|---|---:|---|
| critical | `secret-in-diff` | `app/settings.py` | 40 | diff adds AWS access key in app/settings.py |
| critical | `destructive-sql` | `migrations/0007_drop_orders_table.py` | 30 | destructive SQL added in migrations/0007_drop_orders_table.py |
| high | `removed-tests` | `tests/test_billing.py` | 25 | test file deleted: tests/test_billing.py |
| high | `migration-change` | `migrations/0007_drop_orders_table.py` | 20 | schema migration touched (added): migrations/0007_drop_orders_table.py |
| high | `auth-change` | `app/auth/tokens.py` | 20 | authentication/authorization weakening pattern added in app/auth/tokens.py |
| medium | `logic-without-tests` | `app/billing.py` | 20 | 10 changed line(s) in app/billing.py with no test file in the same diff |
| medium | `ci-config-change` | `.gitlab-ci.yml` | 10 | CI/CD configuration changed (modified): .gitlab-ci.yml |
| low | `debug-leftovers` | `app/auth/tokens.py` | 5 | debug/TODO markers added in app/auth/tokens.py |

### Duo agent decision

- mode: `offline-local-reasoner`
- recommendation: **revert**
- rationale: Score 100/100 (critical). Rules fired: auth-change, ci-config-change, debug-leftovers, destructive-sql, logic-without-tests, migration-change, removed-tests, secret-in-diff. Top reason: diff adds AWS access key in app/settings.py

### Suggested actions

- Rotate the exposed credential immediately and purge it from history.
- Block further merges until the destructive migration is reviewed by a DBA.
- Restore or replace the removed test coverage before the next release.
- Verify forward and rollback migrations against a production-like snapshot.
- Request a security review of the changed authentication surface.
- Open a blocking issue and propose reverting the merge commit.
```

## Clean fixture — negative path

```
$ python3 -m sentinel analyze tests/fixtures/clean.diff --format json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in ("score","level","files_total","findings","mode")}, indent=2))'
{
  "score": 0,
  "level": "low",
  "files_total": 3,
  "findings": [],
  "mode": "offline-local-reasoner"
}
```

## Dry-run review — no network

```
$ env -u GITLAB_TOKEN GITLAB_PROJECT_ID=42 python3 -m sentinel review tests/fixtures/risky.diff --mr-iid 7 --format json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({"dry_run":d["dry_run"],"mode":d["mode"],"score":d["score"],"level":d["level"],"recommendation":d["recommendation"],"actions":[{k:a[k] for k in ("kind","method","url","dry_run")} for a in d["actions"]]}, indent=2))'
{
  "dry_run": true,
  "mode": "offline-local-reasoner",
  "score": 100,
  "level": "critical",
  "recommendation": "revert",
  "actions": [
    {
      "kind": "comment_mr",
      "method": "POST",
      "url": "https://gitlab.com/api/v4/projects/42/merge_requests/7/notes",
      "dry_run": true
    },
    {
      "kind": "create_issue",
      "method": "POST",
      "url": "https://gitlab.com/api/v4/projects/42/issues",
      "dry_run": true
    }
  ]
}
```

`dry_run: true` and `status: null` in the full payload show no request was made.

## Live mode without token — fail closed

```
$ env -u GITLAB_TOKEN GITLAB_PROJECT_ID=42 python3 -m sentinel review tests/fixtures/risky.diff --live --mr-iid 7
error: GITLAB_TOKEN is not set. Live mode needs a project access token with 'api' scope. Re-run with --dry-run (the default) to preview actions.
exit=2
```

## What was not run

- No live GitLab API call: no token was available in this environment. The live
  transport is exercised only by the mocked test
  `test_live_dispatch_uses_mocked_transport`.
- No live Duo call: `DUO_AGENT_ENDPOINT` was not configured. The live request
  shape and response parsing are covered by
  `test_live_mode_posts_and_parses_response` with a mocked `urlopen`.
- No `docker build` and no full repository test suite, per the build rules.
