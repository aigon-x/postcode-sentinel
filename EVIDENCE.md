# Evidence

All commands run from the `project/` directory. Outputs below are copied from
the terminal, not retyped.

## Environment

```
$ date -Is
2026-10-20T12:42:57+02:00

$ python3 --version
Python 3.11.15
```

Runtime code uses the standard library only; `pytest 9.0.3` is the test runner
and `PyYAML 6.0.3` is used only by the static CI/Duo config tests. The web layer
adds `fastapi` and its `TestClient` (`httpx`) for the HTTP tests. The CI image
is `alpine:3.20` with `git`, `python3`, `py3-pytest` and `py3-yaml` installed
(see `.gitlab-ci.yml`).

## Test suite

```
$ python3 -m pytest -q -p no:warnings
........................................................................ [ 94%]
....                                                                     [100%]
74 passed in 1.02s
```

Re-run 2026-10-20 after adding `tests/test_docs.py`: `76 passed in 3.05s`.

`tests/test_docs.py` executes the README quick start as written (substituting
`sys.executable` for `python3`) and asserts every `python3 -m sentinel ...`
command in README, DEVPOST_DRAFT and this file resolves to a real subcommand or
module. It was added because the quick start line said `python3 -m sentinel eval`
while the entry point is `python3 -m sentinel.eval`; the first reviewer click
died with `invalid choice: 'eval'` (exit 2). Ablation: restoring the old line
turns both tests red.

Static CI and Duo configuration tests alone:

```
$ python3 -m pytest tests/test_ci_config.py -q
..........                                                               [100%]
10 passed in 0.05s
```

That file parses `.gitlab-ci.yml` and `.gitlab/duo/flows/postcode-sentinel.yml`
and asserts: the image is `alpine:3.20`; `apk add` includes `git`, `python3`,
`py3-pytest`, `py3-yaml`; jobs run on merge request pipelines and pushes to the
default branch; the diff uses
`origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD` or
`$CI_COMMIT_BEFORE_SHA..$CI_COMMIT_SHA` with a `HEAD~1..HEAD` fallback; no `tee`
pipe can mask a failure; the sentinel steps use `--offline`; the flow has the
required v1 keys and avoids restricted custom-flow fields; the Duo rules, agent
registration and workspace subagent files exist in their documented formats.

Covered areas: parser (git and plain diffs, added/deleted files, hunk symbols,
rejection of non-diffs), rules (secret, removed tests, migration, destructive
SQL, determinism, clean diff severity), scoring (risky ≥ 60, clean < 20, cap and
traceability, level thresholds), GitLab client (dry-run, missing token, missing
project id, invalid input, mocked live dispatch), Duo (explicit offline label,
no transport in offline mode, missing `GITLAB_DUO_TOKEN`, missing project id,
workflow POST payload and auth header, nested `result` JSON parsing, missing
recommendation, HTTP 401, unreachable, non-JSON, endpoint default), CLI (json/md
output, live Duo error mapping, dry-run action preview, live without token,
missing file), web layer (index HTML, health, risky/clean scoring over HTTP,
empty and unparsable diff rejection), CI/Duo config (static checks above).

Required negative case (clean diff must score below 20) still passes:

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

Observed score for `tests/fixtures/clean.diff` is `0`.

## Rule engine measured on a labelled corpus

```
$ python3 -m sentinel.eval
cases=7 rules=7 macro_precision=1.0 macro_recall=1.0
  auth-change            tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  ci-config-change       tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  debug-leftovers        tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  destructive-sql        tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  migration-change       tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  removed-tests          tp=1 fp=0 fn=0 precision=1.0 recall=1.0
  secret-in-diff         tp=1 fp=0 fn=0 precision=1.0 recall=1.0
OK
```

Seven labelled diffs — one per rule class plus a clean control — run through the same
engine. Synthetic: it shows each rule fires on its showcase pattern and the control stays
silent, not that the weights match a real repository. `tests/test_eval.py` keeps it as a regression test plus an ablation that
removes `secret-in-diff`.

## Risky fixture — score and reasons (offline)

```
$ python3 -m sentinel analyze tests/fixtures/risky.diff --offline --format json | ...
{
  "score": 100,
  "level": "critical",
  "mode": "offline",
  "duo_mode": "offline",
  "duo_recommendation": "revert"
}
```

The Markdown report lists eight findings (secret-in-diff, destructive-sql,
removed-tests, migration-change, auth-change, logic-without-tests,
ci-config-change, debug-leftovers) with a file, weight and reason per row, and
the heading `**Mode:** \`offline\``.

## Live Duo without a token — fail closed

```
$ env -u GITLAB_DUO_TOKEN GITLAB_PROJECT_ID=42 python3 -m sentinel analyze tests/fixtures/clean.diff --format json
error: GITLAB_DUO_TOKEN is not set. A live Duo Agent Platform call needs a GitLab token with access to the project's Duo workflow API. Set GITLAB_DUO_TOKEN, or run with --offline to use the local deterministic reasoner.
exit=2
```

No request is built in this path; the missing-token check runs before the HTTP
call. `test_live_mode_without_token_fails_clearly` asserts the same message and
that `--offline` is offered.

## Transport refuses to put the token on the wire in clear text

```
$ python3 -c "from sentinel.gitlab_client import GitLabClient; GitLabClient(base_url='http://gitlab.example.com', project_id='42')"
GitLabError: GitLab base URL points at plaintext http on 'gitlab.example.com'; the token
would travel in clear text. Use https, or point the URL at localhost for a local GitLab.
```
The same check runs on the Duo endpoint and raises `DuoError` with that message.

Both adapters validate the URL before a request is built: https only, with
loopback `http` allowed for a local GitLab in development; URLs carrying
`user:password@` are refused, because those credentials land in logs and in the
`Action` trace; a token echoed back in an API error body is replaced with
`[redacted]`. `tests/test_transport_security.py` holds eight tests, including the
negative cases above.

## Dry-run review — no network

```
$ env -u GITLAB_TOKEN GITLAB_PROJECT_ID=42 python3 -m sentinel review tests/fixtures/risky.diff --offline --mr-iid 7 --format json | ...
{
  "dry_run": true,
  "mode": "offline",
  "score": 100,
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

## CI diff step — executed locally with `sh`

The exact shell block from `.gitlab-ci.yml` was run in the repository with no CI
variables set, so it took the branch-pipeline fallback:

```
$ sh -c '<script block from .gitlab-ci.yml>'
diff range: HEAD~1..HEAD
diff bytes: 5420

$ python3 -m sentinel review /tmp/merged.diff --offline --dry-run --format md | head -6
## PostCode Sentinel — post-merge risk review

**Risk score:** `0/100` (low)
**Mode:** `offline`
**Changeset:** 2 file(s), +71/-1 lines
```

This proves the diff-building and report commands work with a POSIX shell and a
real git checkout. The Alpine image itself (`apk add git ...`) could not be
executed here: no Docker build/run was performed under the build rules.

## Duo configuration files

```
$ find .gitlab .agents -type f
.gitlab/duo/chat-rules.md
.gitlab/duo/agent.md
.gitlab/duo/flows/postcode-sentinel.yml
.agents/agents/postcode-sentinel-reviewer.md
```

```
$ python3 -c 'import yaml; d=yaml.safe_load(open(".gitlab/duo/flows/postcode-sentinel.yml")); print(sorted(d)); print("entry:", d["flow"]["entry_point"], "prompts:", [p["prompt_id"] for p in d["prompts"]])'
['components', 'environment', 'flow', 'prompts', 'routers', 'version']
entry: postcode_sentinel prompts: ['postcode_sentinel_review']
```

## Web UI (FastAPI)

The browser layer reuses the existing analyzer unchanged: `web/server.py` parses
the pasted diff with `sentinel.diff_parser`, scores it with
`sentinel.scoring.analyze_changeset` and renders the report with
`sentinel.report.render_markdown`. No rule, weight or threshold was modified.

Endpoints:

| Method | Path | Behaviour |
|---|---|---|
| GET | `/` | serves `web/index.html` (dark UI, accent `#4c82d9`) |
| GET | `/api/health` | `{"status":"ok","service":"postcode-sentinel-web","version":"0.1.0"}` |
| POST | `/api/analyze` | body `{"diff":"..."}` → `score`, `level`, `findings`, `recommendation`, `rationale`, `decision_mode`, `markdown`; empty or unparsable diff → `400` |

The recommended action is not computed in the web layer: `sentinel.duo.offline_decision` maps
the score to `approve` (<30), `comment_and_review` (≥30), `block_and_followup` (≥55) or
`revert` (≥75), exactly as `review --offline` does. The page shows it as a badge next to the
level, with the rationale underneath, and the exported markdown carries the same decision
block.

The request size is bounded. `POST /api/analyze` requires `Content-Length` (`411` without
it) and refuses anything above 4 MB with `413` before FastAPI parses the body; the model
caps `diff` at 4 MiB characters as a second layer. Measured on one uvicorn worker with an
8.6 MB (8.3 MiB) JSON body, 200 000 added lines:

```
before the cap: 200 OK in 7.25 s, worker peak RSS 52 -> 110 MB
after the cap:  413 in 0.26 s, peak RSS unchanged (89.9 MB)
```

The rejected body is drained (up to 16 MB) before the response, so a client reads the error
body instead of a connection reset. Regression:
`tests/test_web.py::test_oversized_diff_is_refused_before_the_analyzer` (the analyzer is
monkeypatched to raise; it must never be called), `::test_content_length_policy`,
`::test_model_also_caps_the_diff`, plus `::test_small_diff_still_works` to prove the cap did
not break the normal path. Ablation: skipping `_body_rejection` turns the first test red.

```
$ python3 -m pytest -q -p no:warnings tests/test_web.py
..............                                                           [100%]
14 passed in 1.02s
```

Browser check (Chromium 155, 1440×1000 and 390×844) against `python3 -m uvicorn web.server:app`
with `tests/fixtures/risky.diff` pasted into the textarea: score **100**, level badge
`CRITICAL`, action badge `REVERT`, rationale line "Score 100/100 (critical). Rules fired:
auth-change, ci-config-change, …", status line "… recommended action revert.", the raw
markdown contains `recommendation: **revert**`, 0 console errors. Frames:
`evidence/webui-decision-desktop.png`, `evidence/webui-decision-mobile.png`.

The same run measured a pre-existing responsive defect: at 390 px the five-column findings
table pushed the page to 498 px. The table now sits in a `.table-scroll` container, so the
page stays at 390 px and the table itself scrolls (`[390, 390]` after the change).

Earlier revision of this section (8 tests) is kept below for the record.

Measured response for `tests/fixtures/risky.diff`:

```
$ python3 -c "from fastapi.testclient import TestClient; from web.server import app; \
  print(TestClient(app).post('/api/analyze', json={'diff': open('tests/fixtures/risky.diff').read()}).json()['score'])"
100
```

```
$ python3 -c "from fastapi.testclient import TestClient; from web.server import app; \
  print(TestClient(app).post('/api/analyze', json={'diff': '   '}).status_code)"
400
```

```
$ python3 -c "from fastapi.testclient import TestClient; from web.server import app; \
  print(TestClient(app).get('/api/health').json())"
{'status': 'ok', 'service': 'postcode-sentinel-web', 'version': '0.1.0'}
```

Colour thresholds in the UI mirror `sentinel.scoring.LEVELS` (`critical` ≥75,
`high` ≥55, `medium` ≥30, `low` <30; the engine names the third tier
`moderate`, the UI labels it `medium`). The page shows the score, a threshold
badge, a reasons table, the raw Markdown in `<details>`, and empty/loading/error
states announced through `aria-live`; `prefers-reduced-motion` disables
transitions and animations.

Run it locally:

```
$ uvicorn web.server:app --host 127.0.0.1 --port 8000
```

Not verified: the UI was not opened in a real browser (no screenshot, no console
check); only the `GET /` markup and the API contract are covered by tests.

## What was not run

- **No live Duo call.** No GitLab Duo entitlement or `GITLAB_DUO_TOKEN` was
  available in this environment. The live request shape, auth header, payload
  and response parsing are covered by tests with an injected transport
  (`test_live_mode_posts_duo_workflow_and_parses_response` and the failure-path
  tests). The flow has not been registered in a GitLab AI Catalog and has not
  been executed end to end.
- **No live GitLab API call.** No `GITLAB_TOKEN` was available. The live
  transport is exercised only by the mocked test
  `test_live_dispatch_uses_mocked_transport`.
- **No GitLab CI pipeline run.** The repository is still on GitHub
  (`https://github.com/aigon-x/postcode-sentinel`); GitLab CI only runs after
  the project is pushed to a GitLab instance. The YAML and its commands were
  validated statically and the diff step was executed locally with `sh`.
- **No `docker build` and no full repository test suite**, per the build rules.

## Quality gates w CI (2026-10-20)
```
$ python3 ci/quality_gates.py .
PASS license        LICENSE
PASS documentation  README.md
...
8/8 gates passed
```
Job `quality-gates` publikuje `quality-gates.json` oraz raport Code Quality (`codequality.json`).
