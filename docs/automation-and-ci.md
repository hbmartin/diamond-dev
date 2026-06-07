# Automation & CI integration

`diamond-dev` is built to run unattended. This guide covers the machine-readable
outputs and signals you wire into a pipeline: exit codes, the structured run
report, run statuses, and notification webhooks. For log formats and tracing, see
[Observability](observability.md).

## Exit codes

`main()` in [`main.py`](../diamond_dev/main.py) returns:

| Code | Meaning |
| ---- | ------- |
| `0`  | The command completed. For a workflow run this includes the `succeeded_with_warnings` status — see below. |
| `1`  | A `DiamondDevError` was raised (config error, preflight failure, plan drift, an agent failure, an existing PR on resume, etc.). The message is logged at `error` level. |
| `130`| Interrupted with Ctrl-C during the run (commonly while polling for wiki acceptance). The current run report is still written before exit. |

Key nuance for CI: **`succeeded_with_warnings` is a run *status*, not an exit
code.** A run that skips a best-effort phase (e.g. a notification or an optional
test command) still exits `0`. If you want CI to react to warnings, inspect the
run report's `status` and `phase_warnings` rather than the exit code.

`diamond-dev init` and `diamond-dev doctor` also return `0`/`1`; `doctor` runs the
same preflight checks used before a workflow and is the right gate to run first in
a pipeline.

## The run report

Every attempted run writes a deterministic JSON summary to **both**
`logs/run-report.json` and `logs/run.json` (identical payloads; `run.json` is a
stable alias). The writer is `write_run_report` in
[`report.py`](../diamond_dev/report.py). Keys are sorted and the file ends with a
trailing newline, so it is safe to diff and to parse with any JSON tool.

### Top-level shape

```jsonc
{
  "status": "succeeded",            // "succeeded" | "succeeded_with_warnings" | "failed"
  "started_at": "2026-06-07T...",   // ISO 8601
  "finished_at": "2026-06-07T...",
  "duration_seconds": 1234.567,
  "error": null,                    // string when status is "failed", else null
  "context": { ... },               // null until the run context is built
  "selected_implementation": { ... },// null until a branch is accepted
  "preflight": { ... },             // null until preflight runs
  "phase_timings": [ ... ],
  "phase_warnings": [ ... ],
  "command_logs": [ ... ]
}
```

Fields that depend on run progress are `null` when the run failed before reaching
them — always null-check `context`, `selected_implementation`, and `preflight`.

### `context`

```jsonc
"context": {
  "mode": "plan",                    // "plan" | "commit_pair"
  "cwd": "/abs/invocation/dir",
  "config_path": "/abs/.diamond-dev.toml",
  "plan_path": "/abs/my-plan.md",
  "commit_pair": null,               // populated only in two-commit mode (see below)
  "repository_url": "git@github.com:owner/repo.git",
  "wiki_repository_url": "git@github.com:owner/repo.wiki.git",
  "branches": { "base": "main", "codex": "codex/my-plan", "claude": "claude/my-plan" },
  "repositories": {
    "wiki": "/abs/repo.wiki",
    "codex": "/abs/codex-my-plan",
    "claude": "/abs/claude-my-plan"
  },
  "workflow_roles": {
    "implementers": ["codex", "claude"],
    "comparison_judge": "gemini",
    "comparison_fixer": null,        // null means "first non-selected implementer"
    "review_provider": "coderabbit",
    "review_judge": "codex",
    "review_fixer": "codex",
    "final_reviewer": "claude"
  },
  "artifacts": {
    "comparison": "/abs/repo.wiki/my-plan-comparison.md",
    "comparison_bundle": "/abs/repo.wiki/my-plan-comparison-bundle.md",
    "review": "/abs/repo.wiki/my-plan-review.md",
    "review_judgments": "/abs/repo.wiki/my-plan-review-judgments.json",
    "review_judgments_parse_status": { "status": "valid", "path": "...", "error": null },
    "pr_url": "https://github.com/owner/repo/pull/123"
  },
  "dirty_records": [
    { "label": "codex", "branch": "codex/my-plan", "files": ["path/left/dirty.txt"] }
  ]
}
```

`review_judgments_parse_status.status` is `missing`, `valid`, or `invalid` — a
quick gate for whether the structured review sidecar can be trusted.
`dirty_records` lists uncommitted files an agent left behind; these are also
surfaced in the PR body.

In **two-commit mode** (`mode: "commit_pair"`), `commit_pair` is populated:

```jsonc
"commit_pair": {
  "slug": "compare-...",
  "entries": [
    {
      "label": "codex",
      "original_arg": "abc123",
      "sha": "abc123...",
      "short_sha": "abc123",
      "branch": "codex/feature",
      "source": "repository_url",
      "refs": ["refs/heads/codex/feature"]
    }
    // ...second entry
  ]
}
```

### `selected_implementation`

`null` until you accept a branch in the wiki. Once set:

```jsonc
"selected_implementation": {
  "accepted_agent": "codex",
  "comparison_fixer": "claude",
  "branch": "codex/my-plan",
  "repo_dir": "/abs/codex-my-plan"
}
```

### `phase_timings`

One entry per orchestration phase, in execution order. Use this for dashboards
and for spotting where a run stalled or failed.

```jsonc
"phase_timings": [
  { "name": "resolve plan", "duration_seconds": 0.004, "status": "succeeded", "error": null, "log_path": null },
  { "name": "preflight",    "duration_seconds": 3.210, "status": "succeeded", "error": null, "log_path": null },
  { "name": "poll acceptance", "duration_seconds": 240.0, "status": "succeeded", "error": null, "log_path": null }
  // ...
]
```

`status` is `succeeded` or `failed`; a failed phase carries an `error` string and,
when available, a `log_path` to the failing command's log. Phase names you will
see include: `resolve plan`, `load config`, `build context`, `preflight`,
`prepare or resume implementation clones`, `prepare comparison`,
`poll acceptance`, `run comparison implementation`, `run review phases`, and
`finalize pull request`.

### `phase_warnings`

Non-fatal degradations — the reason a run can end as `succeeded_with_warnings`.

```jsonc
"phase_warnings": [
  { "phase": "notify open pr", "status": "failed", "message": "...", "error": "...", "log_name": null },
  { "phase": "comparison tests", "status": "skipped", "message": "...", "error": null, "log_name": "..." }
]
```

`status` is `failed` or `skipped`. These do not change the exit code; gate on this
array if your pipeline must treat warnings as actionable.

### `preflight` and `command_logs`

- `preflight` records the resolved CLI paths (`cli_checks`), the `gh auth` log
  path, per-agent auth checks, the wiki access check, and write-permission probes.
  Useful for debugging "works locally, fails in CI" auth problems.
- `command_logs` lists every external command run, with its `label`, full
  `command` array, `cwd`, and `log_path` under `logs/`. This is your index into
  the per-command logs.

### Consuming the report

```bash
# Fail a pipeline step on a non-clean status.
status=$(jq -r '.status' logs/run.json)
[ "$status" = "succeeded" ] || echo "::warning::diamond-dev: $status"

# Surface the PR that was opened.
jq -r '.context.artifacts.pr_url // "no PR"' logs/run.json

# List any phase that failed.
jq -r '.phase_timings[] | select(.status=="failed") | "\(.name): \(.error)"' logs/run.json

# Treat best-effort warnings as actionable.
jq -e '.phase_warnings | length == 0' logs/run.json >/dev/null \
  || echo "::warning::diamond-dev finished with warnings"
```

## Notification webhooks

`diamond-dev` fires best-effort HTTP GET requests at phase boundaries so an
external system can react without parsing logs. Configure them under
`[notifications]`:

```toml
[notifications]
initial_implementation_url     = "https://example.test/initial"
comparison_url                 = "https://example.test/comparison"
comparison_implementation_url  = "https://example.test/followup"
review_input_needed_url        = "https://example.test/review"
open_pr_url                    = "https://example.test/open-pr"
```

| Config key | Fires when |
| ---------- | ---------- |
| `initial_implementation_url` | the initial implementation phase needs attention / completes |
| `comparison_url` | the comparison page is ready (this is the one that means **"go check the wiki and accept a branch"**) |
| `comparison_implementation_url` | the comparison follow-up phase runs |
| `review_input_needed_url` | review reaches a state needing input |
| `open_pr_url` | the PR is opened |

Semantics (from [`notify.py`](../diamond_dev/notify.py)):

- **GET only, best-effort.** A 10-second timeout; any failure is logged at
  `warning` and the workflow continues. A failed notification can produce a
  `phase_warnings` entry but never fails the run.
- **`http`/`https` only.** Other schemes (and malformed URLs) are skipped with a
  warning — you cannot point these at arbitrary commands.
- **Only phases completed by the current process notify.** On auto-resume, phases
  finished by an earlier process do not re-fire.

These pair naturally with services like ntfy, a Slack/Discord incoming webhook
proxy, or a small endpoint that records run progress. The most useful one to wire
first is `comparison_url`, since the workflow pauses there for your human
acceptance.

## Driving acceptance unattended

The workflow pauses after publishing the comparison page and polls the wiki for
your checkbox edit. To automate the resume:

1. Subscribe to `comparison_url` to learn the comparison page is live.
2. Edit `<slug>-comparison.md` in the wiki repo to check exactly one box, e.g.
   `- [x] Accept: codex`, and push. (See the README's
   *Acceptance & Review Judgments* for the exact marker format.)
3. The running process polls every `acceptance.poll_interval_seconds` (default
   120s) up to `acceptance.max_wait_seconds` (default 4620s) and resumes
   automatically. If the process already exited (e.g. timed out, or you sent
   Ctrl-C → exit 130), simply re-run the same command — it
   [auto-resumes](../README.md#auto-resume) and picks up your choice.

Tune `[acceptance]` for your automation cadence; lower `poll_interval_seconds`
resumes faster at the cost of more wiki fetches.
