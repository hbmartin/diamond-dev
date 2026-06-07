# Acceptance & review artifacts

This guide is the format reference for the artifacts `diamond-dev` produces around
the human-acceptance step: the deterministic comparison bundle the judge reads, the
acceptance checkbox you edit, and the structured review-judgment sidecar. The
generators live in [`comparison_bundle.py`](../diamond_dev/comparison_bundle.py)
and [`review_judgments.py`](../diamond_dev/review_judgments.py); artifact filenames
come from `PlanContext` in [`workflow.py`](../diamond_dev/workflow.py). For
overriding the prompts that drive these phases, see
[Custom prompts](custom-prompts.md).

All artifacts are written in the invocation directory and pushed to the GitHub
Gollum wiki, named from the run slug:

| Artifact | Filename |
| --- | --- |
| Comparison bundle | `<slug>-comparison-bundle.md` |
| Comparison page (local) | `comparison.md` |
| Comparison page (wiki) | `<slug>-comparison.md` |
| Review page | `<slug>-review.md` |
| Review judgment sidecar | `<slug>-review-judgments.json` |

## The comparison bundle

Before comparison judgment, `diamond-dev` writes `<slug>-comparison-bundle.md`, a
deterministic input the judge must read before scoring branches. It opens with run
identity (plan name, or in two-commit mode the slug, args, labels, SHAs, and commit
subjects), the base branch, and the diff byte budgets. Then one section per branch
(outer fence widened to four backticks so the nested diff fence is literal):

````markdown
# Diamond Dev comparison bundle

- Plan: my-plan.md
- Base branch: main
- Diff byte budget: 200000
- Per-file diff byte cap: 40000

## codex

- Branch: codex/my-plan
- Repository: /abs/codex-my-plan
- Head SHA: <sha>
- Ahead/behind base: ahead=3, behind=0
- Changed files: 4
- Change stats: added=1, modified=3

### Changed file list

- M: diamond_dev/config.py
...

### Tests

- tests: not_run

### Capped diffs

#### diamond_dev/config.py

```diff
...
```
````

Key behaviors:

- **Change stats** count by status: `added`, `modified`, `deleted`, `renamed`,
  `copied`, `other`.
- **Capping is byte-budgeted.** The changed-file list, per-file diffs, and the
  total diff are clipped to `[comparison].max_file_diff_bytes` and
  `max_total_diff_bytes`. Anything dropped is listed under explicit
  `Omitted changed files` / `### Omitted diff files` headings, so omissions are
  visible rather than silent.
- **Tests.** With no `[comparison].test_commands`, each branch records
  `tests: not_run`. When set, each command runs with `sh -lc` in the branch's
  clone and the bundle records the command, `passed`/`failed` status with exit
  code, the log path, and clipped output (capped by `max_test_output_bytes`). A
  nonzero exit is recorded; the workflow still continues to judgment. Test
  commands that leave uncommitted files produce dirty-file records.

These byte budgets are configurable — see
[`[comparison]`](configuration.md#comparison--comparison-bundle).

## The acceptance checkbox

The comparison judge must write `comparison.md`. `diamond-dev` then appends the
default acceptance line and pushes the file to the wiki as `<slug>-comparison.md`:

```markdown
- [ ] Accept: (codex/claude)
```

The workflow accepts only one of these edited values:

```markdown
- [x] Accept: codex
- [x] Accept: claude
```

With custom implementers, the checkbox and accepted values use the configured
implementer names, e.g. `- [ ] Accept: (codex/claude/aider)`.

Malformed acceptance markers fail immediately. The command checks once immediately,
then polls every `[acceptance].poll_interval_seconds` until
`[acceptance].max_wait_seconds` has elapsed (see
[`[acceptance]`](configuration.md#acceptance--wiki-polling)). To drive acceptance
from automation, see
[Automation & CI integration](automation-and-ci.md#driving-acceptance-unattended).

## The review-judgment sidecar

Review judgment creates a machine-readable sidecar `<slug>-review-judgments.json`.
It is parsed and **strictly validated** by
[`review_judgments.py`](../diamond_dev/review_judgments.py):

```json
{
  "schema_version": 1,
  "review_file": "my-plan-review.md",
  "review_provider": "coderabbit",
  "review_judge": "codex",
  "findings": [
    {
      "id": "finding-1",
      "decision": "fix",
      "confidence": 0.9,
      "rationale": "Off-by-one in the loop bound."
    }
  ]
}
```

Validation rules (a violation marks the sidecar `invalid`):

- `schema_version` **must equal `1`**.
- `review_file`, `review_provider`, and `review_judge` are non-empty strings.
- `findings` is an array; each finding needs:
  - `id` — non-empty string
  - `decision` — one of `fix`, `decline`, `needs_input`
  - `confidence` — a number in `0..1` (a boolean is rejected)
  - `rationale` — non-empty string

### Rendering and the PR body

A valid sidecar is rendered into a deterministic `Structured review judgments`
section in `<slug>-review.md`, between HTML comment markers
(`<!-- diamond-dev structured-review-judgments:start -->` … `:end -->`), as a table
of `ID | Decision | Confidence | Rationale` plus compact decision counts. The PR
body includes only those decision counts (`fix`/`decline`/`needs_input`) and any
`needs_input` IDs.

A missing or malformed sidecar is logged and the review fixer falls back to legacy
markdown judgments — the run does not fail, but you lose the structured PR summary.
On auto-resume, a valid local sidecar and a valid wiki sidecar must match after
canonical JSON parsing (see [Repositories & auto-resume](repositories-and-resume.md)).
For how the judge and fixer prompts must produce and consume this sidecar, see
[Custom prompts](custom-prompts.md#the-review-judgment-sidecar-contract).
