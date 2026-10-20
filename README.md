# PostCode Sentinel

Post-merge risk review for GitLab. It reads a unified diff, applies a fixed set
of deterministic rules, produces a 0–100 risk score with a reason per rule, and
proposes GitLab actions (MR comment, issue) — dry-run by default.

No external services are needed for the core path: stdlib only, offline Duo
reasoner, and no API calls unless you explicitly pass `--live` with a token.

## Requirements

- Python 3.11+ (tested on 3.11.15; CI image is `python:3.12-slim`)
- `pytest` for the test suite
- No third-party runtime dependencies

## Quick start

```bash
cd project
python3 -m pytest tests -q
python3 -m sentinel analyze tests/fixtures/risky.diff --format json
python3 -m sentinel analyze tests/fixtures/clean.diff --format md
python3 -m sentinel review tests/fixtures/risky.diff --dry-run --mr-iid 7
```

`analyze` never touches the network. `review` defaults to `--dry-run`; it prints
the exact HTTP requests it *would* send and sends nothing.

## Rules and weights

A rule contributes its weight at most once per diff, so the score reflects the
number of distinct risk classes, capped at 100. Every rule emits a reason and
evidence lines from the diff.

| Rule | Severity | Weight | Fires when |
|---|---|---:|---|
| `secret-in-diff` | critical | 40 | AWS key, private key block, GitLab/GitHub/Slack token, JWT, or `api_key/password/...` assigned a literal in added lines |
| `destructive-sql` | critical | 30 | `DROP TABLE/COLUMN`, `TRUNCATE TABLE`, `DELETE FROM ...;`, `ALTER ... DROP` added |
| `removed-tests` | high | 25 | test file deleted, or more test cases removed than added |
| `migration-change` | high | 20 | migration/schema path or `.sql` file touched |
| `auth-change` | high | 20 | auth module touched, or weakening patterns (`verify=False`, `skip_auth`, `csrf_exempt`, ...) added |
| `logic-without-tests` | medium | 20 | ≥5 changed lines in a source file and no test file in the same diff |
| `deleted-source` | medium | 15 | non-test, non-doc file deleted |
| `ci-config-change` | medium | 10 | `.gitlab-ci.yml`, `.github/workflows`, `Jenkinsfile`, `.circleci` changed |
| `dependency-change` | low | 10 | `requirements*.txt`, `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, `pom.xml` changed |
| `binary-change` | low | 10 | binary blob changed |
| `large-change` | low | 10 | more than 400 changed lines |
| `debug-leftovers` | low | 5 | added `print(`, `console.log(`, `debugger`, `TODO`, `FIXME`, `XXX` |

Levels: `low` < 30, `moderate` ≥ 30, `high` ≥ 55, `critical` ≥ 75.

## CLI

```
python3 -m sentinel analyze <diff-file> --format json|md [--offline] [--duo-endpoint URL]
python3 -m sentinel review  <diff-file> [--dry-run|--live] [--mr-iid N] [--issue]
                                        [--format json|md] [--gitlab-url URL] [--project-id ID]
```

`analyze` always runs the Duo decision step. Without `DUO_AGENT_ENDPOINT` (or with
`--offline`) it uses the local deterministic reasoner and labels the output
`offline-local-reasoner` in both JSON and Markdown.

## GitLab adapter

Environment variables (never hardcode them):

- `GITLAB_URL` — default `https://gitlab.com`
- `GITLAB_PROJECT_ID` — numeric id or URL-encoded path; required for live
- `GITLAB_TOKEN` — project access token with `api` scope; required for live

Live mode without `GITLAB_TOKEN` exits with code 2 and a message pointing back to
`--dry-run`. `review` with `--mr-iid` posts an MR note; a score ≥ 55 (or
`--issue`) also opens an issue labelled `postcode-sentinel`.

## Duo adapter

- `DUO_AGENT_ENDPOINT` — POST target for the live Duo agent
- `DUO_AGENT_TOKEN` — optional bearer token for that endpoint

The request body carries task, score, level, findings and file list. A non-2xx,
non-JSON or unreachable endpoint raises `DuoError` instead of silently pretending
a decision was made.

## CI

`.gitlab-ci.yml` defines the `postcode-sentinel` job (image `python:3.12-slim`).
It builds `merged.diff` from the MR target branch (fallback `HEAD~1`), runs the
unit tests, then `review --dry-run` and stores `merged.diff` + the report as
artifacts.

## Limitations

- Rules are regex/heuristic over diff text, not an AST or data-flow analysis.
  They are intentionally conservative and can produce false positives.
- The score saturates at 100; it is a triage signal, not a probability.
- Secret detection covers common token shapes only; it is not a replacement for
  a dedicated history scanner.
- `logic-without-tests` is diff-local: a test changed elsewhere in the repo does
  not count, and it is suppressed only when a non-deleted test file appears in
  the same diff.
- Rename detection relies on `rename from/to` headers; pure content moves look
  like delete + add.
- Live GitLab and live Duo paths are covered by unit tests with mocked
  transport; the build environment had no token, so no real API call was made.
- `python3 -m sentinel` must run from the project root (or with it on
  `PYTHONPATH`).
