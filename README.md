# diamond-dev

`diamond-dev` orchestrates a configurable multi-agent implementation workflow
from a single markdown plan. By default it asks Codex and Claude to implement
the same plan on separate branches, asks Gemini to compare the work, waits for
an acceptance choice in the repository wiki, refines the selected branch, runs
CodeRabbit review, applies accepted fixes, and opens a GitHub PR.

## Usage

```bash
diamond-dev path/to/my-plan.md
```

The command must be run from a directory containing `.diamond-dev.toml`. It takes
a path to a `.md` plan file.

Useful flags:

- `--config PATH`: Load configuration from a specific TOML file instead of
  `.diamond-dev.toml` in the current directory. Relative paths resolve from the
  invocation directory.
- `--version`: Show the installed `diamond-dev` version.

## Configuration

`.diamond-dev.toml` requires a target repository URL:

```toml
repository_url = "git@github.com:owner/repo.git"
```

`repository_url` must be a Git remote URL in a supported URL form such as
`https://github.com/owner/repo`, `ssh://git@github.com/owner/repo.git`,
`git://host/owner/repo.git`, `file:///path/to/repo.git`, or an SCP-like form
such as `git@github.com:owner/repo.git`.

Optional top-level keys:

- `wiki_repository_url`: GitHub Gollum wiki repository URL. If omitted, GitHub
  remotes are derived as `<repo>.wiki.git`. The local wiki clone directory is
  named from the effective wiki repository URL.

Optional tables:

```toml
[notifications]
initial_implementation_url = "https://example.test/initial"
comparison_url = "https://example.test/comparison"
comparison_implementation_url = "https://example.test/followup"
review_input_needed_url = "https://example.test/review"
open_pr_url = "https://example.test/open-pr"

[prompts]
initial_implementation_file = "prompts/initial.md"
comparison_judgment_file = "prompts/compare.md"
comparison_implementation_file = "prompts/followup.md"
review_judgment_file = "prompts/review-judgment.md"
review_fix_file = "prompts/review-fixes.md"

[workflow]
implementers = ["codex", "claude"]
comparison_judge = "gemini"
# comparison_fixer is optional; omitted means the first non-selected implementer.
review_provider = "coderabbit"
review_judge = "codex"
review_fixer = "codex"
final_reviewer = "claude"

[comparison]
test_commands = []
max_total_diff_bytes = 200000
max_file_diff_bytes = 40000
max_test_output_bytes = 20000

[agents.codex]
model = "gpt-5"

[agents.claude]
model = "opus"

[agents.gemini]
model = "gemini-3"

[agents.claude-fixer]
adapter = "claude"
model = "opus"
```

Prompt file paths resolve from the config file directory. Prompt overrides
replace the built-in task instructions while keeping Diamond Dev's required
workflow context, such as artifact filenames and commit/no-push requirements.
`[prompts].gemini_comparison_file` and the legacy top-level
`gemini_comparison_prompt_file` key are still accepted as aliases for
`[prompts].comparison_judgment_file`.

Agent table names are workflow-local agent names. Built-in names such as
`codex`, `claude`, `gemini`, and `coderabbit` implicitly use matching adapters.
Additional agent names must set `adapter` to one of those built-ins, which lets a
workflow use the same CLI in multiple roles with different models.

The `[comparison]` table controls the deterministic comparison bundle generated
before the comparison judge runs. `test_commands` defaults to empty, which
records `tests: not_run` for each implementation branch. When set, commands run
with `sh -lc` in each implementation clone; nonzero exits are recorded in the
bundle and the workflow still continues to comparison judgment. Test commands
are trusted project-specific commands. If they leave uncommitted files,
`diamond-dev` records those dirty files but does not clean them.

Notification URLs are best-effort GET requests. Failures are logged but do not
stop the workflow.

Legacy top-level notification keys are still accepted:
`notify_initial_implementation_url`, `notify_comparison_url`,
`notify_comparison_implementation_url`, `notify_review_input_needed_url`, and
`notify_open_pr_url`. A config fails if a legacy key and its table replacement
are both present.

The previous `notes_repository_url` key has been removed. Use
`wiki_repository_url`; configs that still contain the old key fail at startup.

## Prompts

Built-in prompt sources:

- [Initial implementation prompt](diamond_dev/commands.py): asks each configured
  implementer to implement the plan and commit without pushing.
- [Comparison follow-up prompt](diamond_dev/commands.py): asks the configured
  comparison fixer to apply requested follow-up changes from the comparison.
- [Review judgment prompt](diamond_dev/commands.py): asks the configured review
  judge to classify review findings and write `<slug>-review-judgments.json`.
- [Review fix prompt](diamond_dev/commands.py): asks the configured review fixer
  to implement accepted review fixes, preferring the JSON sidecar when valid and
  falling back to legacy markdown judgments when it is absent or malformed.
- [Comparison judgment prompt wrapper](diamond_dev/commands.py): adds required
  branch, repository, and output-file context to the comparison judge prompt.
- [Fallback comparison judgment prompt](diamond_dev/commands.py): used when
  `[prompts].comparison_judgment_file` is unset or empty.

Each optional prompt file can replace its matching fallback instructions while
keeping the required context wrapper.

## Generated Repositories

For a plan named `My Plan.md`, the command uses the slug `my-plan`. With the
default implementers it creates:

- `codex-my-plan` on branch `codex/my-plan`
- `claude-my-plan` on branch `claude/my-plan`
- `<repo-name>.wiki` for the GitHub Gollum wiki

For custom implementers, generated implementation clones and branches use the
same pattern: `<agent-name>-my-plan` on branch `<agent-name>/my-plan`.

The wiki clone is reused if present and synchronized with fast-forward-only
pulls. On a fresh run, `diamond-dev` clones the implementation repository once,
makes a preserving local copy for the second agent, then checks out each
workflow branch. Implementation clone directories are required on an auto-resume
run.

After each implementation clone is prepared on its workflow branch, `diamond-dev`
checks that clone root for package lockfiles. If `uv.lock` exists, it runs
`uv sync --locked`; if `pnpm-lock.yaml` exists, it runs
`pnpm install --frozen-lockfile`. Repositories with both lockfiles run both
commands in that order in each clone. Repositories with neither lockfile skip
package install. These install commands can execute dependency lifecycle scripts
from the target repository, so run `diamond-dev` only against repositories you
trust.

## Auto-Resume

`diamond-dev` does not write checkpoint files. Rerunning the same plan
automatically resumes from existing local implementation clones, workflow branch
state, wiki artifacts, and PR state.

The source plan file is immutable for resume. Editing the plan after a run starts
causes plan drift failure when the wiki or implementation-clone copy no longer
matches the source. Use a new plan filename/slug, or reset the generated
repositories and wiki artifacts, to start a different plan.

Auto-resume requires every configured implementer clone to exist as a Git
repository with the configured `repository_url` as `origin`. If only some clones
are missing, or workflow branches exist on origin while local clones are
missing, the run fails clearly.

Branch resume rules:

- Remote workflow branches must match the local branch exactly; divergence fails.
- A zero-commit branch counts as complete only when the matching remote branch
  exists and matches local.
- Local commits with no remote branch are pushed instead of rerunning that agent.
- If only one initial agent branch is incomplete, only that agent is rerun.
- The default branch may have advanced; `diamond-dev` does not rebase or merge.

Artifact resume rules:

- If the wiki comparison page exists, it overwrites local `comparison.md`.
- If only local `comparison.md` exists, it is promoted to the wiki with the
  acceptance checkbox added when missing.
- The comparison bundle is reused or promoted alongside the comparison page when
  present.
- If only a local review file exists, it is promoted to the wiki.
- If local and wiki review files both exist and differ, the run fails.
- A valid local review judgment sidecar and valid wiki sidecar must match after
  canonical JSON parsing. Missing or malformed sidecars are logged and ignored.
- Existing review files do not skip the configured review fixer; fixes rerun
  when resume reaches the review phase.
- Existing PRs for the selected branch, open, closed, or merged, fail before PR
  creation.
- Notifications are sent only for phases completed by the current process.

## Acceptance

Before comparison judgment, `diamond-dev` writes
`<slug>-comparison-bundle.md` in the invocation directory and wiki. The bundle
includes branch metadata, changed-file stats, capped file lists and diffs,
configured comparison test results, command log paths, and explicit omitted-file
lists. The configured comparison judge must read that bundle and write
`comparison.md` in the invocation directory. The command then appends this
default line and pushes the file to the GitHub Gollum wiki as
`<slug>-comparison.md`:

```markdown
- [ ] Accept: (codex/claude)
```

The workflow accepts only one of these edited values:

```markdown
- [x] Accept: codex
- [x] Accept: claude
```

With custom implementers, the checkbox and accepted values use the configured
implementer names, for example `- [ ] Accept: (codex/claude/aider)`.

Malformed acceptance markers fail immediately. The command checks once
immediately, then waits 2 minutes, then retries with waits of 3 through 12
minutes.

Review judgment creates a machine-readable sidecar named
`<slug>-review-judgments.json` with `schema_version`, `review_file`,
`review_provider`, `review_judge`, and per-finding `id`, `decision`,
`confidence`, and `rationale`. Valid sidecars are rendered into a deterministic
`Structured review judgments` section in `<slug>-review.md`; the PR body only
includes compact decision counts and any `needs_input` IDs.

## External CLIs

The workflow expects `git`, `gh`, and the CLIs for configured agent adapters to
be installed and authenticated where needed. With the default workflow that
means:

- `git`
- `codex`
- `claude`
- `gemini`
- `coderabbit`
- `gh`

The following commands are required only when the cloned target repository has
matching root lockfiles:

- `uv` for `uv.lock`
- `pnpm` for `pnpm-lock.yaml`

Before cloning or launching agents, `diamond-dev` runs a fast preflight that
checks configured commands are available on `PATH` and verifies `gh auth status`.

Agent subprocess logs are written under `logs/` and streamed through Loguru.
Agents commit their changes; `diamond-dev` pushes committed work. If uncommitted
files remain, they are logged and included in the final PR body.

## Logging

diamond-dev uses Loguru for console, readable text file, and JSONL file logging.
Logs are written to stderr, `logs/diamond-dev.log`, and
`logs/diamond-dev.jsonl` by default.

Each run also writes `logs/run-report.json`, a structured summary containing the
run status, chosen agent, branches, PR URL, dirty-file records, per-phase
timings, non-fatal phase warnings, preflight details, and per-step command log
paths. The report includes comparison bundle and review judgment sidecar paths,
plus the sidecar parse status. Runs that finish after skipped or failed
best-effort phases report `succeeded_with_warnings` and include those warnings
in the PR body.

Configure logging with environment variables:

- `DIAMOND_DEV_LOG_LEVEL`: Log level for console, text file, and JSONL output.
  Defaults to `INFO`.
- `DIAMOND_DEV_LOG_FILE`: File path for readable persistent logs. Defaults to
  `logs/diamond-dev.log`.
- `DIAMOND_DEV_JSON_LOG_FILE`: File path for serialized JSONL logs. Defaults to
  `logs/diamond-dev.jsonl`.
- `DIAMOND_DEV_LOG_DIAGNOSE`: Whether Loguru should include local variable
  values in exception tracebacks. Defaults to enabled. Disable with `0`,
  `false`, `no`, or `off` if logs may contain secrets.

File logs rotate at 10 MB, retain rotated files for 30 days, compress rotated
logs as zip files, use UTF-8 with fallback escaping, and are created with owner
read/write permissions. Exception logs include extended tracebacks. When
OpenTelemetry is installed, log records include the active trace ID, span ID,
sampled flag, and service name; otherwise those fields are present with default
zero or empty values.

## License

`diamond-dev` is licensed under the Apache License, Version 2.0. See
[LICENSE](LICENSE) for details.
