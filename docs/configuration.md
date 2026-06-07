# Configuration

`diamond-dev` reads `.diamond-dev.toml` from the invocation directory (or the path
passed to `--config`). Only one key is required; everything else has a default, so
most projects need just a few lines. This guide is the complete reference for every
table and key. The loader and validation live in
[`config.py`](../diamond_dev/config.py); the [README](../README.md) covers the
minimal config and the rest of the getting-started path.

## Minimal configuration

The smallest working config is a single line — the target repository:

```toml
repository_url = "git@github.com:owner/repo.git"
```

`repository_url` must be a Git remote URL in a supported form such as
`https://github.com/owner/repo`, `ssh://git@github.com/owner/repo.git`,
`git://host/owner/repo.git`, `file:///path/to/repo.git`, or an SCP-like form such
as `git@github.com:owner/repo.git`. URL validity is checked by `is_git_remote_url`
in [`naming.py`](../diamond_dev/naming.py); the supported schemes are `file`,
`git`, `http`, `https`, and `ssh`, plus the SCP shorthand. With only this key,
`diamond-dev` uses the default implementers, judge, prompts, and comparison
settings described below.

## `wiki_repository_url` (optional, top-level)

GitHub Gollum wiki repository URL. If omitted, the wiki remote is derived from
`repository_url` as `<owner>/<repo>.wiki.git` (see `derive_wiki_repository_url`
in [`naming.py`](../diamond_dev/naming.py); derivation requires a `github.com`
URL). The local wiki clone directory is named from the effective wiki repository
URL.

```toml
repository_url = "git@github.com:owner/repo.git"
# wiki_repository_url = "git@github.com:owner/repo.wiki.git"
```

## Full configuration

The complete set of tables and keys is shown below. Everything outside
`repository_url` is optional — add only the tables you want to change. The
`[workflow]`, `[comparison]`, and `[acceptance]` values shown are the built-in
defaults; the `[notifications]`, `[prompts]`, and `[agents]` entries are
illustrative examples.

```toml
repository_url = "git@github.com:owner/repo.git"
# wiki_repository_url = "git@github.com:owner/repo.wiki.git"

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

[acceptance]
poll_interval_seconds = 120
max_wait_seconds = 4620

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

## `[workflow]` — roles

Each key names a configured agent that fills one workflow role. Defaults are shown
above. Validation (`_load_workflow` and `_validate_agent_configuration` in
[`config.py`](../diamond_dev/config.py)) enforces:

| Key | Default | Notes |
| --- | --- | --- |
| `implementers` | `["codex", "claude"]` | Array of **at least two** distinct agent names. Each must back an adapter with the `implementation` capability. |
| `comparison_judge` | `"gemini"` | Needs the `comparison_judge` capability. |
| `comparison_fixer` | *(unset)* | Optional. When unset, the first implementer that is not the accepted branch is used. |
| `review_provider` | `"coderabbit"` | Needs the `review_provider` capability. |
| `review_judge` | `"codex"` | Needs the `review_judge` capability. |
| `review_fixer` | `"codex"` | Needs the `review_fixer` capability. |
| `final_reviewer` | `"claude"` | Needs the `final_reviewer` capability. |

A role agent whose adapter lacks the required capability fails at config load. See
[Agents & custom adapters](agents.md) for the capability matrix and how to add
your own agent names.

## `[agents.<name>]` — adapters and models

Agent table names are workflow-local agent names. Built-in names (`codex`,
`claude`, `gemini`, `coderabbit`) implicitly use the matching adapter. Any other
name **must** set `adapter` to one of those built-ins, which lets a workflow use
the same CLI in multiple roles with different models.

| Key | Purpose |
| --- | --- |
| `adapter` | The built-in adapter that backs this agent. Required for non-built-in names; defaults to the agent name for built-ins. |
| `model` | The model flag passed to the CLI (e.g. `gpt-5`, `opus`, `gemini-3`). Omitted means the CLI's own default. |

Agent names must match `^[a-z0-9]+(?:-[a-z0-9]+)*$` — lowercase letters, numbers,
and single-hyphen separators. The example `[agents.claude-fixer]` above defines a
second Claude-backed agent you could slot into `review_fixer` or `review_judge`.

## `[comparison]` — comparison bundle

Controls the deterministic comparison bundle generated before the comparison judge
runs (see [Acceptance & review artifacts](acceptance-and-review.md) for the bundle
format). Byte limits must be positive integers.

| Key | Default | Purpose |
| --- | --- | --- |
| `test_commands` | `[]` | Commands run with `sh -lc` in each implementation clone. Empty records `tests: not_run`. Nonzero exits are recorded but do **not** stop the workflow. |
| `max_total_diff_bytes` | `200000` | Total diff byte budget across all branches in the bundle. |
| `max_file_diff_bytes` | `40000` | Per-file diff byte cap. |
| `max_test_output_bytes` | `20000` | Per-command captured test-output cap. |

Test commands are trusted project-specific commands and run with sandboxing
disabled. If they leave uncommitted files, `diamond-dev` records those dirty files
but does not clean them. See the [README Security](../README.md#security) section.

## `[acceptance]` — wiki polling

Controls how long the workflow waits for the wiki acceptance checkbox after the
immediate first check. Both keys must be positive integers.

| Key | Default | Purpose |
| --- | --- | --- |
| `poll_interval_seconds` | `120` | Fixed wait between checks. |
| `max_wait_seconds` | `4620` | Caps the total wait window. |

The defaults preserve a roughly 77-minute total polling window with a responsive
fixed cadence: one immediate wiki sync followed by 39 delayed checks. A shorter
`poll_interval_seconds` resumes faster at the cost of more remote fetch/pull load.
See [Acceptance & review artifacts](acceptance-and-review.md) for the checkbox
format and [Automation & CI integration](automation-and-ci.md) for driving
acceptance unattended.

## `[notifications]` — webhooks

Best-effort HTTP GET requests fired at phase boundaries. Failures are logged but
never stop the workflow, and only `http`/`https` URLs are honored. See
[Automation & CI integration](automation-and-ci.md#notification-webhooks) for the
firing semantics and the meaning of each key.

| Key | Fires when |
| --- | --- |
| `initial_implementation_url` | the initial implementation phase completes |
| `comparison_url` | the comparison page is ready for your acceptance |
| `comparison_implementation_url` | the comparison follow-up phase runs |
| `review_input_needed_url` | review reaches a state needing input |
| `open_pr_url` | the PR is opened |

## `[prompts]` — task overrides

Each key points to a file whose contents replace the built-in task instructions
for one phase, while `diamond-dev` always appends the non-configurable
`Required context:` block. Paths resolve relative to the config file's directory
(absolute paths are used as-is), and a missing file fails at startup. See
[Custom prompts](custom-prompts.md) for the override contract and what each phase's
prompt must accomplish.

| Key | Phase |
| --- | --- |
| `initial_implementation_file` | initial implementation |
| `comparison_judgment_file` | comparison judgment |
| `comparison_implementation_file` | comparison follow-up |
| `review_judgment_file` | review judgment |
| `review_fix_file` | review fix |

## Legacy and removed keys (migration)

`[prompts].gemini_comparison_file` and the legacy top-level
`gemini_comparison_prompt_file` key are still accepted as aliases for
`[prompts].comparison_judgment_file`. Setting both a modern key and its alias fails.

Legacy top-level notification keys are still accepted:
`notify_initial_implementation_url`, `notify_comparison_url`,
`notify_comparison_implementation_url`, `notify_review_input_needed_url`, and
`notify_open_pr_url`. A config fails if a legacy key and its table replacement are
both present.

The previous `notes_repository_url` key has been **removed**. Use
`wiki_repository_url`; configs that still contain the old key fail at startup.
