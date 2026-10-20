# PostCode Sentinel

Post-merge risk review for GitLab. It reads a unified diff, applies a fixed set
of deterministic rules, produces a 0–100 risk score with a reason per rule, and
asks a GitLab Duo Agent Platform flow for the decision (recommendation +
rationale). GitLab actions (MR comment, issue) are dry-run by default.

The core path is stdlib only. The Duo call is a real HTTP call to the Duo
workflow API and needs a token; the local deterministic reasoner is available
only when you ask for it with `--offline`, and its output is labelled `offline`.

## Requirements

- Python 3.11+ (tested on 3.11.15; CI image is `alpine:3.20`)
- `pytest` for the test suite
- `PyYAML` only for the static CI/Duo config tests (skipped if absent; installed
  in CI as `py3-yaml`)
- No third-party runtime dependencies

## Quick start

Run from the repository root (the directory containing `sentinel/`):

```bash
python3 -m pytest tests -q
python3 -m sentinel analyze tests/fixtures/risky.diff --offline --format json
python3 -m sentinel analyze tests/fixtures/clean.diff --offline --format md
python3 -m sentinel review tests/fixtures/risky.diff --offline --dry-run --mr-iid 7
```

`analyze` never touches GitLab. Without `--offline` it calls the Duo Agent
Platform workflow API and requires `GITLAB_DUO_TOKEN`; missing configuration is
an error, not a silent fallback. `review` defaults to `--dry-run`; it prints the
exact HTTP requests it *would* send to GitLab and sends nothing.

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
python3 -m sentinel review  <diff-file> [--dry-run|--live] [--offline] [--mr-iid N] [--issue]
                                        [--format json|md] [--gitlab-url URL] [--project-id ID]
```

Exit codes: `0` success, `2` unusable diff file, missing configuration, or a
failed Duo/GitLab call. Errors are printed to stderr without a traceback.

## GitLab Duo Agent Platform

Configuration lives in the repository, next to the code:

| Path | What it is |
|---|---|
| `.gitlab/duo/flows/postcode-sentinel.yml` | Custom flow in Flow Registry v1 format (`version`, `environment: ambient`, `components`, `prompts`, `routers`, `flow`) |
| `.gitlab/duo/chat-rules.md` | Project-level custom rules, read automatically by Duo Chat, agents and flows |
| `.gitlab/duo/agent.md` | Copy-paste registration data for the custom agent (GitLab has no repository file format for custom agents; they are created in AI > Agents or the AI Catalog) |
| `.agents/agents/postcode-sentinel-reviewer.md` | Workspace subagent (Markdown + YAML front matter) for the Developer Flow |

`sentinel/duo.py` posts to the Duo workflow API on the GitLab instance:

- `GITLAB_DUO_TOKEN` — **required** for live calls; without it the CLI exits 2
  with an explanation and points to `--offline`.
- `GITLAB_PROJECT_ID` — required for live calls (`CI_PROJECT_ID` is used
  automatically in GitLab CI).
- `GITLAB_DUO_ENDPOINT` — optional override; default is
  `{GITLAB_URL}/api/v4/ai/duo_workflows/workflows` (`GITLAB_URL` defaults to
  `https://gitlab.com`).

The request names the flow (`postcode-sentinel`, schema `v1`, version `1.0.0`),
sends the review context as the goal, and expects a JSON decision
(`recommendation`, `rationale`). A non-2xx, non-JSON or unreachable endpoint
raises `DuoError`; a response without a valid recommendation is an error too.

To activate the flow on a GitLab instance (requires GitLab 18.8+ / 19.2+ and
Duo Agent Platform enabled for the project):

1. Create the flow in **AI > Flows > New flow** (or the AI Catalog) and paste
   the YAML from `.gitlab/duo/flows/postcode-sentinel.yml`.
2. Enable it for the project and add a trigger (for example, a pipeline event).
3. Create the agent from `.gitlab/duo/agent.md` if you want it in Chat.

Sources used for the format (accessed 2026-10-20):
<https://docs.gitlab.com/user/duo_agent_platform/>,
<https://docs.gitlab.com/user/duo_agent_platform/flows/custom/>,
<https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/>,
<https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md>,
<https://docs.gitlab.com/user/duo_agent_platform/customize/custom_rules/>,
<https://docs.gitlab.com/user/duo_agent_platform/customize/workspace_subagents/>.

## GitLab adapter

Environment variables (never hardcode them):

- `GITLAB_URL` — default `https://gitlab.com`
- `GITLAB_PROJECT_ID` — numeric id or URL-encoded path; required for live
- `GITLAB_TOKEN` — project access token with `api` scope; required for live

Live mode without `GITLAB_TOKEN` exits with code 2 and a message pointing back to
`--dry-run`. `review` with `--mr-iid` posts an MR note; a score ≥ 55 (or
`--issue`) also opens an issue labelled `postcode-sentinel`.

## CI

`.gitlab-ci.yml` uses `alpine:3.20` and installs `git`, `python3`, `py3-pytest`
and `py3-yaml` with `apk add`, so the diff commands actually run (the previous
`python:3.12-slim` image had no `git`). Two jobs run on merge request pipelines
and on pushes to the default branch:

- `unit-tests` — `python3 -m pytest tests -q`.
- `postcode-sentinel` — builds `merged.diff` from
  `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD` for merge requests, or
  from `$CI_COMMIT_BEFORE_SHA..$CI_COMMIT_SHA` for branch pipelines (fallback
  `HEAD~1..HEAD`), then runs `review --offline --dry-run` and `analyze --offline`
  and stores `merged.diff`, `sentinel-report.md`, `sentinel-report.json` as
  artifacts.

CI deliberately runs `--offline`: no Duo or GitLab token is exposed to merge
request pipelines. The live Duo path is exercised by unit tests with a mocked
transport.

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
- **No live Duo call has been executed**: no GitLab Duo entitlement or token was
  available in this environment. The request shape and response parsing are
  covered by mocked-transport tests, and the flow config is validated
  statically against the documented v1 schema. Registering the flow and running
  it end to end requires a GitLab project with Duo Agent Platform enabled.
- The repository currently lives on GitHub; GitLab CI and the Duo Agent
  Platform only run after the project is pushed to a GitLab instance.
- `python3 -m sentinel` must run from the project root (or with it on
  `PYTHONPATH`).
