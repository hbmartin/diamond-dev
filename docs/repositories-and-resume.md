# Repositories & auto-resume

`diamond-dev` generates a predictable set of local clones, branches, and wiki
artifacts, and it can resume an interrupted run by inspecting that state instead of
checkpoint files. This guide documents the naming conventions and the full resume
rules. The naming helpers live in [`naming.py`](../diamond_dev/naming.py) and
[`workflow.py`](../diamond_dev/workflow.py); resume behavior is enforced across the
orchestrator and [`git_ops.py`](../diamond_dev/git_ops.py). Read the main
[README](../README.md) first.

## Generated repositories and branches

For a plan named `My Plan.md`, the command uses the slug `my-plan`. With the
default implementers it creates:

- `codex-my-plan` on branch `codex/my-plan`
- `claude-my-plan` on branch `claude/my-plan`
- `<repo-name>.wiki` for the GitHub Gollum wiki

For custom implementers, clones and branches follow the same pattern:
`<agent-name>-my-plan` on branch `<agent-name>/my-plan`. Two-commit mode uses a
different scheme — see [Two-commit mode](two-commit-mode.md).

On a fresh run, `diamond-dev` clones the implementation repository once, makes a
preserving local copy for the second agent, then checks out each workflow branch.
The wiki clone is reused if present and synchronized with **fast-forward-only**
pulls.

## Lockfile install

After each implementation clone is prepared on its workflow branch, `diamond-dev`
checks that clone root for package lockfiles:

- `uv.lock` → `uv sync --locked`
- `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`

Repositories with both lockfiles run both commands in that order in each clone;
repositories with neither skip package install. These install commands can execute
dependency lifecycle scripts from the target repository, so run `diamond-dev` only
against repositories you trust (see [README Security](../README.md#security)).

## Auto-resume model

`diamond-dev` does not write checkpoint files. Rerunning the same plan
automatically resumes from existing local implementation clones, workflow branch
state, wiki artifacts, and PR state.

Auto-resume requires every configured implementer clone to exist as a Git
repository with the configured `repository_url` as `origin`. If only some clones
are missing, or workflow branches exist on origin while local clones are missing,
the run fails clearly. Implementation clone directories are required on an
auto-resume run.

## Plan immutability and drift

The source plan file is immutable for resume. Editing the plan after a run starts
causes a **plan drift** failure when the wiki or implementation-clone copy no
longer matches the source (the comparison is done on normalized markdown by
`_ensure_agent_plan_copy`). To start a different plan, use a new plan
filename/slug, or reset the generated repositories and wiki artifacts. See
[Troubleshooting](troubleshooting.md#plan-drift) for recovery steps.

## Branch resume rules

- Remote workflow branches must match the local branch exactly; divergence fails.
- A zero-commit branch counts as complete only when the matching remote branch
  exists and matches local.
- Local commits with no remote branch are pushed instead of rerunning that agent.
- If only one initial agent branch is incomplete, only that agent is rerun.
- The default branch may have advanced; `diamond-dev` does not rebase or merge.

## Artifact resume rules

- If the wiki comparison page exists, it overwrites local `comparison.md`.
- If only local `comparison.md` exists, it is promoted to the wiki with the
  acceptance checkbox added when missing.
- In two-commit mode, local `comparison.md` is reused only when it contains the
  matching ordered commit-pair marker; otherwise it is regenerated.
- The comparison bundle is reused or promoted alongside the comparison page when
  present.
- If only a local review file exists, it is promoted to the wiki.
- If local and wiki review files both exist and differ, the run fails.
- A valid local review judgment sidecar and valid wiki sidecar must match after
  canonical JSON parsing. Missing or malformed sidecars are logged and ignored.
- Existing review files do not skip the configured review fixer; fixes rerun when
  resume reaches the review phase.
- Existing PRs for the selected branch — open, closed, or merged — fail before PR
  creation.
- Notifications are sent only for phases completed by the current process.
