# Troubleshooting

A deeper companion to the README's FAQ. Each section describes a failure you may
hit, why it happens, and how to recover. The run report (`logs/run.json`) and the
per-command logs are your primary evidence — start there before anything else (see
[Locating logs](#locating-logs-for-a-failure)). Read the main
[README](../README.md) first.

## Preflight failures

Preflight runs before any clone or agent and on demand via `diamond-dev doctor`. It
is intentionally strict so failures surface before long-running work. The checks
live in [`preflight.py`](../diamond_dev/preflight.py).

- **Missing required command.** `Missing required external CLIs on PATH: …` — the
  named CLI is not installed. Install it, or remove the agent that needs it from
  `[workflow]`. Only the CLIs for configured adapters are checked (plus `git` and
  `gh`). See [Agents & custom adapters](agents.md).
- **`gh auth status` fails.** Authenticate the GitHub CLI with `gh auth login` (or
  set `GH_TOKEN`).
- **Per-agent auth fails.** Each agent is probed separately (`codex login status`,
  `claude auth status --text`, a headless Gemini prompt, `coderabbit auth status
  --agent`). Log in to the failing CLI; the log path is in the run report under
  `preflight`.
- **Wiki access fails.** Preflight runs `git ls-remote` and a `git push --dry-run`
  to a throwaway branch on the wiki remote. A failure means the wiki repo is
  missing or your credentials can't push to it. Confirm the wiki exists (create the
  first wiki page once via the GitHub UI) and that your auth can write to it.
- **Write-permission failure.** Preflight writes and deletes a temp file in the
  workspace, `logs/`, and the wiki directory. `Doctor cannot write to … directory`
  means a permissions or read-only-filesystem problem at that path.

## Plan drift

`"Plan drift detected …"` means the source plan was edited after a run started, so
it no longer matches the copy stored in the wiki or an implementation clone. The
plan is **immutable for resume**: `diamond-dev` compares normalized markdown and
refuses to continue when they differ (see
[Repositories & auto-resume](repositories-and-resume.md#plan-immutability-and-drift)).

To recover, pick one:

- **Start a new plan.** Rename the plan file so it gets a fresh slug, and run that.
  This is the cleanest path and leaves the prior run's artifacts intact.
- **Reset and rerun.** Delete the generated implementation clones and the wiki
  artifacts for this slug (`<slug>-comparison*.md`, `<slug>-review*`), then rerun
  the original plan from a clean state.

Do not hand-edit the stored plan copies to match — treat the plan as append-only
across a run's lifetime.

## Divergent or stuck branches

`"Cannot auto-resume divergent workflow branch: …"` means a remote workflow branch
and your local branch have diverged. `diamond-dev` never rebases or merges. Resolve
the divergence yourself (or delete the remote branch if it is safe to discard), or
start a new slug. The branch resume rules are listed in
[Repositories & auto-resume](repositories-and-resume.md#branch-resume-rules).

## Two-commit resolution errors

In [two-commit mode](two-commit-mode.md):

- `"Commit comparison requires two distinct commits"` — both arguments resolved to
  the same SHA. Pass two different commits.
- `"Commit not reachable from configured remote and invocation repository origin
  does not match repository_url"` — the commit isn't on the configured remote, and
  the local fallback was refused because your current repo's `origin` doesn't match
  `repository_url`. Push the commit to the configured remote, or run from a clone
  whose `origin` matches.

## The run exited while waiting for acceptance

Expected. Edit the acceptance checkbox in the wiki (`- [x] Accept: <agent>`), then
rerun the same command — it [auto-resumes](repositories-and-resume.md) and picks up
your choice. Pressing Ctrl-C during polling exits with code `130` after writing the
run report. To automate this, see
[Automation & CI integration](automation-and-ci.md#driving-acceptance-unattended).

## `succeeded_with_warnings`

One or more best-effort phases (a notification, an optional comparison test
command) were skipped or failed without blocking the run. The exit code is still
`0`; the specifics are in `logs/run.json` under `phase_warnings` and in the PR body.
Gate CI on `phase_warnings` rather than the exit code if you need to react. See
[Automation & CI integration](automation-and-ci.md#exit-codes).

## A PR already exists for the selected branch

Auto-resume fails before PR creation if any PR — open, closed, or merged — already
exists for the accepted branch. Resolve, reopen, or rename the branch, or start a
new plan slug.

## Dirty / uncommitted files

Agents commit their own work; `diamond-dev` only pushes committed changes. Files an
agent or a comparison test command leaves uncommitted are recorded as
`dirty_records` (surfaced in `logs/run.json` and the PR body) but are **not**
cleaned automatically. If you see unexpected dirty files, inspect that clone — they
usually point at a test command writing artifacts or an agent that failed to commit.

## Locating logs for a failure

Every external command streams to its own file under `logs/`, indexed by the run
report's `command_logs` array (`label`, `command`, `cwd`, `log_path`). A failed
phase also records a `log_path` directly. Start from `logs/run.json`:

```bash
# What failed and why.
jq -r '.phase_timings[] | select(.status=="failed") | "\(.name): \(.error)"' logs/run.json

# The exact per-command log for a failure.
jq -r '.phase_timings[] | select(.status=="failed") | .log_path' logs/run.json
```

For log configuration, the JSONL schema, and live tailing, see
[Observability](observability.md).
