# Architecture

## Data flow

```
merged.diff (file / CI artifact)
      │
      ▼
diff_parser.parse_unified_diff ──► ChangeSet(DiffFile[], Hunk[])
      │                               paths, hunk headers, added/removed lines
      ▼
rules.evaluate ──► Finding[]         12 fixed rules, each with weight + reason
      │
      ▼
scoring.analyze_changeset ──► ReviewResult(score 0–100, level, findings, summary)
      │
      ├──► report.render_json / render_markdown
      │
      ├──► duo.DuoAgentClient.decide
      │        ├─ offline (explicit): deterministic reasoner, mode `offline`
      │        └─ live:    POST GitLab Duo workflow API with GITLAB_DUO_TOKEN
      │
      ▼
gitlab_client.GitLabClient
      ├─ dry-run (default): records Action objects, no socket
      └─ live: PRIVATE-TOKEN POST → MR note / issue
```

## Modules

| Module | Responsibility |
|---|---|
| `sentinel/models.py` | Frozen dataclasses: `Hunk`, `DiffFile`, `ChangeSet`, `Finding`, `ReviewResult`; symbol extraction |
| `sentinel/diff_parser.py` | Tolerant unified-diff parser (git headers, `/dev/null`, renames, binary markers, plain `---/+++`) |
| `sentinel/rules.py` | Rule registry with fixed order and weights; pure functions over `ChangeSet` |
| `sentinel/scoring.py` | Per-rule-class summation, 100 cap, level thresholds, summary |
| `sentinel/report.py` | Markdown/JSON rendering and action suggestions driven by fired rules |
| `sentinel/gitlab_client.py` | stdlib HTTP adapter, dry-run action journal, explicit config errors |
| `sentinel/duo.py` | Duo Agent Platform workflow API adapter + explicit offline reasoner |
| `sentinel/cli.py` | `analyze` / `review` commands, exit codes, error mapping |

## GitLab Duo Agent Platform integration

The project ships the Duo configuration as code, plus an adapter that calls the
Duo workflow API.

### Configuration files

| Path | Format | How GitLab uses it |
|---|---|---|
| `.gitlab/duo/flows/postcode-sentinel.yml` | Flow Registry v1 (`version: "v1"`, `environment: ambient`, `components`, `prompts`, `routers`, `flow`) | Source to create a **custom flow** in AI > Flows or the AI Catalog. GitLab does not auto-load flow YAML from the repository. |
| `.gitlab/duo/chat-rules.md` | Markdown | Project-level custom rules; read automatically by Duo Chat, agents and flows. |
| `.gitlab/duo/agent.md` | Markdown (registration data) | Custom agents are created in AI > Agents / AI Catalog and have no repository file format; this file versions the fields to paste. |
| `.agents/agents/postcode-sentinel-reviewer.md` | Markdown + YAML front matter | Workspace subagent for the Developer Flow (experimental feature). |

The flow has one `AgentComponent` (`postcode_sentinel`) with the tools
`get_repository_file`, `get_repository_files` and `list_repository_tree`, an
inline prompt (`prompt_id: postcode_sentinel_review`), a router to `end`, and
`environment: ambient` (the only environment custom flows support). The custom
flow schema restricts `response_schema_id`, `response_schema_version`,
`ui_role_as`, prompt `model`, and top-level `name`/`description`/`product_group`;
the file avoids all of them.

### Live call contract

`sentinel/duo.py` resolves the endpoint as `GITLAB_DUO_ENDPOINT`, or
`{GITLAB_URL}/api/v4/ai/duo_workflows/workflows` (default
`https://gitlab.com/api/v4/ai/duo_workflows/workflows`). The POST body names
the flow and carries the review context:

```json
{
  "project_id": 42,
  "flow_config_id": "postcode-sentinel",
  "flow_config_schema_version": "v1",
  "flow_version": "1.0.0",
  "environment": "ambient",
  "goal": "<instructions + JSON review context>",
  "context": {"score": 100, "level": "critical", "findings": [], "files": []}
}
```

Authentication is `Authorization: Bearer $GITLAB_DUO_TOKEN`. A response must
contain a valid `recommendation` (`approve`, `comment_and_review`,
`block_and_followup`, `revert`) either at the top level or in
`result`/`output`/`final_answer`; the rationale is optional. Missing token,
missing project id, HTTP error, unreachable endpoint, non-JSON body and missing
recommendation all raise `DuoError` (CLI exit 2).

### Mode labels

- `offline` — printed only when `--offline` / `offline=True` was requested. No
  socket is opened (`test_offline_mode_never_calls_transport`).
- `live-duo-workflow` — a real HTTP POST was attempted and parsed.

There is no automatic downgrade from live to offline: a missing token is an
error, not a fallback.

### Sources used for the format (accessed 2026-10-20)

- <https://docs.gitlab.com/user/duo_agent_platform/>
- <https://docs.gitlab.com/user/duo_agent_platform/flows/custom/>
- <https://docs.gitlab.com/user/duo_agent_platform/flows/custom_flows_schema/>
- <https://gitlab.com/gitlab-org/modelops/applied-ml/code-suggestions/ai-assist/-/blob/main/docs/flow_registry/v1.md>
- <https://docs.gitlab.com/user/duo_agent_platform/customize/custom_rules/>
- <https://docs.gitlab.com/user/duo_agent_platform/customize/workspace_subagents/>

### Verification status

Verified locally: the flow YAML parses and satisfies the documented v1
structure and restricted-field rules (static test); the adapter's live request
shape, auth header, payload and response parsing are covered with an injected
transport; offline mode is covered end to end.

Not verified: a real Duo workflow call and an end-to-end flow run. Both need a
GitLab project with Duo Agent Platform enabled (Premium/Ultimate or Duo Core
credits), the flow registered in AI > Flows, and a token. No such account or
token was available here, so EVIDENCE does not claim a live run.

## CI design

`.gitlab-ci.yml` uses `alpine:3.20` + `apk add git python3 py3-pytest py3-yaml`.
The previous `python:3.12-slim` image had no `git`, so every `git` command
failed. Jobs run on merge request pipelines and on pushes to the default branch
only (no duplicate branch pipelines for merge requests):

- `unit-tests` — `python3 -m pytest tests -q`.
- `postcode-sentinel` — `GIT_DEPTH: "0"` so a merge base exists; builds the diff
  from `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...HEAD` on merge requests or
  `$CI_COMMIT_BEFORE_SHA..$CI_COMMIT_SHA` on branch pipelines, falling back to
  `HEAD~1..HEAD` when a new branch has no before-SHA. It then runs
  `review --offline --dry-run` and `analyze --offline`, writing
  `merged.diff`, `sentinel-report.md`, `sentinel-report.json` as artifacts.

CI uses `--offline` on purpose: no Duo or GitLab token is exposed to merge
request pipelines. Output redirection is used instead of `| tee`, so a failing
command cannot be masked by a pipe.

## Decisions and alternatives

**Deterministic rule engine instead of an LLM call for the score.** The score
must be reproducible in CI and auditable after the fact; an LLM would make
`score == 100` on the same diff non-deterministic. Duo is the *decision layer*
(recommendation + rationale), not the scoring layer. Alternative rejected: ask
Duo for the numeric score directly — untestable and non-reproducible.

**Duo configuration in the repository.** The flow YAML is versioned with the
code and validated statically, so the prompt, tools and routing are reviewable
in the same merge request as the rules. Alternative rejected: configure the flow
only in the GitLab UI, where changes are invisible to code review.

**A real Duo workflow call instead of a generic webhook.** Live mode targets the
GitLab Duo workflow API and names the flow (`postcode-sentinel/v1`); the token
is GitLab-scoped (`GITLAB_DUO_TOKEN`). Alternative rejected: posting to an
arbitrary `DUO_AGENT_ENDPOINT`, which the CRITIQUE correctly called a prop.

**Offline must be requested.** A missing token raises instead of silently
switching to the local reasoner, so a demo or CI log can never be mistaken for a
real Duo decision. Alternative rejected: auto-offline with a label — still
hides misconfiguration.

**Per-rule contribution counted once.** Summing every finding would let a large
diff inflate the score just by touching many files. Distinct risk classes are a
better proxy for review effort. Alternative rejected: per-file weights — noisy
and easy to game.

**Dry-run by default, live requires explicit token.** The post-merge agent runs
in CI where credentials may be absent; the safe default must never mutate the
project. Alternative rejected: default live with a `--dry-run` opt-out.

**stdlib only.** Runtime code has no third-party dependency; `urllib.request` is
sufficient for two POST endpoints and keeps the supply-chain surface at zero.
PyYAML is test-only.

**Frozen dataclasses.** Rules and renderers cannot mutate parse results, which
makes the determinism test (same diff → identical dicts) meaningful.

## Extension points

- Add a rule: append a `Rule(...)` to `RULES` in `sentinel/rules.py`; it is picked
  up by scoring, reports and tests automatically.
- Add a GitLab action: add a method on `GitLabClient` returning `Action`; dry-run
  behavior comes for free through `_dispatch`.
- Replace the reasoner: implement `decide(result) -> dict` and inject it into the
  CLI; mode labels remain the contract.
- Change the flow: edit `.gitlab/duo/flows/postcode-sentinel.yml` and re-register
  it in GitLab (GitLab copies the YAML into the AI Catalog).

## Failure behavior

- Unparseable input → `DiffParseError`, CLI exit 2.
- Missing diff file → `FileNotFoundError`, CLI exit 2.
- Live Duo without `GITLAB_DUO_TOKEN` / `GITLAB_PROJECT_ID` → `DuoError`,
  CLI exit 2, no request sent.
- Live Duo non-2xx / non-JSON / unreachable / no recommendation → `DuoError`,
  CLI exit 2.
- Live GitLab without token/project → `GitLabError`, CLI exit 2, no request sent.
No path returns success without having done the work it reports.

## Etapy pipeline'u
1. `test` — 60 testów jednostkowych i kontraktowych.
2. `quality` — osiem bramek repozytorium (licencja, dokumentacja, sekrety, uczciwe twierdzenia,
   dostępność, responsywność); raport Code Quality dla GitLaba.
3. `postcode` — analiza diffu i raport ryzyka (dry-run).

Bramki są niezależne od analizatora: mogą zatrzymać merge, zanim Sentinel w ogóle policzy ryzyko.
