# Custom prompts

Every agent that `diamond-dev` drives with a prompt — the implementers, the
comparison judge, the comparison fixer, the review judge, and the review fixer —
runs a prompt that you can override from the `[prompts]` table in
`.diamond-dev.toml`. This guide explains exactly what your override replaces,
what `diamond-dev` always appends regardless of your override, and what each
phase's prompt must accomplish.

> The review **provider** (CodeRabbit by default) and the **final reviewer** are
> not prompt-driven in the same way and have no `[prompts]` override. CodeRabbit
> runs a fixed `coderabbit review` command, and the final reviewer runs
> `claude /review <pr-number>`. See [`agents.py`](../diamond_dev/agents.py) and
> [`commands.py`](../diamond_dev/commands.py).

## The override model: your text replaces the task, never the contract

A prompt override does **not** replace the whole prompt. `diamond-dev` builds
every prompt as two parts:

```
<your prompt text, or the built-in fallback if you set none>

Required context:
- <line the workflow injects>
- <line the workflow injects>
- ...
```

This is implemented by `_prompt_with_required_context` in
[`commands.py`](../diamond_dev/commands.py). The takeaways:

- **Your override replaces only the task description** — the first paragraph.
  If you leave a prompt unset, `diamond-dev` uses a short built-in fallback for
  that paragraph instead.
- **The `Required context:` block is always appended** and is not configurable.
  It carries the artifact filenames, branch/repo paths, and the commit/no-push
  contract the workflow depends on. You cannot remove or rewrite these lines.
- An override that is empty or whitespace-only is treated as unset, so the
  fallback is used. (`configured_prompt.strip() or fallback_prompt`.)

Because the contract lines are appended after your text, **write your override
as additional guidance, not as a replacement for the rules.** Telling the agent
to "push your branch" or "write the comparison to a different file" will conflict
with the required context and break the run.

## Configuring overrides

Add a `[prompts]` table. Paths resolve relative to the config file's directory
(absolute paths are used as-is). Each key is optional; omit a key to keep its
fallback.

```toml
[prompts]
initial_implementation_file = "prompts/initial.md"
comparison_judgment_file = "prompts/compare.md"
comparison_implementation_file = "prompts/followup.md"
review_judgment_file = "prompts/review-judgment.md"
review_fix_file = "prompts/review-fixes.md"
```

The file contents become the task paragraph verbatim. There is no templating —
the workflow appends the `Required context:` block after whatever you write.

If a configured prompt file is missing or unreadable at startup, the run fails
with a clear config error (see `read_prompt_file` in
[`config.py`](../diamond_dev/config.py)) — overrides are validated eagerly, not
silently ignored.

## The prompts, phase by phase

The builders live in [`commands.py`](../diamond_dev/commands.py). For each prompt
below: the config key, the built-in fallback (used when unset), and the
non-negotiable `Required context:` lines that are always appended.

### Initial implementation

- **Key:** `prompts.initial_implementation_file`
- **Builder:** `initial_implementation_prompt`
- **Fallback task:** "Read and implement the supplied plan."
- **Always appended:**
  - the plan filename to implement
  - "Commit your changes on the current branch."
  - "Do not push; diamond-dev will push committed work."

Run for every implementer. Each implementer gets the same prompt and works on its
own branch/clone.

### Comparison judgment

- **Key:** `prompts.comparison_judgment_file`
  (aliases: `prompts.gemini_comparison_file`, legacy top-level
  `gemini_comparison_prompt_file`)
- **Builder:** `gemini_comparison_prompt`
- **Fallback task:** compare the implementation branches against the base branch
  on correctness, completeness, maintainability, tests, and risk; recommend one
  branch as the base and describe follow-up changes for the comparison fixer.
- **Always appended:**
  - the base branch
  - the comparison-bundle filename, plus "Read the comparison bundle before
    judging branch quality."
  - one line per implementation branch (`<agent> branch: <branch> in <repo>`)
  - "Write the final comparison to `comparison.md` in the current directory."
  - "Do not modify any implementation repository."

The judge must produce `comparison.md`; `diamond-dev` appends the acceptance
checkbox and pushes it to the wiki. Do not change the output filename in your
override.

### Comparison implementation (follow-up fixer)

- **Key:** `prompts.comparison_implementation_file`
- **Builder:** `comparison_implementation_prompt`
- **Fallback task:** "Implement the requested comparison follow-up changes."
- **Always appended:**
  - the comparison filename to read
  - "Inspect the current branch first because this prompt may be rerun."
  - "Avoid duplicating work that is already applied."
  - "Commit your changes."
  - "Do not push; diamond-dev will push committed work."

The idempotency lines matter: this prompt can be re-run on auto-resume, so your
override should not assume a clean starting branch.

### Review judgment

- **Key:** `prompts.review_judgment_file`
- **Builder:** `review_judgment_prompt`
- **Fallback task:** "Evaluate each CodeRabbit review item."
- **Always appended:**
  - the review filename and the structured judgment sidecar filename
  - write valid JSON to the sidecar with `schema_version`, `review_file`,
    `review_provider`, `review_judge`, and `findings`
  - the exact `review_provider` and `review_judge` values to record
  - each finding must have `id`, `decision`, `confidence`, and `rationale`
  - allowed decisions are `fix`, `decline`, and `needs_input`
  - judge each item as (A) should fix, (B) decline fix, or (C) ambiguous /
    input needed
  - append judgments to the review file without removing existing content
  - commit both files; do not push

This is the most demanding contract because the sidecar is machine-validated (see
the next section). An override here should refine *how* findings are judged, not
change the output format.

### Review fix

- **Key:** `prompts.review_fix_file`
- **Builder:** `review_fix_prompt`
- **Fallback task:** "Implement accepted CodeRabbit review fixes."
- **Always appended:**
  - the review filename and the sidecar filename
  - if the sidecar is valid, implement every finding with decision `fix`
  - if the sidecar is missing or invalid, fall back to the legacy markdown
    judgments and implement every item judged (A) should fix
  - never implement `decline` / `needs_input` findings (or legacy (B)/(C) items)
  - inspect the current branch first because this prompt may be rerun; avoid
    duplicating applied work
  - commit your changes; do not push

## The review-judgment sidecar contract

The review judgment prompt must write `<slug>-review-judgments.json`. This file is
parsed and validated by [`review_judgments.py`](../diamond_dev/review_judgments.py);
the validation is strict, so if you customize the review-judgment prompt keep the
output exactly conformant:

- `schema_version` **must equal `1`**.
- `review_file`, `review_provider`, and `review_judge` must be non-empty strings.
- `findings` must be an array. Each finding requires:
  - `id` — non-empty string
  - `decision` — one of `fix`, `decline`, `needs_input`
  - `confidence` — a number in `0..1` (a boolean is rejected)
  - `rationale` — non-empty string

A valid sidecar is rendered into a deterministic `Structured review judgments`
section in `<slug>-review.md` (between HTML comment markers), and the PR body
shows compact decision counts plus any `needs_input` IDs. A missing or malformed
sidecar is logged and the fixer falls back to legacy markdown judgments — the run
does not fail, but you lose the structured PR summary.

On auto-resume, a valid local sidecar and a valid wiki sidecar must match after
canonical JSON parsing, or the run fails. Keep your prompt deterministic enough
that re-runs produce equivalent JSON.

## Practical guidance

- **Add, don't override the rules.** Write domain guidance ("prefer the standard
  library", "match the repo's existing test style") and let the appended
  `Required context:` carry the workflow mechanics.
- **Never rename artifacts.** `comparison.md`, the review file, and the sidecar
  filenames are fixed by the workflow. Your prompt text must not instruct the
  agent to write elsewhere.
- **Keep follow-up/fix prompts idempotent.** Both can be re-run on resume.
- **Test with a throwaway plan first.** Because agents run with sandboxing
  disabled (see the README's Security section), validate a new prompt on a
  repository and plan you trust.
