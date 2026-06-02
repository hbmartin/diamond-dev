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
exactly one argument: a path to a `.md` plan file.

## Configuration

`.diamond-dev.toml` requires a target repository URL:

```toml
repository_url = "git@github.com:owner/repo.git"
```

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

## Generated Repositories

For a plan named `My Plan.md`, the command uses the slug `my-plan` and creates:

- `codex-my-plan` on branch `codex/my-plan`
- `claude-my-plan` on branch `claude/my-plan`
- `<repo-name>.wiki` for notes

The implementation clone directories must not already exist. The notes wiki
clone is reused if present and synchronized with fast-forward-only pulls.

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

Agent subprocess logs are written under `logs/` and streamed through Loguru.
Agents commit their changes; `diamond-dev` pushes committed work. If uncommitted
files remain, they are logged and included in the final PR body.

## Logging

diamond-dev uses Loguru for console and file logging. Logs are written to stderr
and to `logs/diamond-dev.log` by default.

Configure logging with environment variables:

- `DIAMOND_DEV_LOG_LEVEL`: Log level for both console and file output. Defaults to
  `INFO`.
- `DIAMOND_DEV_LOG_FILE`: File path for persistent logs. Defaults to
  `logs/diamond-dev.log`.

File logs rotate at 10 MB, retain rotated files for 30 days, and compress rotated
logs as zip files.
