# diamond-dev

`diamond-dev` orchestrates a multi-agent implementation workflow from a single
markdown plan. It clones a target repository twice, asks Codex and Claude to
implement the same plan on separate branches, asks Gemini to compare the work,
waits for an acceptance choice in the repository wiki, refines the selected
branch, runs CodeRabbit review, applies accepted fixes, and opens a GitHub PR.

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

Optional keys:

- `notes_repository_url`: Gollum wiki repository URL. If omitted, GitHub remotes
  are derived as `<repo>.wiki.git`.
- `gemini_comparison_prompt_file`: Prompt file for Gemini branch comparison.
  Relative paths resolve from the config file directory.
- `notify_initial_implementation_url`
- `notify_comparison_url`
- `notify_comparison_implementation_url`
- `notify_review_input_needed_url`
- `notify_open_pr_url`

Notification URLs are best-effort GET requests. Failures are logged but do not
stop the workflow.

## Prompts

Built-in prompt sources:

- [Initial implementation prompt](diamond_dev/commands.py#L88-L93): asks Codex
  and Claude to implement the plan and commit without pushing.
- [Comparison follow-up prompt](diamond_dev/commands.py#L96-L102): asks the
  opposite agent to apply requested follow-up changes from the comparison.
- [Review judgment prompt](diamond_dev/commands.py#L105-L113): asks Codex to
  classify CodeRabbit review findings.
- [Review fix prompt](diamond_dev/commands.py#L116-L123): asks Codex to
  implement accepted review fixes.
- [Gemini comparison prompt wrapper](diamond_dev/commands.py#L126-L141): adds
  required branch, repository, and output-file context to the Gemini prompt.
- [Fallback Gemini comparison prompt](diamond_dev/commands.py#L144-L150): used
  when `gemini_comparison_prompt_file` is unset or empty.

The optional [Gemini comparison prompt file](diamond_dev/config.py#L85-L96) can
replace the fallback comparison instructions while keeping the required context
wrapper.

## Generated Repositories

For a plan named `My Plan.md`, the command uses the slug `my-plan` and creates:

- `codex-my-plan` on branch `codex/my-plan`
- `claude-my-plan` on branch `claude/my-plan`
- `<repo-name>.wiki` for notes

The implementation clone directories must not already exist. The notes wiki
clone is reused if present and synchronized with fast-forward-only pulls.

After each implementation clone is prepared on its workflow branch,
`diamond-dev` checks the clone root for package lockfiles. If `uv.lock` exists,
it runs `uv sync --locked`; if `pnpm-lock.yaml` exists, it runs
`pnpm install --frozen-lockfile`. Repositories with both lockfiles run both
commands in that order. Repositories with neither lockfile skip package install.

## Acceptance

Gemini must write `comparison.md` in the invocation directory. The command then
appends this exact line and pushes the file to the notes wiki as
`<slug>-comparison.md`:

```markdown
- [ ] Accept: (codex/claude)
```

The workflow accepts only one of these edited values:

```markdown
- [x] Accept: codex
- [x] Accept: claude
```

Malformed acceptance markers fail immediately. If no valid acceptance is found,
the command waits 2 minutes, then retries with waits of 3 through 12 minutes.

## External CLIs

The workflow expects these commands to be installed and authenticated where
needed:

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
checks these commands are available on `PATH` and verifies `gh auth status`.

Agent subprocess logs are written under `logs/` and streamed through Loguru.
Agents commit their changes; `diamond-dev` pushes committed work. If uncommitted
files remain, they are logged and included in the final PR body.

## Logging

diamond-dev uses Loguru for console, readable text file, and JSONL file logging.
Logs are written to stderr, `logs/diamond-dev.log`, and
`logs/diamond-dev.jsonl` by default.

Each run also writes `logs/run-report.json`, a structured summary containing the
run status, chosen agent, branches, PR URL, dirty-file records, per-phase
timings, preflight details, and per-step command log paths.

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
