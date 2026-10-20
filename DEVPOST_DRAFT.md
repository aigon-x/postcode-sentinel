# PostCode Sentinel

**Post-merge risk review agent for GitLab, built on the GitLab Duo Agent Platform pattern.**

## Elevator pitch

Every merge is a small bet that nothing broke. PostCode Sentinel reads the diff
after the merge, scores it 0–100 against twelve deterministic risk rules, explains
every point, and proposes the next action — comment, issue, or revert — with
GitLab API calls in dry-run until a token is explicitly provided.

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
4. Asks the Duo adapter for a recommendation (`approve`, `comment_and_review`,
   `block_and_followup`, `revert`). Without a configured endpoint it uses a local
   deterministic reasoner and says so in the output.
5. In `review`, prepares GitLab actions: an MR comment and (score ≥ 55) an issue
   labelled `postcode-sentinel`. Default is `--dry-run`; `--live` needs
   `GITLAB_TOKEN` and fails with a readable error without it.
6. Ships a `.gitlab-ci.yml` job that runs on MRs and the default branch and
   stores the diff plus report as artifacts.

## How we built it

- Python 3.11+ stdlib only for runtime code; `pytest` for tests.
- Parser is a small state machine over lines: `diff --git`, `new/deleted file
  mode`, `rename from/to`, `---`/`+++`, `@@` hunks, binary markers. Malformed
  hunks are skipped instead of aborting the review.
- Rules are pure functions over frozen dataclasses, registered in a fixed order.
  Scoring sums rule-class weights once and caps at 100, so the score is
  reproducible byte-for-byte.
- GitLab adapter uses `urllib.request` with `PRIVATE-TOKEN`; dry-run records
  `Action` objects and never opens a socket.
- Duo adapter posts score, level, findings and files; offline mode is an explicit
  mode string and note, not a silent fallback.
- CLI exposes `analyze` (json/md) and `review` (dry-run/live, MR note, issue).

## Challenges

- **Determinism vs. intelligence.** An LLM score is not reproducible in CI. We
  split responsibilities: rules score, Duo decides and explains.
- **Score inflation.** Summing every finding made a big diff look catastrophic.
  Counting each rule class once kept the score about risk diversity.
- **Safe defaults.** A post-merge bot with a token can spam a project. Dry-run is
  the default and live mode fails closed when configuration is missing.
- **Diff tolerance.** Real diffs include renames, `/dev/null`, binary patches and
  truncated hunks; the parser degrades gracefully instead of raising.

## Accomplishments

- 28 unit tests, including the required negative case: a clean diff with a test
  update scores 0 (< 20), while the risky fixture scores 100 with ten findings.
- Every score point traces to a finding with a file, reason and evidence.
- Zero runtime dependencies; the CI job needs only `pytest`.
- Live paths raise typed errors instead of pretending success.

## What we learned

- Post-merge review is a different product from pre-merge linting: the diff is
  the source of truth and the useful output is an action, not a warning.
- Conservative regex rules catch the highest-cost mistakes (secrets, dropped
  tables, deleted tests) with very little code.
- Explicit mode labels (`offline-local-reasoner` vs `live-duo-endpoint`) prevent
  the most common demo failure: mistaking a mock for an integration.

## What's next

- Map findings to GitLab Duo Agent Platform autonomy levels so low-risk merges
  auto-approve and high-risk ones open a blocking issue.
- AST-aware "logic without tests" coverage using tree-sitter instead of line
  heuristics.
- Learn rule weights from historical reverts, keeping the score deterministic per
  configuration version.
- GitLab webhook receiver so the agent reacts to merge events without CI.

## Technologies

Python 3.11+ · stdlib (`urllib`, `json`, `argparse`, `dataclasses`, `re`) ·
pytest · GitLab REST API v4 · GitLab Duo Agent Platform (adapter) · GitLab CI
(`python:3.12-slim`)

## Links

- Repository: `https://github.com/aigon-x/postcode-sentinel`
- Demo video: `<VIDEO_URL_PLACEHOLDER>`

## Running it

```bash
cd project
python3 -m pytest tests -q
python3 -m sentinel analyze tests/fixtures/risky.diff --format json
python3 -m sentinel review tests/fixtures/risky.diff --dry-run --mr-iid 7
```

Live GitLab run (never required for the demo):

```bash
export GITLAB_URL="https://gitlab.com"
export GITLAB_PROJECT_ID="<numeric-id>"
export GITLAB_TOKEN="<project-access-token>"   # api scope
python3 -m sentinel review merged.diff --live --mr-iid 7
```

Live Duo run:

```bash
export DUO_AGENT_ENDPOINT="https://<duo-agent-endpoint>"
export DUO_AGENT_TOKEN="<token-if-required>"
python3 -m sentinel analyze merged.diff --format json
```
