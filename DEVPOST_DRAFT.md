# PostCode Sentinel

**Post-merge risk review agent for GitLab, built on the GitLab Duo Agent Platform.**

## Elevator pitch

Every merge is a small bet that nothing broke. PostCode Sentinel reads the diff
after the merge, scores it 0–100 against twelve deterministic risk rules, explains
every point, and asks a GitLab Duo Agent Platform flow for the decision —
comment, block, or revert. GitLab API calls stay in dry-run until a token is
explicitly provided.

## Inspiration

Code review stops at the merge button. Post-code incidents — a dropped table, a
deleted test suite, a token pasted into `settings.py` — are usually visible in the
diff itself, but nobody rereads the diff after CI turns green. We wanted an agent
that runs *after* the merge, when the diff is settled, and answers one question:
"what did we just let in?" The Life After Code brief asks exactly for automation
in the post-code phase: analysis, decisions, actions.

## What it does

1. Parses a unified diff (git-style or plain) into files, hunks, symbols and
   added/removed lines.
2. Applies twelve deterministic rules: secrets in the diff, destructive SQL,
   removed tests, schema migrations, auth surface changes, logic without tests,
   deleted source, CI config, dependency manifests, binary blobs, large
   changesets, debug leftovers.
3. Produces a 0–100 score with a reason and evidence lines per rule, capped at
   100 and counted once per risk class.
4. Calls the GitLab Duo Agent Platform workflow API with the review context. The
   flow (`postcode-sentinel`, Flow Registry v1) returns a recommendation
   (`approve`, `comment_and_review`, `block_and_followup`, `revert`) plus a
   rationale. A live call needs `GITLAB_DUO_TOKEN`; without it the CLI exits 2.
   The local deterministic reasoner is available only with `--offline` and is
   labelled `offline` in every output.
5. In `review`, prepares GitLab actions: an MR comment and (score ≥ 55) an issue
   labelled `postcode-sentinel`. Default is `--dry-run`; `--live` needs
   `GITLAB_TOKEN` and fails with a readable error without it.
6. Ships a `.gitlab-ci.yml` that installs `git` in an `alpine:3.20` image, builds
   the diff for merge requests (`origin/<target>...HEAD`) or branch pipelines
   (`CI_COMMIT_BEFORE_SHA..CI_COMMIT_SHA`), runs the tests and the offline
   report, and stores the diff plus reports as artifacts.

## How we built it

- Python 3.11+ stdlib only for runtime code; `pytest` for tests.
- Parser is a small state machine over lines: `diff --git`, `new/deleted file
  mode`, `rename from/to`, `---`/`+++`, `@@` hunks, binary markers. Malformed
  hunks are skipped instead of aborting the review.
- Rules are pure functions over frozen dataclasses, registered in a fixed order.
  Scoring sums rule-class weights once and caps at 100, so the score is
  reproducible byte-for-byte.
- Duo configuration is versioned in the repository: the flow in
  `.gitlab/duo/flows/postcode-sentinel.yml` (v1 schema, one `AgentComponent`,
  inline prompt, repository-read tools), project custom rules in
  `.gitlab/duo/chat-rules.md`, the agent registration data in
  `.gitlab/duo/agent.md`, and a workspace subagent in
  `.agents/agents/postcode-sentinel-reviewer.md`.
- The Duo adapter posts to
  `{GITLAB_URL}/api/v4/ai/duo_workflows/workflows` with
  `Authorization: Bearer $GITLAB_DUO_TOKEN`, names the flow/schema/version, and
  parses the decision from the response. Missing configuration, HTTP errors,
  non-JSON bodies and responses without a recommendation are typed errors.
- GitLab adapter uses `urllib.request` with `PRIVATE-TOKEN`; dry-run records
  `Action` objects and never opens a socket. Both adapters refuse a plaintext
  `http` URL (loopback excepted) and strip the token from error bodies.
- CI uses `alpine:3.20` + `apk add git python3 py3-pytest py3-yaml`; the earlier
  `python:3.12-slim` image had no `git`, so its `git diff` step could never run.

## Challenges

- **Determinism vs. intelligence.** An LLM score is not reproducible in CI. We
  split responsibilities: rules score, Duo decides and explains.
- **Duo config has no repository auto-load.** Custom flows are registered through
  AI > Flows or the AI Catalog; custom agents have no repository file format.
  We version the exact YAML/registration fields in `.gitlab/duo/` and validate
  them statically, instead of pretending GitLab reads them automatically.
- **No silent offline fallback.** A missing token is an error. The earlier
  behaviour (auto-offline) made a demo look like a Duo integration when it was
  the local reasoner.
- **CI image contents.** The first pipeline image shipped without `git`; the
  diff step failed before the tool ran. The image now installs git explicitly
  and a static test fails if that line disappears.
- **Safe defaults.** A post-merge bot with a token can spam a project. Dry-run is
  the default and live mode fails closed when configuration is missing.
- **Diff tolerance.** Real diffs include renames, `/dev/null`, binary patches and
  truncated hunks; the parser degrades gracefully instead of raising.

## Accomplishments

- 82 unit tests, including the required negative case: a clean diff with a test
  update scores 0 (< 20), while the risky fixture scores 100 with eight findings.
- The rule engine is measured: a seven-case labelled corpus scores it at precision 1.0 /
  recall 1.0 per rule, with an ablation test that fails if the metric stops reacting.
- Static tests parse `.gitlab-ci.yml` and the Duo flow and enforce the image's
  git installation, the MR/branch `rules:`, the diff ranges, the offline flags,
  and the Flow Registry v1 structure/restricted fields.
- Duo live paths (auth header, payload, response parsing) and failure paths are
  covered with an injected transport; offline mode is proven not to open a
  socket.
- The README quick start is executed by a test, so a documented command that no longer
  exists fails the suite instead of the reviewer's first click.
- The web inspector shows the recommended action (approve / comment / block / revert)
  from the same mapping the CLI uses, so a judge sees the decision, not only a score.
- Every score point traces to a finding with a file, reason and evidence.
- Zero runtime dependencies; CI needs only Alpine packages.

## What we learned

- Post-merge review is a different product from pre-merge linting: the diff is
  the source of truth and the useful output is an action, not a warning.
- Conservative regex rules catch the highest-cost mistakes (secrets, dropped
  tables, deleted tests) with very little code.
- Explicit mode labels (`offline` vs `live-duo-workflow`) prevent the most common
  demo failure: mistaking a mock for an integration.
- A CI job is only as real as its image: `git diff` needs `git`.
- Duo Agent Platform configuration is a UI/API object, not a file GitLab reads
  from the repo; the repository can still be the reviewed source of truth.

## What's next

- Register the flow in a GitLab AI Catalog and attach a pipeline-event trigger so
  the agent reacts to merges without CI calling it.
- Map recommendations to GitLab Duo autonomy levels so low-risk merges
  auto-approve and high-risk ones open a blocking issue.
- AST-aware "logic without tests" coverage using tree-sitter instead of line
  heuristics.
- Learn rule weights from historical reverts, keeping the score deterministic per
  configuration version.
- Move the repository from GitHub to GitLab so the pipeline and Duo integration
  run on the platform the project targets.

## Technologies

Python 3.11+ · stdlib (`urllib`, `json`, `argparse`, `dataclasses`, `re`) ·
pytest · PyYAML (tests only) · GitLab REST API v4 · GitLab Duo Agent Platform
(Flow Registry v1 flow, custom rules, workflow API adapter) · GitLab CI
(`alpine:3.20` + `git`)

## Links

- Repository (current): `https://github.com/aigon-x/postcode-sentinel`
- GitLab project: https://gitlab.com/szamaniai-group/postcode-sentinel; CI and Duo need a GitLab URL
- Demo video: `<VIDEO_URL_PLACEHOLDER>`

## Running it

```bash
cd postcode-sentinel
python3 -m pytest tests -q
python3 -m sentinel analyze tests/fixtures/risky.diff --offline --format json
python3 -m sentinel review tests/fixtures/risky.diff --offline --dry-run --mr-iid 7
```

Live Duo run (requires Duo Agent Platform enabled and the flow registered):

```bash
export GITLAB_URL="https://gitlab.com"
export GITLAB_PROJECT_ID="<numeric-id>"
export GITLAB_DUO_TOKEN="<token with Duo workflow access>"
python3 -m sentinel analyze merged.diff --format json
```

Registration steps for the flow: create it in **AI > Flows > New flow**, paste
`.gitlab/duo/flows/postcode-sentinel.yml`, enable it for the project and add a
trigger. Live GitLab action run (never required for the demo):

```bash
export GITLAB_TOKEN="<project-access-token>"   # api scope
python3 -m sentinel review merged.diff --offline --live --mr-iid 7
```


## Duo run
- Flow `PostCode Sentinel` registered in the AI Catalog and enabled on the project.
- Session [9763141](https://gitlab.com/szamaniai-group/postcode-sentinel/-/automate/agent-sessions/9763141) started by a mention and completed.
