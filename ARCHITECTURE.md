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
      │        ├─ offline: deterministic reasoner (labeled offline-local-reasoner)
      │        └─ live:    POST DUO_AGENT_ENDPOINT
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
| `sentinel/duo.py` | Duo endpoint adapter + offline reasoner with explicit mode label |
| `sentinel/cli.py` | `analyze` / `review` commands, exit codes, error mapping |

## Decisions and alternatives

**Deterministic rule engine instead of an LLM call for the score.** The score
must be reproducible in CI and auditable after the fact; an LLM would make
`score == 100` on the same diff non-deterministic. Duo is the *decision layer*
(recommendation + rationale), not the scoring layer. Alternative rejected: ask
Duo for the numeric score directly — untestable and non-reproducible.

**Per-rule contribution counted once.** Summing every finding would let a large
diff inflate the score just by touching many files. Distinct risk classes are a
better proxy for review effort. Alternative rejected: per-file weights — noisy
and easy to game.

**Dry-run by default, live requires explicit token.** The post-merge agent runs
in CI where credentials may be absent; the safe default must never mutate the
project. Alternative rejected: default live with a `--dry-run` opt-out.

**Offline reasoner labeled in-band.** When no Duo endpoint is configured, output
carries `mode: offline-local-reasoner` and a note, so demos and CI logs cannot be
mistaken for a real Duo decision.

**stdlib only.** CI runs on `python:3.12-slim` with no dependency install beyond
pytest; `urllib.request` is sufficient for two POST endpoints and keeps the
supply-chain surface at zero.

**Frozen dataclasses.** Rules and renderers cannot mutate parse results, which
makes the determinism test (same diff → identical dicts) meaningful.

## Extension points

- Add a rule: append a `Rule(...)` to `RULES` in `sentinel/rules.py`; it is picked
  up by scoring, reports and tests automatically.
- Add a GitLab action: add a method on `GitLabClient` returning `Action`; dry-run
  behavior comes for free through `_dispatch`.
- Replace the reasoner: implement `decide(result) -> dict` and inject it into the
  CLI; offline/live mode labels remain the contract.

## Failure behavior

- Unparseable input → `DiffParseError`, CLI exit 2.
- Missing diff file → `FileNotFoundError`, CLI exit 2.
- Live GitLab without token/project → `GitLabError`, CLI exit 2, no request sent.
- Live Duo non-2xx / non-JSON / unreachable → `DuoError`, CLI exit 2.
No path returns success without having done the work it reports.
