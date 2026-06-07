# Agents & custom adapters

`diamond-dev` drives external CLIs — coding agents and a review provider — through a
small adapter layer. This guide covers the built-in adapters, the capabilities that
decide which agent can fill which role, the exact commands each one runs, how to
define custom agent names, and how authentication is probed. The registry lives in
[`agents.py`](../diamond_dev/agents.py) and the command builders in
[`commands.py`](../diamond_dev/commands.py). For the `[workflow]` and `[agents.*]`
keys, see [Configuration](configuration.md).

## The adapter model

An **agent name** is a workflow-local label (the keys under `[agents.*]` and the
values in `[workflow]`). Each name resolves to one **adapter** — a built-in CLI
integration. Built-in names map to their own adapter implicitly; any other name
must set `adapter` to a built-in (see [custom agents](#custom-agent-names)).

Each adapter declares a frozenset of **capabilities**. A role assignment is only
valid if the backing adapter has the matching capability, and this is enforced at
config load by `_validate_agent_configuration` in
[`config.py`](../diamond_dev/config.py) — a mismatch fails fast with a clear error.

## Built-in adapters and capabilities

| Adapter | Executable | Capabilities |
| --- | --- | --- |
| `codex` | `codex` | `implementation`, `comparison_fixer`, `review_judge`, `review_fixer` |
| `claude` | `claude` | `implementation`, `comparison_fixer`, `review_judge`, `review_fixer`, `final_reviewer` |
| `gemini` | `gemini` | `comparison_judge` |
| `coderabbit` | `coderabbit` | `review_provider` |

Consequences worth noting: only `claude` can be the `final_reviewer`; only `gemini`
can be the `comparison_judge`; only `coderabbit` can be the `review_provider`; and
`gemini`/`coderabbit` cannot be implementers. `codex` and `claude` are the
interchangeable code-writers.

## Roles → capabilities → `[workflow]` keys

| `[workflow]` key | Required capability | Default agent |
| --- | --- | --- |
| `implementers` (≥2) | `implementation` | `codex`, `claude` |
| `comparison_judge` | `comparison_judge` | `gemini` |
| `comparison_fixer` (optional) | `comparison_fixer` | first non-selected implementer |
| `review_provider` | `review_provider` | `coderabbit` |
| `review_judge` | `review_judge` | `codex` |
| `review_fixer` | `review_fixer` | `codex` |
| `final_reviewer` | `final_reviewer` | `claude` |

Only the CLIs for the adapters you actually configure are required on `PATH`;
`config.required_cli_names()` derives that set from the roles in use, plus the
always-required `git` and `gh`.

## Commands each adapter runs

The implementers, judge, and fixers are **prompt-driven**: `diamond-dev` builds a
task prompt (see [Custom prompts](custom-prompts.md)) and runs the agent
non-interactively with sandboxing and approval prompts disabled.

| Adapter | Command shape |
| --- | --- |
| `codex` (prompt) | `codex exec -C <repo> [-m <model>] --dangerously-bypass-approvals-and-sandbox <prompt>` |
| `claude` (prompt) | `claude [--model <model>] -p --permission-mode bypassPermissions --dangerously-skip-permissions <prompt>` |
| `gemini` (prompt) | `gemini [-m <model>] -p <prompt> --skip-trust -y` |
| `coderabbit` (review) | `coderabbit review --plain --base <base-branch>` |
| `claude` (final review) | `claude [--model <model>] --permission-mode bypassPermissions --dangerously-skip-permissions /review <pr-number>` |

The review provider and final reviewer are **not** prompt-overridable — they run
fixed commands. Because agents run with safety prompts disabled, only point
`diamond-dev` at repositories and plans you trust (see
[README Security](../README.md#security)).

## Custom agent names

Define extra agent names to reuse a CLI in multiple roles with different models. A
custom name must set `adapter`, and the name must match
`^[a-z0-9]+(?:-[a-z0-9]+)*$`:

```toml
[agents.claude-fixer]
adapter = "claude"
model = "opus"

[agents.codex-judge]
adapter = "codex"
model = "gpt-5"

[workflow]
review_fixer = "claude-fixer"
review_judge = "codex-judge"
```

The custom agent inherits its adapter's capabilities, so it can fill any role that
adapter supports. `[agents.<name>].model` becomes the CLI's model flag; omit it to
use the CLI's own default.

## Authentication and `doctor`

Preflight (run before every workflow, and on its own via `diamond-dev doctor`)
probes each configured agent's auth. The probe per adapter
(`_agent_auth_command` in [`preflight.py`](../diamond_dev/preflight.py)):

| Adapter | Auth probe |
| --- | --- |
| `codex` | `codex login status` |
| `claude` | `claude auth status --text` |
| `gemini` | a tiny headless prompt (`gemini [-m <model>] -p <check> --skip-trust`) — Gemini has no status command |
| `coderabbit` | `coderabbit auth status --agent` |

Preflight also verifies `gh auth status`, that required CLIs are on `PATH`, that the
workspace/logs/wiki directories are writable, and that the wiki remote accepts a
dry-run push. See [Troubleshooting](troubleshooting.md#preflight-failures) for
diagnosing failures.
